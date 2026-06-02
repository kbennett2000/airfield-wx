"""Flexible anemometer topology end-to-end (Cycle 10a / ADR-0006).

Exercises the read-time resolver + freshness guard through the live /current
endpoint under both wind sources, and proves the topology-agnostic contract:
equivalent wind yields identical wind-derived output regardless of origin.

These tests run in real-poll mode pointed at a closed port (instant connection
refused ⇒ the logger writes nothing), so every row in the DB is one the test
inserted directly — full control over wind values and freshness.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from weather_server import db as db_module

REPO_ROOT = Path(__file__).parent.parent.parent
BRANDING_EXAMPLE = REPO_ROOT / "branding.toml.example"

# KSEA-area coordinates so the airport (with runways) resolves.
_LAT, _LON, _ALT = 47.45, -122.31, 132.0

_TOML = """
[server]
host = "127.0.0.1"
port = 8005
db_path = "{db_path}"
branding_path = "{branding_path}"

[logger]
interval_seconds = 60
http_timeout_seconds = 1

[cache]
ttl_seconds = 5

[logging]
enabled = true

[wind]
source = "{wind_source}"
max_age_s = {max_age_s}

[airport]
ident = "KSEA"
crosswind_limit_kt = 15

[[sensors]]
id = "outdoor"
role = "outdoor"
ip = "127.0.0.1:1"
has_gps = true
has_wind = {outdoor_has_wind}
wind_vane_offset_deg = 0.0
online_threshold_seconds = 120
{wind_station_block}
"""

_WIND_STATION_BLOCK = """
[[sensors]]
id = "windy"
role = "wind_station"
ip = "127.0.0.1:1"
has_wind = true
wind_vane_offset_deg = 0.0
online_threshold_seconds = 120
"""


def _make_app(tmp_path, monkeypatch, *, wind_source, max_age_s, outdoor_has_wind, station):  # type: ignore[no-untyped-def]
    cfg = tmp_path / "weather.toml"
    cfg.write_text(
        _TOML.format(
            db_path=str(tmp_path / "weather.db"),
            branding_path=str(BRANDING_EXAMPLE),
            wind_source=wind_source,
            max_age_s=max_age_s,
            outdoor_has_wind="true" if outdoor_has_wind else "false",
            wind_station_block=_WIND_STATION_BLOCK if station else "",
        )
    )
    monkeypatch.setenv("WEATHER_CONFIG", str(cfg))
    from weather_server.main import create_app

    return create_app()


def _insert_outdoor(db, ts, *, wind=False):  # type: ignore[no-untyped-def]
    payload = {
        "temperature_c": 16.0,
        "humidity_pct": 55.0,
        "pressure_pa": 100000,
        "latitude": _LAT,
        "longitude": _LON,
        "altitude_m": _ALT,
        "satellites": 9,
    }
    if wind:
        payload |= {"wind_speed_ms": 5.0, "wind_gust_ms": 8.0, "wind_direction_deg": 270.0}
    db_module.insert_outdoor_reading(db, timestamp=ts, payload=payload)


def _insert_wind_station(db, ts):  # type: ignore[no-untyped-def]
    db_module.insert_wind_reading(
        db,
        timestamp=ts,
        payload={"wind_speed_ms": 5.0, "wind_gust_ms": 8.0, "wind_direction_deg": 270.0},
    )


# ── freshness guard (outdoor source) ─────────────────────────────────────────


def test_fresh_outdoor_wind_lights_runway(tmp_path: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    app = _make_app(
        tmp_path, monkeypatch, wind_source="outdoor", max_age_s=90,
        outdoor_has_wind=True, station=False,
    )
    with TestClient(app) as tc:
        _insert_outdoor(tc.app.state.db, int(time.time()), wind=True)
        body = tc.get("/api/v1/current").json()

    outdoor = body["sensors"]["outdoor"]
    assert outdoor["derived"]["wind_direction_true_deg"] == 270.0
    assert outdoor["derived"]["beaufort_force"] is not None
    assert outdoor["derived"]["wind_chill_c"] is not None
    assert body["airport"]["runway_solution"] is not None


def test_stale_outdoor_wind_nulls_derived_and_runway(tmp_path: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    app = _make_app(
        tmp_path, monkeypatch, wind_source="outdoor", max_age_s=90,
        outdoor_has_wind=True, station=False,
    )
    with TestClient(app) as tc:
        # A reading well past the freshness threshold.
        _insert_outdoor(tc.app.state.db, int(time.time()) - 1000, wind=True)
        body = tc.get("/api/v1/current").json()

    outdoor = body["sensors"]["outdoor"]
    # Raw wind (what the sensor measured) is still surfaced...
    assert outdoor["raw"]["wind_speed_ms"] is not None
    # ...but every wind-DERIVED field is nulled by the freshness guard.
    assert outdoor["derived"]["wind_direction_true_deg"] is None
    assert outdoor["derived"]["beaufort_force"] is None
    assert outdoor["derived"]["wind_chill_c"] is None
    assert body["airport"]["runway_solution"] is None


# ── separate wind station (topology 3) ───────────────────────────────────────


def test_station_source_feeds_derivations(tmp_path: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    app = _make_app(
        tmp_path, monkeypatch, wind_source="windy", max_age_s=90,
        outdoor_has_wind=False, station=True,
    )
    now = int(time.time())
    with TestClient(app) as tc:
        _insert_outdoor(tc.app.state.db, now, wind=False)  # no wind on outdoor
        _insert_wind_station(tc.app.state.db, now)
        body = tc.get("/api/v1/current").json()

    outdoor = body["sensors"]["outdoor"]
    # The outdoor unit has no anemometer ⇒ no raw wind on its block...
    assert outdoor["raw"]["wind_speed_ms"] is None
    # ...yet the field's wind-derived values come from the wind station.
    assert outdoor["derived"]["wind_direction_true_deg"] == 270.0
    assert outdoor["derived"]["wind_chill_c"] is not None
    assert body["airport"]["runway_solution"] is not None
    # The wind station is a data source, not a display sensor.
    assert "windy" not in body["sensors"]
    assert tc.get("/api/v1/current/windy").status_code == 404


def test_stale_station_wind_nulls_derived_and_runway(tmp_path: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    # The freshness guard applies over the real wind_readings ingest table too:
    # a stale wind-station row nulls the wind-derived fields + runway solution.
    app = _make_app(
        tmp_path, monkeypatch, wind_source="windy", max_age_s=90,
        outdoor_has_wind=False, station=True,
    )
    now = int(time.time())
    with TestClient(app) as tc:
        _insert_outdoor(tc.app.state.db, now, wind=False)
        _insert_wind_station(tc.app.state.db, now - 1000)  # well past max_age_s
        body = tc.get("/api/v1/current").json()

    outdoor = body["sensors"]["outdoor"]
    assert outdoor["derived"]["wind_direction_true_deg"] is None
    assert outdoor["derived"]["wind_chill_c"] is None
    assert body["airport"]["runway_solution"] is None


# ── produce → consume through the real logger loop (integration) ─────────────

_FIXTURE_TOML = """
[server]
host = "127.0.0.1"
port = 8005
db_path = "{db_path}"
branding_path = "{branding_path}"

