"""Pluggable external weather providers.

Three keyless-or-bring-your-own-key providers, all returning the same
normalized `Observation`:

- ``open-meteo`` (default): keyless, global, point-specific (``best_match``
  ⇒ HRRR in the US). Returns wind + cloud/UV/precip/visibility in one call.
- ``nws``: real US station observations, auto-discovers the nearest station
  from GPS (or uses a configured ``station_id``). No cloud %/UV.
- ``wunderground``: a specific PWS by ``station_id``; requires a free
  member ``api_key``. Use a real key only — never scrape the site key.

The HTTP call is injected (``http_get``) so tests pass canned JSON and the
normalizers / dispatch can be exercised without a network.

Every function fails soft: any error returns ``None`` (provider down) rather
than raising. That is what keeps the offline-first guarantee intact.
"""

from __future__ import annotations

import logging
import math
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from typing import Any

import requests

from ..config import ExternalConfig
from ..geo import haversine_km

log = logging.getLogger(__name__)

# url, headers, timeout_seconds -> parsed JSON
HttpGetJson = Callable[[str, dict[str, str] | None, float], Any]

_NWS_USER_AGENT = "(airfield-wx, weather-server)"
_METAR_USER_AGENT = "airfield-wx/0.1 (https://github.com/kbennett2000/airfield-wx)"
_KMH_TO_MS = 1.0 / 3.6
_INHG_PER_HPA = 0.0295300
_CEILING_COVERS = frozenset({"BKN", "OVC", "OVX"})  # OVX = vertical visibility / obscured
_CATEGORY_RANK = {"VFR": 0, "MVFR": 1, "IFR": 2, "LIFR": 3}


@dataclass(frozen=True)
class MetarSkyLayer:
    """One reported sky layer from a METAR (cover + base in ft AGL)."""

    cover: str
    base_ft_agl: int | None = None


@dataclass(frozen=True)
class Observation:
    """Normalized regional observation. All measurements optional; wind speed
    is stored internally in m/s and converted to display units downstream."""

    provider: str
    source: str
    station_id: str | None = None
    distance_km: float | None = None
    observed_at: datetime | None = None
    wind_speed_ms: float | None = None
    wind_gust_ms: float | None = None
    wind_direction_deg: float | None = None
    cloud_cover_pct: float | None = None
    uv_index: float | None = None
    precip_mm: float | None = None
    visibility_m: float | None = None
    confidence: str | None = None  # "normal" | "low"

    # Aviation fields — populated by the `metar` provider only (EXTERNAL).
    metar_raw: str | None = None
    sky_layers: tuple[MetarSkyLayer, ...] | None = None
    ceiling_ft_agl: int | None = None
    visibility_sm: float | None = None
    flight_category: str | None = None  # VFR | MVFR | IFR | LIFR
    altimeter_inhg: float | None = None
    altimeter_hpa: float | None = None
    temp_c: float | None = None
    dewpoint_c: float | None = None


_CARDINALS = (
    "N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE",
    "S", "SSW", "SW", "WSW", "W", "WNW", "NW", "NNW",
)


def cardinal_from_deg(deg: float | None) -> str | None:
    """16-point compass label for a bearing in degrees."""
    if deg is None:
        return None
    idx = int((deg % 360.0) / 22.5 + 0.5) % 16
    return _CARDINALS[idx]


def _default_http_get(url: str, headers: dict[str, str] | None, timeout: float) -> Any:
    resp = requests.get(url, headers=headers, timeout=timeout)
    resp.raise_for_status()
    return resp.json()


def _scrub(message: str, secret: str | None) -> str:
    """Keep an api_key out of log output. requests/urllib3 errors embed the
    full request URL (including ?apiKey=...) in their message."""
    return message.replace(secret, "***") if secret else message


# ── number / time coercion helpers ────────────────────────────────────────────


def _num(v: Any) -> float | None:
    if v is None or isinstance(v, bool):
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(f) else f


