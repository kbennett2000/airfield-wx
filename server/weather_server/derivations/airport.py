"""Offline, GPS-driven location intelligence (D-LOCATION).

Resolves the nearest airport (identity, published field elevation, runways)
and the local magnetic variation from bundled, public-domain data — fully
offline, with a config ident override and a manual fallback for unmapped
strips. Nothing location-specific is hardcoded; everything flows from the
outdoor GPS (or its configured fallback) plus config.

Data sources (bundled under ../data/ourairports/):
- OurAirports airports.csv + runways.csv (public domain).
- WMM2025 magnetic model via pygeomag (ships its own WMM_2025.COF).

Pure-ish and fail-soft: missing inputs cascade to None fields; nothing
raises. The datasets are parsed once and cached (the airport is static for a
fixed station), so resolution never rescans from disk.
"""

from __future__ import annotations

import csv
import functools
import math
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import NamedTuple

from pygeomag import GeoMag

from .. import geo
from ..config import AirportConfig, AirportRunwayConfig

FT_PER_M = 3.280839895
M_PER_KM = 1000.0
MAGNETIC_MODEL = "WMM2025"
_COEFFICIENTS_FILE = "wmm/WMM_2025.COF"


# ── lightweight parsed rows (internal) ─────────────────────────────────────


class _AirportRow(NamedTuple):
    ident: str
    type: str
    name: str
    lat: float
    lon: float
    elevation_ft: float | None


class _RunwayRow(NamedTuple):
    le_ident: str | None
    he_ident: str | None
    le_heading_true_deg: float | None
    he_heading_true_deg: float | None
    length_ft: float | None
    surface: str | None


# ── public result shapes ───────────────────────────────────────────────────


@dataclass(frozen=True)
class RunwayInfo:
    le_ident: str | None
    he_ident: str | None
    le_heading_true_deg: float | None
    he_heading_true_deg: float | None
    le_heading_mag_deg: float | None
    he_heading_mag_deg: float | None
    length_ft: float | None
    surface: str | None


@dataclass(frozen=True)
class AirportInfo:
    ident: str
    name: str | None
    type: str | None
    field_elevation_ft: float | None
    distance_nm: float | None
    source: str  # "config_override" | "gps_nearest" | "manual"
    magnetic_variation_deg: float | None
    magnetic_model: str
    runways: tuple[RunwayInfo, ...]


# ── dataset loading (cached once) ──────────────────────────────────────────


def _data_dir() -> Path:
    return Path(__file__).resolve().parent.parent / "data" / "ourairports"


def _as_float(value: str | None) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except ValueError:
        return None