[logger]
interval_seconds = 1
http_timeout_seconds = 1

[cache]
ttl_seconds = 5

[logging]
enabled = true

[development]
fixture_dir = "{fixture_dir}"

[wind]
source = "windy"
max_age_s = 90

[airport]
ident = "KSEA"
crosswind_limit_kt = 15

[[sensors]]
id = "outdoor"
role = "outdoor"
ip = "127.0.0.1:1"
has_gps = true
has_wind = false
online_threshold_seconds = 120

[[sensors]]
id = "windy"
role = "wind_station"
ip = "127.0.0.1:1"
has_wind = true
wind_vane_offset_deg = 0.0
online_threshold_seconds = 120
"""


@pytest.mark.integration
def test_wind_logger_produces_row_consumed_by_current(tmp_path: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """The full producing→consuming path: the wind logger loop polls the
    station fixture and writes a wind_readings row, which the resolver then
    surfaces on /current (runway populated). No direct DB injection."""
    fixtures = tmp_path / "fixtures"
    fixtures.mkdir()
    (fixtures / "outdoor.json").write_text(
        json.dumps([{
            "temperature_c": 16.0, "humidity_pct": 55.0, "pressure_pa": 100000,
            "latitude": _LAT, "longitude": _LON, "altitude_m": _ALT, "satellites": 9,
        }])
    )
    (fixtures / "windy.json").write_text(
        json.dumps({"wind_speed_ms": 5.0, "wind_gust_ms": 8.0, "wind_direction_deg": 270.0})
    )
    cfg = tmp_path / "weather.toml"
    cfg.write_text(
        _FIXTURE_TOML.format(
            db_path=str(tmp_path / "weather.db"),
            branding_path=str(BRANDING_EXAMPLE),
            fixture_dir=str(fixtures),
        )
    )
    monkeypatch.setenv("WEATHER_CONFIG", str(cfg))
    from weather_server.main import create_app

    app = create_app()
    with TestClient(app) as tc:
        # Wait for the wind logger to poll-and-write a row that /current resolves.
        deadline = time.monotonic() + 10.0
        body: dict = {}
        while time.monotonic() < deadline:
            body = tc.get("/api/v1/current").json()
            if body["airport"]["runway_solution"] is not None:
                break
            time.sleep(0.1)

        assert body["airport"]["runway_solution"] is not None
        # The row really was logged (not injected).
        assert db_module.latest_wind_reading(tc.app.state.db) is not None
        # And the derived true direction came from the station's wind.
        assert body["sensors"]["outdoor"]["derived"]["wind_direction_true_deg"] == 270.0


# ── topology-agnostic contract ───────────────────────────────────────────────


def test_outdoor_and_station_wind_yield_identical_output(tmp_path: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """Equivalent wind (same speed/direction, zero offset) must produce the
    same wind_direction_true_deg and runway solution whether it rode the
    outdoor payload or a separate wind station — the resolver contract."""
    now = int(time.time())

    (tmp_path / "a").mkdir(exist_ok=True)
    app_a = _make_app(
        tmp_path / "a", monkeypatch, wind_source="outdoor", max_age_s=90,
        outdoor_has_wind=True, station=False,
    )
    with TestClient(app_a) as tc:
        _insert_outdoor(tc.app.state.db, now, wind=True)
        body_a = tc.get("/api/v1/current").json()

    (tmp_path / "b").mkdir(exist_ok=True)
    app_b = _make_app(
        tmp_path / "b", monkeypatch, wind_source="windy", max_age_s=90,
        outdoor_has_wind=False, station=True,
    )
    with TestClient(app_b) as tc:
        _insert_outdoor(tc.app.state.db, now, wind=False)
        _insert_wind_station(tc.app.state.db, now)
        body_b = tc.get("/api/v1/current").json()

    da = body_a["sensors"]["outdoor"]["derived"]
    db_ = body_b["sensors"]["outdoor"]["derived"]
    assert da["wind_direction_true_deg"] == db_["wind_direction_true_deg"]

    sol_a = body_a["airport"]["runway_solution"]
    sol_b = body_b["airport"]["runway_solution"]
    assert sol_a is not None and sol_b is not None
    assert sol_a["favored"] == sol_b["favored"]
    assert sol_a["ends"][0]["headwind_kt"] == sol_b["ends"][0]["headwind_kt"]
    assert sol_a["ends"][0]["crosswind_kt"] == sol_b["ends"][0]["crosswind_kt"]
