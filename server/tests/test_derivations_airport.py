"""Offline GPS-driven location intelligence (D-LOCATION).

Backend engine only — no API surface. Exercises nearest-airport resolution,
config override, manual fallback, field-elevation preference, WMM2025
magnetic variation, and true<->magnetic runway heading derivation against the
bundled OurAirports data.
"""

from __future__ import annotations

from datetime import datetime

import pytest

from weather_server.config import AirportConfig, AirportRunwayConfig
from weather_server.derivations import airport

# KSEA published values (from the vendored airports.csv) — a large, stable field.
KSEA_LAT = 47.447943
KSEA_LON = -122.310276
KSEA_ELEV_FT = 433.0

WHEN = datetime(2026, 6, 1)


# ── magnetic variation ────────────────────────────────────────────────────


def test_magnetic_variation_seattle_anchor() -> None:
    # pygeomag's documented Space Needle value: 47.6205, -122.3493 @ 2025.25.
    d = airport.magnetic_variation_deg(47.6205, -122.3493, 0.0, 2025.25)
    assert d is not None and d == pytest.approx(15.07, abs=0.05)


def test_decimal_year_midyear() -> None:
    # 2026-06-01 is ~41% through the year.
    assert airport._decimal_year(datetime(2026, 6, 1)) == pytest.approx(2026.42, abs=0.01)


# ── nearest-airport resolution ─────────────────────────────────────────────


def test_resolve_nearest_match() -> None:
    # Query a point ~0.3 nm north of KSEA's reference point.
    offset_deg = (0.3 * airport.geo.KM_PER_NM) / 111.19  # nm -> km -> deg latitude
    info = airport.resolve_airport(
        KSEA_LAT + offset_deg, KSEA_LON, None, WHEN, AirportConfig()
    )
    assert info is not None
    assert info.ident == "KSEA"
    assert info.source == "gps_nearest"
    assert info.distance_nm == pytest.approx(0.3, abs=0.05)


def test_override_beats_nearest() -> None:
    # GPS in the middle of Kansas, but the ident override pins KSEA.
    info = airport.resolve_airport(
        39.0, -100.0, None, WHEN, AirportConfig(ident="KSEA")
    )
    assert info is not None
    assert info.ident == "KSEA"
    assert info.source == "config_override"


def test_field_elevation_prefers_published_over_gps() -> None:
    # Pass an obviously-wrong GPS vertical; published elevation must win.
    info = airport.resolve_airport(
        KSEA_LAT, KSEA_LON, 9999.0, WHEN, AirportConfig()
    )
    assert info is not None
    assert info.field_elevation_ft == pytest.approx(KSEA_ELEV_FT)


def test_runway_true_magnetic_relationship() -> None:
    info = airport.resolve_airport(KSEA_LAT, KSEA_LON, None, WHEN, AirportConfig())
    assert info is not None and info.runways
    decl = info.magnetic_variation_deg
    assert decl is not None
    # Find a runway end with a published true heading.
    end = next(
        r for r in info.runways if r.le_heading_true_deg is not None and r.le_ident
    )
    # The exact invariant: magnetic = true - declination (mod 360).
    expected_mag = (end.le_heading_true_deg - decl) % 360.0
    assert end.le_heading_mag_deg == pytest.approx(expected_mag, abs=1e-6)
    # Sanity: the magnetic heading lands within ~10deg of the painted number
    # (numbers round magnetic heading to the nearest 10deg).
    painted = (int(end.le_ident.rstrip("LRC")) * 10) % 360
    circular_diff = abs(((end.le_heading_mag_deg - painted + 180.0) % 360.0) - 180.0)
    assert circular_diff <= 10.0


# ── manual fallback ────────────────────────────────────────────────────────


def test_manual_fallback_builds_record() -> None:
    cfg = AirportConfig(
        ident="XX99",  # not in the dataset
        field_elevation_ft=1700.0,
        runways=(
            AirportRunwayConfig(
                ident="12/30",
                le_heading_magnetic_deg=120.0,
                length_ft=1700.0,
                surface="gravel",
            ),
        ),
    )
    info = airport.resolve_airport(39.0, -104.0, None, WHEN, cfg)
    assert info is not None
    assert info.source == "manual"
    assert info.ident == "XX99"
    assert info.field_elevation_ft == pytest.approx(1700.0)
    assert len(info.runways) == 1
    rw = info.runways[0]
    assert rw.le_ident == "12" and rw.he_ident == "30"
    assert rw.le_heading_mag_deg == pytest.approx(120.0)
    assert rw.he_heading_mag_deg == pytest.approx(300.0)
    assert rw.length_ft == pytest.approx(1700.0)
    assert rw.surface == "gravel"
    # True heading derived from magnetic + declination.
    assert rw.le_heading_true_deg is not None


# ── closed exclusion & edges ───────────────────────────────────────────────


def test_closed_airports_and_runways_excluded() -> None:
    assert all(ap.type != "closed" for ap in airport._load_airports())
    # KSEA has 3 open runways in the dataset (a 4th is flagged closed).
    ksea_runways = airport._load_runways().get("KSEA", ())
    assert len(ksea_runways) == 3


def test_no_gps_no_config_returns_none() -> None:
    assert airport.resolve_airport(None, None, None, WHEN, AirportConfig()) is None


def test_override_not_in_dataset_no_manual_returns_none() -> None:
    info = airport.resolve_airport(
        None, None, None, WHEN, AirportConfig(ident="ZZZZ")
    )
    assert info is None
