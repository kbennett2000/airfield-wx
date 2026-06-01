"""End-to-end endpoint tests using the FastAPI TestClient.

These tests start the app with the example config + fixture dir and
exercise every endpoint, asserting response shape against the Pydantic
models (which already mirror 02-api-design.md exactly) plus the
documented error cases.

The `client` fixture lives in conftest.py.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from weather_server import schemas

SERVER_ROOT = Path(__file__).parent.parent
FIXTURE_SRC = SERVER_ROOT / "fixtures"


def test_current_endpoint_returns_documented_shape(client: TestClient) -> None:
    r = client.get("/api/v1/current")
    assert r.status_code == 200
    parsed = schemas.CurrentResponse.model_validate(r.json())
    # /current = outdoor + airport + astronomy + external (ADR-0007: no
    # indoor/basement instances).
    assert set(parsed.sensors) == {"outdoor"}
    assert "indoor" not in parsed.sensors
    assert "basement" not in parsed.sensors

    outdoor = parsed.sensors["outdoor"]
    assert outdoor.online is True
    # Pressure quadruple all present.
    d = outdoor.derived
    assert d.pressure_station_hpa is not None
    assert d.pressure_station_inhg is not None
    assert d.pressure_sealevel_hpa is not None
    assert d.pressure_sealevel_inhg is not None

    # Location block on the outdoor (GPS) sensor.
    assert outdoor.location is not None
    assert outdoor.location.maidenhead is not None

    # Astronomy populated.
    assert parsed.astronomy.timezone in ("America/Denver", "UTC")
    assert parsed.astronomy.reference_location.source == "outdoor_sensor"


def test_current_outdoor_has_sky_block(client: TestClient) -> None:
    parsed = schemas.CurrentResponse.model_validate(client.get("/api/v1/current").json())
    sky = parsed.sensors["outdoor"].derived.sky
    assert sky is not None
    assert sky.estimated is True
    assert sky.solar_irradiance_w_m2 is not None
    assert sky.sun_altitude_deg is not None


def test_current_outdoor_extended_thermo_present(client: TestClient) -> None:
    parsed = schemas.CurrentResponse.model_validate(client.get("/api/v1/current").json())
    d = parsed.sensors["outdoor"].derived
    assert d.wet_bulb_c is not None
    assert d.vapor_pressure_deficit_kpa is not None
    assert d.air_density_kg_m3 is not None


def test_current_one_outdoor(client: TestClient) -> None:
    r = client.get("/api/v1/current/outdoor")
    assert r.status_code == 200
    parsed = schemas.CurrentSensorResponse.model_validate(r.json())
    assert parsed.sensor.sensor_id == "outdoor"


def test_current_one_unknown_returns_404_with_error_envelope(client: TestClient) -> None:
    r = client.get("/api/v1/current/kitchen")
    assert r.status_code == 404
    body = r.json()
    schemas.ErrorResponse.model_validate(body)
    assert body["error"]["code"] == "sensor_not_found"


def test_history_outdoor_returns_rows(client: TestClient) -> None:
    r = client.get("/api/v1/history/outdoor?hours=24")
    assert r.status_code == 200
    parsed = schemas.HistoryResponse.model_validate(r.json())
    assert parsed.sensor_id == "outdoor"
    assert parsed.row_count == len(parsed.rows)
    if parsed.rows:
        # default include is "weather": no lux on the row.
        sample = parsed.rows[0].model_dump()
        assert "lux" not in sample
        assert "temperature_c" in sample


def test_history_include_light_adds_lux(client: TestClient) -> None:
    r = client.get("/api/v1/history/outdoor?hours=24&include=weather,light")
    assert r.status_code == 200
    parsed = schemas.HistoryResponse.model_validate(r.json())
    if parsed.rows:
        sample = parsed.rows[0].model_dump()
        assert "lux" in sample


def test_history_unknown_sensor_returns_404_sensor_not_found(client: TestClient) -> None:
    r = client.get("/api/v1/history/kitchen")
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "sensor_not_found"


def test_history_bad_include_group_returns_400(client: TestClient) -> None:
    r = client.get("/api/v1/history/outdoor?include=weather,nonsense")
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "bad_request"


def test_history_from_to_window(client: TestClient) -> None:
    # An explicit `from` defines the window (overriding the default `hours`).
    r = client.get("/api/v1/history/outdoor?from=2020-01-01T00:00:00Z")
    assert r.status_code == 200
    parsed = schemas.HistoryResponse.model_validate(r.json())
    assert parsed.from_.year == 2020
    assert parsed.row_count > 0  # fixture-prefilled rows fall in [2020, now]


def test_history_from_to_overrides_hours(client: TestClient) -> None:
    r = client.get(
        "/api/v1/history/outdoor?hours=1&from=2020-01-01T00:00:00Z&to=2030-01-01T00:00:00Z"
    )
    assert r.status_code == 200
    parsed = schemas.HistoryResponse.model_validate(r.json())
    assert parsed.from_.year == 2020
    assert parsed.to.year == 2030


def test_history_bad_from_returns_400(client: TestClient) -> None:
    r = client.get("/api/v1/history/outdoor?from=not-a-date")
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "bad_request"


def test_history_from_after_to_returns_400(client: TestClient) -> None:
    r = client.get(
        "/api/v1/history/outdoor?from=2030-01-01T00:00:00Z&to=2020-01-01T00:00:00Z"
    )
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "bad_request"


def test_summary_outdoor_returns_stats(client: TestClient) -> None:
    r = client.get("/api/v1/summary/outdoor?period=7d")
    assert r.status_code == 200
    parsed = schemas.SummaryResponse.model_validate(r.json())
    assert parsed.sensor_id == "outdoor"
    assert parsed.period == "7d"
    assert parsed.sample_count > 0
    assert parsed.temperature_c is not None and parsed.temperature_c.max is not None
    assert parsed.pressure_trend in ("rising", "falling", "steady")
    assert parsed.diurnal_range_c is not None
    assert parsed.heating_degree_days_f is not None
    assert parsed.light_integral_mol_m2 is not None


def test_summary_today_period_label(client: TestClient) -> None:
    r = client.get("/api/v1/summary/outdoor")  # default period=today
    assert r.status_code == 200
    parsed = schemas.SummaryResponse.model_validate(r.json())
    assert parsed.period == "today"


def test_summary_empty_db_returns_null_stats(client: TestClient) -> None:
    client.app.state.db.execute("DELETE FROM outdoor_readings")
    r = client.get("/api/v1/summary/outdoor?period=7d")
    assert r.status_code == 200
    parsed = schemas.SummaryResponse.model_validate(r.json())
    assert parsed.sample_count == 0
    assert parsed.temperature_c is None
    assert parsed.pressure_trend is None
    assert parsed.heating_degree_days_f is None
    assert parsed.light_integral_mol_m2 is None


def test_summary_unknown_404_sensor_not_found(client: TestClient) -> None:
    r = client.get("/api/v1/summary/kitchen")
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "sensor_not_found"


def test_summary_bad_period_422(client: TestClient) -> None:
    # period is a Literal; FastAPI rejects unknown values with 422.
    r = client.get("/api/v1/summary/outdoor?period=decade")
    assert r.status_code == 422


def test_sensors_endpoint_lists_outdoor(client: TestClient) -> None:
    r = client.get("/api/v1/sensors")
    assert r.status_code == 200
    parsed = schemas.SensorListResponse.model_validate(r.json())
    ids = [s.sensor_id for s in parsed.sensors]
    assert set(ids) == {"outdoor"}
    outdoor_entry = next(s for s in parsed.sensors if s.sensor_id == "outdoor")
    assert outdoor_entry.logged is True


def test_astronomy_endpoint(client: TestClient) -> None:
    r = client.get("/api/v1/astronomy")
    assert r.status_code == 200
    parsed = schemas.AstronomyResponse.model_validate(r.json())
    assert parsed.astronomy.reference_location.source == "outdoor_sensor"


def test_astronomy_includes_extended_fields(client: TestClient) -> None:
    parsed = schemas.AstronomyResponse.model_validate(client.get("/api/v1/astronomy").json())
    sun = parsed.astronomy.sun
    moon = parsed.astronomy.moon
    # Denver latitude gets all twilight bands year-round.
    assert sun.nautical_dawn is not None
    assert sun.astronomical_dawn is not None
    assert sun.golden_hour_dusk is not None
    assert sun.season is not None
    assert sun.next_solar_event is not None
    assert sun.next_solar_event_time is not None
    assert moon.next_new_moon is not None
    assert moon.next_full_moon is not None


def test_astronomy_with_lat_lon_override(client: TestClient) -> None:
    r = client.get("/api/v1/astronomy?lat=51.5074&lon=-0.1278")
    assert r.status_code == 200
    parsed = schemas.AstronomyResponse.model_validate(r.json())
    assert parsed.astronomy.reference_location.source == "query_override"
    assert parsed.astronomy.timezone == "Europe/London"


def test_health_endpoint(client: TestClient) -> None:
    r = client.get("/api/v1/health")
    assert r.status_code == 200
    parsed = schemas.HealthResponse.model_validate(r.json())
    assert parsed.db_reachable is True
    assert any(le.sensor_id == "outdoor" for le in parsed.loggers)
    assert len(parsed.loggers) == 1  # only outdoor is logged


def test_offline_sensor_returns_503_when_never_seen(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Generic on-demand-poll mechanism (retained for the multi-station
    capability): a configured non-outdoor station whose snapshot is offline
    AND has no last_seen → GET /api/v1/current/<id> returns 503
    sensor_no_data."""
    fixture_dir = tmp_path / "fixtures"
    fixture_dir.mkdir()
    shutil.copy(FIXTURE_SRC / "outdoor.json", fixture_dir / "outdoor.json")
    (fixture_dir / "aux.json").write_text('{"temperature_c": 22.0, "offline": true}')

    db_path = tmp_path / "weather.db"
    cfg = tmp_path / "weather.toml"
    from tests.conftest import BRANDING_EXAMPLE, TOML_TEMPLATE
    aux_block = (
        '\n[[sensors]]\nid = "aux"\nrole = "aux"\n'
        'ip = "192.168.1.99"\nonline_threshold_seconds = 120\n'
    )
    cfg.write_text(
        TOML_TEMPLATE.format(
            db_path=str(db_path),
            fixture_dir=str(fixture_dir),
            branding_path=str(BRANDING_EXAMPLE),
        )
        + aux_block
    )
    monkeypatch.setenv("WEATHER_CONFIG", str(cfg))

    from weather_server.main import create_app

    with TestClient(create_app()) as tc:
        r = tc.get("/api/v1/current/aux")
        assert r.status_code == 503
        assert r.json()["error"]["code"] == "sensor_no_data"


def test_dashboard_assets_served_no_cache(client: TestClient) -> None:
    """Dashboard static files must revalidate so a deploy never leaves the
    browser running a stale app.js (regression guard for the cache bug)."""
    for path in ("/dashboard/app.js", "/dashboard/index.html"):
        r = client.get(path)
        assert r.status_code == 200, path
        assert r.headers.get("cache-control") == "no-cache", path


def test_openapi_docs_render(client: TestClient) -> None:
    r = client.get("/docs")
    assert r.status_code == 200
    assert b"swagger" in r.content.lower()
    r = client.get("/openapi.json")
    assert r.status_code == 200
    schema = r.json()
    paths = schema["paths"]
    for expected in (
        "/api/v1/current",
        "/api/v1/current/{sensor_id}",
        "/api/v1/history/{sensor_id}",
        "/api/v1/summary/{sensor_id}",
        "/api/v1/external",
        "/api/v1/sensors",
        "/api/v1/astronomy",
        "/api/v1/health",
    ):
        assert expected in paths