def _parse_iso(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    text = value.replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None
    return dt.replace(tzinfo=UTC) if dt.tzinfo is None else dt


# ── open-meteo ────────────────────────────────────────────────────────────────

_OPEN_METEO_URL = "https://api.open-meteo.com/v1/forecast"


def _open_meteo_url(lat: float, lon: float) -> str:
    fields = (
        "wind_speed_10m,wind_direction_10m,wind_gusts_10m,"
        "cloud_cover,uv_index,precipitation,visibility"
    )
    return (
        f"{_OPEN_METEO_URL}?latitude={lat}&longitude={lon}"
        f"&current={fields}&wind_speed_unit=ms&models=best_match&timezone=GMT"
    )


def normalize_open_meteo(payload: Any) -> Observation | None:
    if not isinstance(payload, dict):
        return None
    current = payload.get("current")
    if not isinstance(current, dict):
        return None
    return Observation(
        provider="open-meteo",
        source="open-meteo:best_match",
        observed_at=_parse_iso(current.get("time")),
        wind_speed_ms=_num(current.get("wind_speed_10m")),
        wind_gust_ms=_num(current.get("wind_gusts_10m")),
        wind_direction_deg=_num(current.get("wind_direction_10m")),
        cloud_cover_pct=_num(current.get("cloud_cover")),
        uv_index=_num(current.get("uv_index")),
        precip_mm=_num(current.get("precipitation")),
        visibility_m=_num(current.get("visibility")),
    )


# ── NWS (api.weather.gov) ─────────────────────────────────────────────────────


def _nws_nearest_station(
    lat: float, lon: float, http_get: HttpGetJson, timeout: float
) -> tuple[str, float, float] | None:
    """Return (station_id, station_lat, station_lon) for the nearest station."""
    headers = {"User-Agent": _NWS_USER_AGENT}
    points = http_get(f"https://api.weather.gov/points/{lat},{lon}", headers, timeout)
    stations_url = points["properties"]["observationStations"]
    stations = http_get(stations_url, headers, timeout)
    feat = stations["features"][0]
    sid = feat["properties"]["stationIdentifier"]
    coords = feat["geometry"]["coordinates"]  # [lon, lat]
    return sid, float(coords[1]), float(coords[0])


def normalize_nws(payload: Any, station_id: str, distance_km: float | None) -> Observation | None:
    if not isinstance(payload, dict):
        return None
    props = payload.get("properties")
    if not isinstance(props, dict):
        return None

    def field_ms(key: str) -> float | None:
        # NWS reports wind in km/h (wmoUnit:km_h-1).
        block = props.get(key)
        if not isinstance(block, dict):
            return None
        kmh = _num(block.get("value"))
        return None if kmh is None else kmh * _KMH_TO_MS

    def field_value(key: str) -> float | None:
        block = props.get(key)
        return _num(block.get("value")) if isinstance(block, dict) else None

    return Observation(
        provider="nws",
        source=f"nws:{station_id}",
        station_id=station_id,
        distance_km=distance_km,
        observed_at=_parse_iso(props.get("timestamp")),
        wind_speed_ms=field_ms("windSpeed"),
        wind_gust_ms=field_ms("windGust"),
        wind_direction_deg=field_value("windDirection"),
        visibility_m=field_value("visibility"),
    )


def _fetch_nws(
    cfg: ExternalConfig, lat: float, lon: float, http_get: HttpGetJson, timeout: float
) -> Observation | None:
    headers = {"User-Agent": _NWS_USER_AGENT}
    distance_km: float | None = None
    station_id = cfg.station_id
    if station_id is None:
        found = _nws_nearest_station(lat, lon, http_get, timeout)
        if found is None:
            return None
        station_id, slat, slon = found
        distance_km = round(haversine_km(lat, lon, slat, slon), 1)
    obs_url = f"https://api.weather.gov/stations/{station_id}/observations/latest"
    payload = http_get(obs_url, headers, timeout)
    return normalize_nws(payload, station_id, distance_km)


# ── Weather Underground PWS ───────────────────────────────────────────────────


def normalize_wunderground(payload: Any, station_id: str) -> Observation | None:
    if not isinstance(payload, dict):
        return None
    observations = payload.get("observations")
    if not isinstance(observations, list) or not observations:
        return None
    obs = observations[0]
    metric = obs.get("metric") if isinstance(obs.get("metric"), dict) else {}
    return Observation(
        provider="wunderground",
        source=f"wunderground:{station_id}",
        station_id=station_id,
        observed_at=_parse_iso(obs.get("obsTimeUtc")),
        wind_speed_ms=(
            None if _num(metric.get("windSpeed")) is None
            else _num(metric.get("windSpeed")) * _KMH_TO_MS  # type: ignore[operator]
        ),
        wind_gust_ms=(
            None if _num(metric.get("windGust")) is None
            else _num(metric.get("windGust")) * _KMH_TO_MS  # type: ignore[operator]
        ),
        wind_direction_deg=_num(obs.get("winddir")),
        precip_mm=_num(metric.get("precipTotal")),
    )


def _fetch_wunderground(
    cfg: ExternalConfig, http_get: HttpGetJson, timeout: float
) -> Observation | None:
    station_id = cfg.station_id
    if not station_id or not cfg.api_key:
        return None
    url = (
        "https://api.weather.com/v2/pws/observations/current"
        f"?stationId={station_id}&format=json&units=m&apiKey={cfg.api_key}"
    )
    payload = http_get(url, None, timeout)
    return normalize_wunderground(payload, station_id)


# ── METAR (aviationweather.gov) ───────────────────────────────────────────────

_METAR_BASE = "https://aviationweather.gov/api/data/metar"
# Discovered home station is static; cache it across refreshes (keyed on
# rounded coords). Cleared in tests via _reset_station_cache().
_STATION_CACHE: dict[tuple[float, float], str] = {}


def _reset_station_cache() -> None:
    _STATION_CACHE.clear()


def _station_cache_key(lat: float, lon: float) -> tuple[float, float]:
    return round(lat, 3), round(lon, 3)


def _parse_visibility_sm(value: Any) -> float | None:
    """Statute miles. '10+' → 10.0 (>=10); plain numeric → float; fractions
    like '1 1/2' / '1/2' summed. Unparseable → None."""
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    s = str(value).strip().rstrip("+").strip()
    if not s:
        return None
    try:
        return float(s)
    except ValueError:
        pass
    try:
        total = 0.0
        for part in s.split():
            if "/" in part:
                num, den = part.split("/")
                total += float(num) / float(den)
            else:
                total += float(part)
        return total
    except (ValueError, ZeroDivisionError):
        return None


def _parse_sky_layers(clouds: Any) -> tuple[MetarSkyLayer, ...] | None:
    if not isinstance(clouds, list):
        return None
    layers: list[MetarSkyLayer] = []
    for entry in clouds:
        if not isinstance(entry, dict):
            continue
        cover = entry.get("cover")
        if not isinstance(cover, str):
            continue
        base = _num(entry.get("base"))
        layers.append(MetarSkyLayer(cover=cover, base_ft_agl=None if base is None else int(base)))
    return tuple(layers)


def _ceiling_ft(layers: list[MetarSkyLayer] | tuple[MetarSkyLayer, ...] | None) -> int | None:
    """Lowest BKN/OVC/OVX base (ft AGL). FEW/SCT don't count; clear → None."""
    if not layers:
        return None
    bases = [
        layer.base_ft_agl
        for layer in layers
        if layer.cover in _CEILING_COVERS and layer.base_ft_agl is not None
    ]
    return min(bases) if bases else None


def _category_from_ceiling(ceiling_ft: int | None) -> str:
    if ceiling_ft is None:
        return "VFR"  # no ceiling does not constrain
    if ceiling_ft < 500:
        return "LIFR"
    if ceiling_ft < 1000:
        return "IFR"
    if ceiling_ft <= 3000:
        return "MVFR"
    return "VFR"


def _category_from_visibility(vis_sm: float | None) -> str:
    if vis_sm is None:
        return "VFR"
    if vis_sm < 1.0:
        return "LIFR"
    if vis_sm < 3.0:
        return "IFR"
    if vis_sm <= 5.0:
        return "MVFR"
    return "VFR"


def _compute_flight_category(ceiling_ft: int | None, vis_sm: float | None) -> str:
    """The WORSE of the ceiling-based and visibility-based category."""
    by_ceiling = _category_from_ceiling(ceiling_ft)
    by_vis = _category_from_visibility(vis_sm)
    return by_ceiling if _CATEGORY_RANK[by_ceiling] >= _CATEGORY_RANK[by_vis] else by_vis


def normalize_metar(payload: Any, query_lat: float, query_lon: float) -> Observation | None:
    if not isinstance(payload, list) or not payload:
        return None
    entry = payload[0]
    if not isinstance(entry, dict):
        return None

    station_id = entry.get("icaoId")
    if not isinstance(station_id, str):
        return None

    obs_time = entry.get("obsTime")
    observed_at = (
        datetime.fromtimestamp(int(obs_time), tz=UTC)
        if isinstance(obs_time, (int, float)) and not isinstance(obs_time, bool)
        else _parse_iso(entry.get("reportTime"))
    )

    sky_layers = _parse_sky_layers(entry.get("clouds"))
    ceiling = _ceiling_ft(sky_layers)
    vis_sm = _parse_visibility_sm(entry.get("visib"))
    altim_hpa = _num(entry.get("altim"))

    api_cat = entry.get("fltCat")
    flight_category = (
        api_cat if isinstance(api_cat, str) and api_cat in _CATEGORY_RANK
        else _compute_flight_category(ceiling, vis_sm)
    )

    distance_km: float | None = None
    slat, slon = _num(entry.get("lat")), _num(entry.get("lon"))
    if slat is not None and slon is not None:
        distance_km = round(haversine_km(query_lat, query_lon, slat, slon), 1)

    raw = entry.get("rawOb")
    return Observation(
        provider="metar",
        source=f"metar:{station_id}",
        station_id=station_id,
        distance_km=distance_km,
        observed_at=observed_at,
        metar_raw=raw if isinstance(raw, str) else None,
        sky_layers=sky_layers,
        ceiling_ft_agl=ceiling,
        visibility_sm=vis_sm,
        flight_category=flight_category,
        altimeter_hpa=altim_hpa,
        altimeter_inhg=None if altim_hpa is None else altim_hpa * _INHG_PER_HPA,
        temp_c=_num(entry.get("temp")),
        dewpoint_c=_num(entry.get("dewp")),
    )


def _discover_nearest_entry(
    lat: float, lon: float, http_get: HttpGetJson, timeout: float
) -> dict[str, Any] | None:
    """bbox query around the point, expanding until non-empty; return the
    nearest station entry by haversine. None if every box is empty."""
    headers = {"User-Agent": _METAR_USER_AGENT}
    for half in (0.5, 1.0, 2.0):
        bbox = f"{lat - half},{lon - half},{lat + half},{lon + half}"
        payload = http_get(f"{_METAR_BASE}?bbox={bbox}&format=json", headers, timeout)
        if not isinstance(payload, list) or not payload:
            continue
        best: dict[str, Any] | None = None
        best_km = math.inf
        for entry in payload:
            if not isinstance(entry, dict):
                continue
            elat, elon = _num(entry.get("lat")), _num(entry.get("lon"))
            if elat is None or elon is None:
                continue
            d = haversine_km(lat, lon, elat, elon)
            if d < best_km:
                best_km, best = d, entry
        if best is not None:
            return best
    return None


def _fetch_metar(
    cfg: ExternalConfig, lat: float, lon: float, http_get: HttpGetJson, timeout: float
) -> Observation | None:
    headers = {"User-Agent": _METAR_USER_AGENT}
    key = _station_cache_key(lat, lon)
    station_id = cfg.station_id or _STATION_CACHE.get(key)

    if station_id:
        payload = http_get(f"{_METAR_BASE}?ids={station_id}&format=json", headers, timeout)
        return normalize_metar(payload, lat, lon)

    entry = _discover_nearest_entry(lat, lon, http_get, timeout)
    if entry is None:
        return None
    discovered = entry.get("icaoId")
    if isinstance(discovered, str):
        _STATION_CACHE[key] = discovered
    return normalize_metar([entry], lat, lon)


# ── confidence cross-check ────────────────────────────────────────────────────


def assess_confidence(primary_ms: float | None, reference_ms: float | None) -> str:
    """Compare a primary wind speed against an independent reference. Flags
    "low" when they diverge sharply (> 5 m/s and > 50% relative), else
    "normal". Inputs in m/s."""
    if primary_ms is None or reference_ms is None:
        return "normal"
    diff = abs(primary_ms - reference_ms)
    base = max(primary_ms, reference_ms, 1.0)
    if diff > 5.0 and diff / base > 0.5:
        return "low"
    return "normal"


# ── dispatch ──────────────────────────────────────────────────────────────────


def fetch_external(
    cfg: ExternalConfig,
    lat: float,
    lon: float,
    *,
    http_get: HttpGetJson = _default_http_get,
) -> Observation | None:
    """Fetch and normalize one observation for the configured provider.

    Returns None on any failure (network, parse, missing key) — never raises.
    """
    timeout = float(cfg.http_timeout_seconds)
    try:
        if cfg.provider == "open-meteo":
            payload = http_get(_open_meteo_url(lat, lon), None, timeout)
            obs = normalize_open_meteo(payload)
        elif cfg.provider == "nws":
            obs = _fetch_nws(cfg, lat, lon, http_get, timeout)
        elif cfg.provider == "wunderground":
            obs = _fetch_wunderground(cfg, http_get, timeout)
        elif cfg.provider == "metar":
            obs = _fetch_metar(cfg, lat, lon, http_get, timeout)
        else:  # pragma: no cover - guarded by config validation
            log.warning("unknown external provider %r", cfg.provider)
            return None
    except Exception as exc:
        # No exc_info: the traceback/message can contain the wunderground key.
        log.info(
            "external fetch failed for provider %s: %s",
            cfg.provider,
            _scrub(str(exc), cfg.api_key),
        )
        return None

    if obs is None:
        return None

    if cfg.cross_check and cfg.provider not in ("nws", "metar"):
        obs = _apply_cross_check(obs, lat, lon, http_get, timeout)
    return obs


def _apply_cross_check(
    obs: Observation, lat: float, lon: float, http_get: HttpGetJson, timeout: float
) -> Observation:
    """Compare the primary wind against the nearest NWS station and tag
    confidence. Best-effort: any failure leaves confidence at "normal"."""
    try:
        headers = {"User-Agent": _NWS_USER_AGENT}
        found = _nws_nearest_station(lat, lon, http_get, timeout)
        if found is None:
            return obs
        sid, slat, slon = found
        ref_payload = http_get(
            f"https://api.weather.gov/stations/{sid}/observations/latest", headers, timeout
        )
        ref = normalize_nws(ref_payload, sid, None)
        ref_ms = ref.wind_speed_ms if ref is not None else None
    except Exception:
        log.info("cross-check fetch failed", exc_info=True)
        return obs
    confidence = assess_confidence(obs.wind_speed_ms, ref_ms)
    return replace(obs, confidence=confidence)
