"""ESP32 /data wire format → internal SensorPayload adapter.

The outdoor sketch (sketches/outdoor.ino) emits JSON-shaped strings by
string-concatenating field names with String(<float>) values.
Two notable wire-format quirks the adapter absorbs:

1. **`nan` text instead of valid JSON null** (BUG-08 in the findings
   doc). When the underlying sensor reading is NaN, the Arduino
   String(float) conversion emits the literal text "nan", which is not
   valid JSON. Phase 5 fixed this at source: the new sketches route
   every float through a `floatJson()` helper that emits `null` for
   NaN. The sanitizer here stays as defense-in-depth for any ESP32
   still running pre-Phase-5 firmware that hasn't been reflashed.

2. **Field-name and unit differences vs. the internal SensorPayload.**
   The wire format uses `temperatureC`, `humidity`, `pressure` (hPa),
   `uptime` (milliseconds), etc. The internal payload uses
   `temperature_c`, `humidity_pct`, `pressure_pa`, `uptime_s`, etc.
   The adapter renames and converts.

3. **`full` channel is not on the wire.** The outdoor sketch reports
   `ir` and `visible` separately but omits the full-spectrum sum.
   The adapter derives `full_spectrum = visible + ir` when both are
   present; otherwise leaves it None.

Error envelopes (`{"error": "..."}`) are translated to a poll result of
None — the same as a network failure.
"""

from __future__ import annotations

import json
import logging
import math
import re
from typing import Any

log = logging.getLogger(__name__)

_NAN_TOKEN_RE = re.compile(r"(?<![A-Za-z0-9_])(nan|NaN|NAN|undefined)(?![A-Za-z0-9_])")


def sanitize_nan_tokens(text: str) -> str:
    """Replace bare nan/NaN/undefined tokens with null. See BUG-08."""
    return _NAN_TOKEN_RE.sub("null", text)


def parse_outdoor(text: str) -> dict[str, Any] | None:
    raw = _safe_load(text)
    if raw is None or "error" in raw:
        return None
    return _outdoor_to_payload(raw)


def parse_wind_station(text: str) -> dict[str, Any] | None:
    """Adapt a separate wind station's /data payload (ADR-0006 / topology 3).

    The wind station is anemometer-only — it emits the same wind wire keys as
    the outdoor unit (`windSpeed`/`windGust`/`windDirection`) plus device
    telemetry, and nothing else (no BME280, no GPS). Wind direction is the raw
    vane frame; the vane offset is applied server-side by the resolver. NaN /
    absent values are dropped; an error envelope becomes None."""
    raw = _safe_load(text)
    if raw is None or "error" in raw:
        return None
    payload: dict[str, Any] = {}
    _put_wind_fields(payload, raw)
    _put_device_fields(payload, raw)
    return payload


def parse(text: str, role: str) -> dict[str, Any] | None:
    """Dispatch on sensor role. `wind_station` uses the anemometer-only adapter
    (ADR-0006); every other role (the outdoor unit, and any unknown role) uses
    the outdoor adapter."""
    if role == "wind_station":
        return parse_wind_station(text)
    return parse_outdoor(text)


def _safe_load(text: str) -> dict[str, Any] | None:
    sanitized = sanitize_nan_tokens(text)
    try:
        data = json.loads(sanitized)
    except json.JSONDecodeError:
        log.warning("failed to decode wire-format JSON: %r", text[:200])
        return None
    if not isinstance(data, dict):
        log.warning("wire-format JSON was not an object: %r", text[:200])
        return None
    return data


def _outdoor_to_payload(raw: dict[str, Any]) -> dict[str, Any]:
    payload: dict[str, Any] = {}
    _put_float(payload, "temperature_c", raw.get("temperatureC"))
    _put_float(payload, "humidity_pct", raw.get("humidity"))

    pressure_hpa = _clean_float(raw.get("pressure"))
    if pressure_hpa is not None:
        payload["pressure_pa"] = pressure_hpa * 100.0

    _put_float(payload, "lux", raw.get("lux"))
    _put_int(payload, "ir", raw.get("ir"))
    _put_int(payload, "visible", raw.get("visible"))

    ir = payload.get("ir")
    visible = payload.get("visible")
    if ir is not None and visible is not None:
        payload["full_spectrum"] = int(ir) + int(visible)

    _put_wind_fields(payload, raw)

    _put_float(payload, "latitude", raw.get("latitude"))
    _put_float(payload, "longitude", raw.get("longitude"))
    _put_float(payload, "altitude_m", raw.get("altitude"))
    _put_float(payload, "speed_kmh", raw.get("speed"))
    _put_float(payload, "course_deg", raw.get("course"))
    _put_int(payload, "satellites", raw.get("satellites"))

    _put_device_fields(payload, raw)

    return payload


def _put_wind_fields(payload: dict[str, Any], raw: dict[str, Any]) -> None:
    """Map the shared wind wire keys → internal raw wind fields. Used by both
    the outdoor and wind-station adapters so the mapping lives in one place
    (firmware already emits m/s; degrees in the raw vane frame)."""
    _put_float(payload, "wind_speed_ms", raw.get("windSpeed"))
    _put_float(payload, "wind_gust_ms", raw.get("windGust"))
    _put_float(payload, "wind_direction_deg", raw.get("windDirection"))


def _put_device_fields(payload: dict[str, Any], raw: dict[str, Any]) -> None:
    """Map the shared device-telemetry wire keys → internal fields (uptime
    ms→s). Common to every station type."""
    _put_int(payload, "rssi_dbm", raw.get("rssi"))
    uptime_ms = _clean_int(raw.get("uptime"))
    if uptime_ms is not None:
        payload["uptime_s"] = uptime_ms // 1000
    _put_int(payload, "free_heap_bytes", raw.get("freeHeap"))


def _put_float(payload: dict[str, Any], key: str, raw: Any) -> None:
    value = _clean_float(raw)
    if value is not None:
        payload[key] = value


def _put_int(payload: dict[str, Any], key: str, raw: Any) -> None:
    value = _clean_int(raw)
    if value is not None:
        payload[key] = value


def _clean_float(raw: Any) -> float | None:
    if raw is None:
        return None
    try:
        f = float(raw)
    except (TypeError, ValueError):
        return None
    if math.isnan(f) or math.isinf(f):
        return None
    return f


def _clean_int(raw: Any) -> int | None:
    if raw is None:
        return None
    try:
        f = float(raw)
    except (TypeError, ValueError):
        return None
    if math.isnan(f) or math.isinf(f):
        return None
    return int(f)
