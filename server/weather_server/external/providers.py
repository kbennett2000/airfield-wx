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

log = logging.getLogger(__name__)

# url, headers, timeout_seconds -> parsed JSON
HttpGetJson = Callable[[str, dict[str, str] | None, float], Any]

_NWS_USER_AGENT = "(jones-weather-station, weather-server)"
_KMH_TO_MS = 1.0 / 3.6


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


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return r * 2 * math.asin(math.sqrt(a))


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

    if cfg.cross_check and cfg.provider != "nws":
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