@functools.lru_cache(maxsize=1)
def _load_airports() -> tuple[_AirportRow, ...]:
    out: list[_AirportRow] = []
    with (_data_dir() / "airports.csv").open(newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            if row["type"] == "closed":
                continue
            lat = _as_float(row["latitude_deg"])
            lon = _as_float(row["longitude_deg"])
            if lat is None or lon is None:
                continue
            out.append(
                _AirportRow(
                    ident=row["ident"],
                    type=row["type"],
                    name=row["name"],
                    lat=lat,
                    lon=lon,
                    elevation_ft=_as_float(row["elevation_ft"]),
                )
            )
    return tuple(out)


@functools.lru_cache(maxsize=1)
def _airport_index() -> dict[str, _AirportRow]:
    return {ap.ident: ap for ap in _load_airports()}


@functools.lru_cache(maxsize=1)
def _load_runways() -> dict[str, tuple[_RunwayRow, ...]]:
    grouped: dict[str, list[_RunwayRow]] = {}
    with (_data_dir() / "runways.csv").open(newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            if row["closed"] == "1":
                continue
            grouped.setdefault(row["airport_ident"], []).append(
                _RunwayRow(
                    le_ident=row["le_ident"] or None,
                    he_ident=row["he_ident"] or None,
                    le_heading_true_deg=_as_float(row["le_heading_degT"]),
                    he_heading_true_deg=_as_float(row["he_heading_degT"]),
                    length_ft=_as_float(row["length_ft"]),
                    surface=row["surface"] or None,
                )
            )
    return {ident: tuple(rws) for ident, rws in grouped.items()}


# ── magnetic variation (WMM2025 via pygeomag) ──────────────────────────────


@functools.lru_cache(maxsize=1)
def _geomag() -> GeoMag:
    return GeoMag(coefficients_file=_COEFFICIENTS_FILE)


def magnetic_variation_deg(
    lat: float, lon: float, alt_m: float, decimal_year: float
) -> float | None:
    """WMM2025 declination at a point (degrees, +east). pygeomag expects
    altitude in kilometers; the effect on declination is negligible. Fail-soft:
    returns None on any pygeomag error rather than raising."""
    try:
        result = _geomag().calculate(
            glat=lat, glon=lon, alt=alt_m / M_PER_KM, time=decimal_year
        )
        return float(result.d)
    except Exception:
        return None


def _decimal_year(when: datetime | date) -> float:
    d = when.date() if isinstance(when, datetime) else when
    year_start = date(d.year, 1, 1)
    days_in_year = (date(d.year + 1, 1, 1) - year_start).days
    return d.year + (d - year_start).days / days_in_year


# ── runway heading resolution ──────────────────────────────────────────────


def _runway_number(ident: str | None) -> int | None:
    if not ident:
        return None
    try:
        return int(ident.rstrip("LRC"))
    except ValueError:
        return None


def _resolve_end(
    ident: str | None, true_degT: float | None, decl: float | None
) -> tuple[float | None, float | None]:
    """Return (true_deg, magnetic_deg) for one runway end. Prefer the dataset's
    true heading; else derive true from the painted (magnetic) number + decl."""
    true_deg = true_degT
    if true_deg is None:
        number = _runway_number(ident)
        if number is not None and decl is not None:
            true_deg = (number * 10 + decl) % 360.0
    if true_deg is None:
        return None, None
    mag = (true_deg - decl) % 360.0 if decl is not None else None
    return true_deg, mag


def _runway_from_dataset(row: _RunwayRow, decl: float | None) -> RunwayInfo:
    le_true, le_mag = _resolve_end(row.le_ident, row.le_heading_true_deg, decl)
    he_true, he_mag = _resolve_end(row.he_ident, row.he_heading_true_deg, decl)
    return RunwayInfo(
        le_ident=row.le_ident,
        he_ident=row.he_ident,
        le_heading_true_deg=le_true,
        he_heading_true_deg=he_true,
        le_heading_mag_deg=le_mag,
        he_heading_mag_deg=he_mag,
        length_ft=row.length_ft,
        surface=row.surface,
    )


def _runway_from_config(rc: AirportRunwayConfig, decl: float | None) -> RunwayInfo:
    """Manual runway: headings are given as MAGNETIC; derive true via decl."""
    le_ident, _, he_ident = rc.ident.partition("/")
    le_mag = rc.le_heading_magnetic_deg
    he_mag = (le_mag + 180.0) % 360.0 if le_mag is not None else None
    le_true = (le_mag + decl) % 360.0 if (le_mag is not None and decl is not None) else None
    he_true = (he_mag + decl) % 360.0 if (he_mag is not None and decl is not None) else None
    return RunwayInfo(
        le_ident=le_ident or None,
        he_ident=he_ident or None,
        le_heading_true_deg=le_true,
        he_heading_true_deg=he_true,
        le_heading_mag_deg=le_mag,
        he_heading_mag_deg=he_mag,
        length_ft=rc.length_ft,
        surface=rc.surface,
    )


# ── field elevation preference ─────────────────────────────────────────────


def _field_elevation_ft(
    published_ft: float | None, config: AirportConfig, alt_m: float | None
) -> float | None:
    """Published value beats noisy GPS vertical; then a manual override; then
    GPS altitude; else None."""
    if published_ft is not None:
        return published_ft
    if config.field_elevation_ft is not None:
        return config.field_elevation_ft
    if alt_m is not None:
        return alt_m * FT_PER_M
    return None


def _has_manual(config: AirportConfig) -> bool:
    return config.field_elevation_ft is not None or bool(config.runways)


def _nearest(lat: float, lon: float) -> _AirportRow | None:
    best: _AirportRow | None = None
    best_km = math.inf
    for ap in _load_airports():
        d = geo.haversine_km(lat, lon, ap.lat, ap.lon)
        if d < best_km:
            best_km, best = d, ap
    return best


def _from_dataset(
    rec: _AirportRow,
    source: str,
    lat: float | None,
    lon: float | None,
    alt_m: float | None,
    decimal_year: float,
    config: AirportConfig,
) -> AirportInfo:
    elev_ft = _field_elevation_ft(rec.elevation_ft, config, alt_m)
    decl_alt_m = elev_ft / FT_PER_M if elev_ft is not None else 0.0
    decl = magnetic_variation_deg(rec.lat, rec.lon, decl_alt_m, decimal_year)
    distance_nm = None
    if lat is not None and lon is not None:
        distance_nm = geo.haversine_km(lat, lon, rec.lat, rec.lon) / geo.KM_PER_NM
    runways = tuple(
        _runway_from_dataset(r, decl) for r in _load_runways().get(rec.ident, ())
    )
    return AirportInfo(
        ident=rec.ident,
        name=rec.name,
        type=rec.type,
        field_elevation_ft=elev_ft,
        distance_nm=distance_nm,
        source=source,
        magnetic_variation_deg=decl,
        magnetic_model=MAGNETIC_MODEL,
        runways=runways,
    )


def _from_manual(
    config: AirportConfig,
    lat: float | None,
    lon: float | None,
    alt_m: float | None,
    decimal_year: float,
) -> AirportInfo:
    elev_ft = _field_elevation_ft(None, config, alt_m)
    decl = None
    if lat is not None and lon is not None:
        decl_alt_m = elev_ft / FT_PER_M if elev_ft is not None else (alt_m or 0.0)
        decl = magnetic_variation_deg(lat, lon, decl_alt_m, decimal_year)
    runways = tuple(_runway_from_config(rc, decl) for rc in config.runways)
    return AirportInfo(
        ident=config.ident or "MANUAL",
        name=None,
        type=None,
        field_elevation_ft=elev_ft,
        distance_nm=None,
        source="manual",
        magnetic_variation_deg=decl,
        magnetic_model=MAGNETIC_MODEL,
        runways=runways,
    )


def resolve_airport(
    lat: float | None,
    lon: float | None,
    alt_m: float | None,
    when: datetime | date,
    config: AirportConfig,
) -> AirportInfo | None:
    """Resolve the station's airport, in priority order:

    1. config ident override (looked up in the dataset),
    2. GPS nearest (haversine over open airports),
    3. manual config (field elevation + runways for an unmapped strip),
    4. else None.
    """
    decimal_year = _decimal_year(when)

    if config.ident:
        rec = _airport_index().get(config.ident)
        if rec is not None:
            return _from_dataset(
                rec, "config_override", lat, lon, alt_m, decimal_year, config
            )
        if _has_manual(config):
            return _from_manual(config, lat, lon, alt_m, decimal_year)
        return None

    if lat is not None and lon is not None:
        rec = _nearest(lat, lon)
        if rec is not None:
            return _from_dataset(
                rec, "gps_nearest", lat, lon, alt_m, decimal_year, config
            )

    if _has_manual(config):
        return _from_manual(config, lat, lon, alt_m, decimal_year)

    return None
