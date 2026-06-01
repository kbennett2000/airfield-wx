"""End-to-end wind data path (Cycle 6b payoff): a wind-bearing outdoor reading
flows through the DB + derivations to surface raw wind, the vane-corrected true
direction, and a populated runway_solution on /api/v1/current — with no airport
composer change. Fast (fixture mode, no real network).
"""

from __future__ import annotations

import json
from pathlib import Path

from fastapi.testclient import TestClient

REPO_ROOT = Path(__file__).parent.parent.parent
BRANDING_EXAMPLE = REPO_ROOT / "branding.toml.example"

_BASE_PAYLOAD = {
    "temperature_c": 16.0,
    "humidity_pct": 55.0,
    "pressure_pa": 100000,
    "latitude": 47.45,
    "longitude": -122.31,
    "altitude_m": 132.0,
    "satellites": 9,
    "rssi_dbm": -62,
    "uptime_s": 84320,
    "free_heap_bytes": 178000,
}
_WIND = {"wind_speed_ms": 5.2, "wind_gust_ms": 8.1, "wind_direction_deg": 270.0}

_TOML = """
[server]
host = "127.0.0.1"
port = 8005
db_path = "{db_path}"
branding_path = "{branding_path}"

[logger]
interval_seconds = 60
http_timeout_seconds = 10

[cache]
ttl_seconds = 5

[development]
fixture_dir = "{fixture_dir}"

[airport]
ident = "KSEA"
crosswind_limit_kt = 15

[[sensors]]
id = "outdoor"
role = "outdoor"
ip = "192.168.1.60"
has_gps = true
has_wind = {has_wind}
wind_vane_offset_deg = 0.0
online_threshold_seconds = 120
"""


def _build_app(tmp_path: Path, monkeypatch, *, has_wind: bool, include_wind: bool):  # type: ignore[no-untyped-def]
    fixtures = tmp_path / "fixtures"
    fixtures.mkdir()
    payload = dict(_BASE_PAYLOAD)
    if include_wind:
        payload |= _WIND
    (fixtures / "outdoor.json").write_text(json.dumps([payload]))

    cfg = tmp_path / "weather.toml"
    cfg.write_text(
        _TOML.format(
            db_path=str(tmp_path / "weather.db"),
            branding_path=str(BRANDING_EXAMPLE),
            fixture_dir=str(fixtures),
            has_wind="true" if has_wind else "false",
        )
    )
    monkeypatch.setenv("WEATHER_CONFIG", str(cfg))

    from weather_server.main import create_app

    return create_app()


def test_wind_lights_up_runway_solution(tmp_path: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    app = _build_app(tmp_path, monkeypatch, has_wind=True, include_wind=True)
    with TestClient(app) as tc:
        body = tc.get("/api/v1/current").json()

    outdoor = body["sensors"]["outdoor"]
    # Raw wind surfaced.
    assert outdoor["raw"]["wind_speed_ms"] is not None
    assert outdoor["raw"]["wind_direction_deg"] is not None
    # Vane-corrected true direction derived (offset 0 → equals raw).
    assert outdoor["derived"]["wind_direction_true_deg"] is not None

    # Wind-fused comfort indices now live in derived, computed from LOCAL wind.
    derived = outdoor["derived"]
    assert derived["beaufort_force"] is not None
    assert derived["beaufort_description"] is not None
    assert derived["wind_chill_c"] is not None
    assert derived["apparent_temperature_c"] is not None
    # No light sensor (has_light defaults false; no lux) → no solar → THSW/ET0
    # and the whole sky block cascade to null.
    assert derived["thsw_index_c"] is None
    assert derived["et0_mm_hour"] is None
    assert derived["sky"] is None

    # The payoff: runway_solution computed from LOCAL wind.
    airport = body["airport"]
    assert airport is not None
    assert airport["ident"] == "KSEA"
    solution = airport["runway_solution"]
    assert solution is not None
    assert solution["favored"] is not None
    assert solution["ends"]
    # Each end carries the Cycle 4 components.
    end = solution["ends"][0]
    assert end["headwind_kt"] is not None
    assert end["crosswind_kt"] is not None


def test_no_wind_leaves_runway_solution_null(tmp_path: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    app = _build_app(tmp_path, monkeypatch, has_wind=True, include_wind=False)
    with TestClient(app) as tc:
        body = tc.get("/api/v1/current").json()

    outdoor = body["sensors"]["outdoor"]
    assert outdoor["raw"]["wind_speed_ms"] is None
    assert outdoor["derived"]["wind_direction_true_deg"] is None
    assert body["airport"]["runway_solution"] is None


def test_has_wind_false_suppresses_wind(tmp_path: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    # Wind is in the payload, but the station declares no anemometer.
    app = _build_app(tmp_path, monkeypatch, has_wind=False, include_wind=True)
    with TestClient(app) as tc:
        body = tc.get("/api/v1/current").json()

    outdoor = body["sensors"]["outdoor"]
    assert outdoor["raw"]["wind_speed_ms"] is None
    assert outdoor["derived"]["wind_direction_true_deg"] is None
    assert outdoor["calibration"]["wind_vane_offset_deg"] is None
    # No local wind → no derived comfort indices.
    assert outdoor["derived"]["beaufort_force"] is None
    assert outdoor["derived"]["wind_chill_c"] is None
    assert outdoor["derived"]["apparent_temperature_c"] is None
    assert body["airport"]["runway_solution"] is None
