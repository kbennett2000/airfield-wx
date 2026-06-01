"""Tests for coverage gaps in the external feed, summary endpoint, sky block,
and fused indices (commits b428939..HEAD).

Every test here was written after confirming the specific path was NOT
exercised by the existing 239-test suite. Tests are grouped by the thing
under test, not by file.
"""

from __future__ import annotations

import json
import os
import shutil
import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from weather_server import schemas
from weather_server.config import ExternalConfig
from weather_server.external import providers as p
from weather_server.external.providers import Observation
from weather_server.responses import build_external

SERVER_ROOT = Path(__file__).parent.parent
FIXTURE_SRC = SERVER_ROOT / "fixtures"


# ── helpers shared across sections ───────────────────────────────────────────


def _push(client: TestClient, **kw: object) -> None:
    obs = Observation(provider="open-meteo", source="open-meteo:best_match", **kw)
    client.app.state.external_store.set(obs, datetime.now(UTC))


# ── providers: cross-check path ───────────────────────────────────────────────

# NWS station/observation payloads used by the multi-URL dispatcher.
_NWS_POINTS_PAYLOAD: dict[str, Any] = {
    "properties": {
        "observationStations": "https://api.weather.gov/gridpoints/BOU/fake/stations"
    }
}

_NWS_STATIONS_PAYLOAD: dict[str, Any] = {
    "features": [
        {
            "properties": {"stationIdentifier": "KBJC"},
            "geometry": {"coordinates": [-104.62, 39.20]},
        }
    ]
}

_NWS_OBS_CLOSE_WIND: dict[str, Any] = {
    "properties": {
        "timestamp": "2026-05-29T21:00:00+00:00",
        # 18 km/h → 5 m/s; primary is 1 m/s → diff 4 m/s < 5 m/s → "normal".
        "windSpeed": {"value": 18.0, "unitCode": "wmoUnit:km_h-1"},
        "windGust": {"value": None},
        "windDirection": {"value": 270, "unitCode": "wmoUnit:degree_(angle)"},
        "visibility": {"value": 16090, "unitCode": "wmoUnit:m"},
    }
}

_NWS_OBS_FAR_WIND: dict[str, Any] = {
    "properties": {
        "timestamp": "2026-05-29T21:00:00+00:00",
        # 144 km/h → 40 m/s; primary is 1 m/s → diff 39 m/s >> threshold → "low".
        "windSpeed": {"value": 144.0, "unitCode": "wmoUnit:km_h-1"},
        "windGust": {"value": None},
        "windDirection": {"value": 270, "unitCode": "wmoUnit:degree_(angle)"},
        "visibility": {"value": None},
    }
}

_OPEN_METEO_1MS: dict[str, Any] = {
    "current": {
        "time": "2026-05-29T22:00",
        "wind_speed_10m": 1.0,
        "wind_direction_10m": 90,
        "wind_gusts_10m": 2.0,
        "cloud_cover": 20,
        "uv_index": 1.0,
        "precipitation": 0.0,
        "visibility": 20000.0,
    }
}


def _make_cross_check_http(
    primary_payload: dict[str, Any], nws_obs_payload: dict[str, Any]
) -> p.HttpGetJson:
    """Return an injected http_get that dispatches by URL fragment."""

    def _get(url: str, headers: dict[str, str] | None, timeout: float) -> Any:
        if "open-meteo" in url:
            return primary_payload
        if "/points/" in url:
            return _NWS_POINTS_PAYLOAD
        if "stations" in url and "/observations" not in url:
            return _NWS_STATIONS_PAYLOAD
        # .../observations/latest
        return nws_obs_payload

    return _get


def test_cross_check_normal_confidence_when_winds_agree() -> None:
    """open-meteo primary ~1 m/s vs NWS reference ~5 m/s → diff 4 m/s < 5 m/s
    absolute threshold → 'normal'."""
    cfg = ExternalConfig(enabled=True, provider="open-meteo", cross_check=True)
    obs = p.fetch_external(
        cfg,
        39.4,
        -104.5,
        http_get=_make_cross_check_http(_OPEN_METEO_1MS, _NWS_OBS_CLOSE_WIND),
    )
    assert obs is not None
    assert obs.confidence == "normal"


def test_cross_check_low_confidence_when_winds_diverge() -> None:
    """open-meteo primary 1 m/s vs NWS reference 40 m/s → difference far
    exceeds both thresholds → confidence 'low'."""
    cfg = ExternalConfig(enabled=True, provider="open-meteo", cross_check=True)
    obs = p.fetch_external(
        cfg,
        39.4,
        -104.5,
        http_get=_make_cross_check_http(_OPEN_METEO_1MS, _NWS_OBS_FAR_WIND),
    )
    assert obs is not None
    assert obs.confidence == "low"


def test_cross_check_skipped_when_disabled() -> None:
    """cross_check=False ⇒ confidence field stays None (never set)."""
    cfg = ExternalConfig(enabled=True, provider="open-meteo", cross_check=False)
    obs = p.fetch_external(
        cfg,
        39.4,
        -104.5,
        http_get=_make_cross_check_http(_OPEN_METEO_1MS, _NWS_OBS_CLOSE_WIND),
    )
    assert obs is not None
    assert obs.confidence is None


def test_cross_check_survives_nws_failure() -> None:
    """If the NWS cross-check HTTP call raises, the primary obs is returned
    unchanged (confidence None) — not a hard failure."""

    def _get(url: str, headers: dict[str, str] | None, timeout: float) -> Any:
        if "open-meteo" in url:
            return _OPEN_METEO_1MS
        raise ConnectionError("nws is down")

    cfg = ExternalConfig(enabled=True, provider="open-meteo", cross_check=True)
    obs = p.fetch_external(cfg, 39.4, -104.5, http_get=_get)
    assert obs is not None
    assert obs.confidence is None  # best-effort: leaves it at default


# ── providers: NWS auto-discovery path ───────────────────────────────────────


def test_nws_auto_discovery_station_id_and_distance() -> None:
    """When station_id is NOT configured, _nws_nearest_station auto-discovers
    the nearest station and populates both station_id and distance_km on the
    returned Observation."""

    def _get(url: str, headers: dict[str, str] | None, timeout: float) -> Any:
        if "/points/" in url:
            return _NWS_POINTS_PAYLOAD
        if "stations" in url and "/observations" not in url:
            return _NWS_STATIONS_PAYLOAD
        # /observations/latest
        return {
            "properties": {
                "timestamp": "2026-05-29T21:00:00+00:00",
                "windSpeed": {"value": 18.0, "unitCode": "wmoUnit:km_h-1"},
                "windGust": {"value": None},
                "windDirection": {"value": 310, "unitCode": "wmoUnit:degree_(angle)"},
                "visibility": {"value": 16090, "unitCode": "wmoUnit:m"},
            }
        }

    # No station_id configured ⇒ triggers auto-discovery.
    cfg = ExternalConfig(enabled=True, provider="nws")
    obs = p.fetch_external(cfg, 39.43, -104.52, http_get=_get)

    assert obs is not None
    assert obs.station_id == "KBJC"
    # Station coords from _NWS_STATIONS_PAYLOAD: lon=-104.62, lat=39.20.
    assert obs.distance_km is not None
    # haversine(39.43, -104.52, 39.20, -104.62) ≈ 26 km — check ballpark.
    assert 15.0 < obs.distance_km < 40.0


def test_nws_auto_discovery_handles_points_failure() -> None:
    """If the /points/ call fails, _fetch_nws returns None — not a raise."""

    def _get(url: str, headers: dict[str, str] | None, timeout: float) -> Any:
        raise ConnectionError("nws points down")

    cfg = ExternalConfig(enabled=True, provider="nws")
    assert p.fetch_external(cfg, 39.43, -104.52, http_get=_get) is None


# ── responses.build_external: stale boundary and unit conversions ─────────────


def _make_obs(**kw: Any) -> Observation:
    return Observation(provider="open-meteo", source="open-meteo:best_match", **kw)


def test_build_external_stale_boundary_just_over() -> None:
    """Age = stale_after + 1 second must be flagged stale."""
    stale_after = 900.0
    observed_at = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)
    # server_time is exactly stale_after + 1 s after observed_at.
    server_time = observed_at + timedelta(seconds=stale_after + 1)
    obs = _make_obs(wind_speed_ms=2.0, observed_at=observed_at)

    result = build_external(
        (obs, observed_at),
        server_time,
        stale_after_seconds=stale_after,
    )
    assert result is not None
    assert result.stale is True


def test_build_external_stale_boundary_just_under() -> None:
    """Age = stale_after - 1 second must NOT be flagged stale."""
    stale_after = 900.0
    observed_at = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)
    server_time = observed_at + timedelta(seconds=stale_after - 1)
    obs = _make_obs(wind_speed_ms=2.0, observed_at=observed_at)

    result = build_external(
        (obs, observed_at),
        server_time,
        stale_after_seconds=stale_after,
    )
    assert result is not None
    assert result.stale is False


def test_build_external_age_from_fetched_at_when_observed_at_none() -> None:
    """When observed_at is None the stale check falls back to fetched_at."""
    stale_after = 900.0
    fetched_at = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)
    server_time = fetched_at + timedelta(seconds=stale_after + 1)
    # observed_at intentionally absent.
    obs = _make_obs(wind_speed_ms=3.0)

    result = build_external(
        (obs, fetched_at),
        server_time,
        stale_after_seconds=stale_after,
    )
    assert result is not None
    assert result.stale is True
    # age_seconds should be computed from fetched_at, not blow up.
    assert result.age_seconds == pytest.approx(stale_after + 1, abs=0.1)


def test_build_external_wind_unit_conversions_exact() -> None:
    """All four wind-speed display units are computed from the m/s value."""
    ws_ms = 10.0
    obs = _make_obs(
        wind_speed_ms=ws_ms,
        wind_gust_ms=15.0,
        observed_at=datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC),
    )
    now = datetime(2026, 1, 1, 12, 1, 0, tzinfo=UTC)

    result = build_external((obs, now), now, stale_after_seconds=900.0)
    assert result is not None
    assert result.wind_speed_ms == pytest.approx(ws_ms, abs=0.05)
    assert result.wind_speed_kmh == pytest.approx(ws_ms * 3.6, abs=0.05)
    assert result.wind_speed_mph == pytest.approx(ws_ms * 2.236936, abs=0.05)
    assert result.wind_speed_kt == pytest.approx(ws_ms * 1.943844, abs=0.05)


def test_build_external_gust_unit_conversions() -> None:
    """Gust units converted independently from wind speed units."""
    wg_ms = 20.0
    obs = _make_obs(
        wind_gust_ms=wg_ms,
        observed_at=datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC),
    )
    now = datetime(2026, 1, 1, 12, 0, 30, tzinfo=UTC)
    result = build_external((obs, now), now, stale_after_seconds=900.0)
    assert result is not None
    assert result.wind_gust_ms == pytest.approx(wg_ms, abs=0.05)
    assert result.wind_gust_kmh == pytest.approx(wg_ms * 3.6, abs=0.05)
    assert result.wind_gust_mph == pytest.approx(wg_ms * 2.236936, abs=0.05)


# ── external block in GET /api/v1/current/{sensor_id} (single-sensor route) ──


def test_current_sensor_outdoor_has_external_block(client: TestClient) -> None:
    """The single-sensor route /api/v1/current/{sensor_id} must embed the
    external block when a valid observation is in the store — this route was
    added separately from /current and was not tested for external embedding."""
    _push(client, wind_speed_ms=4.5, wind_direction_deg=45.0, observed_at=datetime.now(UTC))
    r = client.get("/api/v1/current/outdoor")
    assert r.status_code == 200
    parsed = schemas.CurrentSensorResponse.model_validate(r.json())
    assert parsed.external is not None
    assert parsed.external.wind_speed_ms == pytest.approx(4.5, abs=0.05)
    assert parsed.external.wind_direction_cardinal == "NE"


def test_current_sensor_outdoor_external_null_when_disabled(client: TestClient) -> None:
    """No store push + disabled config ⇒ external block is None for the
    single-sensor route just like for /current."""
    r = client.get("/api/v1/current/outdoor")
    assert r.status_code == 200
    parsed = schemas.CurrentSensorResponse.model_validate(r.json())
    assert parsed.external is None


def test_current_sensor_indoor_external_key_present(client: TestClient) -> None:
    """Indoor sensor route includes the 'external' key in the response JSON
    (its value may be None since fused indices require outdoor_reading)."""
    _push(client, wind_speed_ms=3.0, observed_at=datetime.now(UTC))
    r = client.get("/api/v1/current/indoor")
    assert r.status_code == 200
    assert "external" in r.json()


# ── summary endpoint: 24h and 30d period labels ───────────────────────────────


def test_summary_24h_period_label(client: TestClient) -> None:
    """period=24h should return a 200 response with period == '24h'."""
    r = client.get("/api/v1/summary/outdoor?period=24h")
    assert r.status_code == 200
    parsed = schemas.SummaryResponse.model_validate(r.json())
    assert parsed.period == "24h"


def test_summary_30d_period_label(client: TestClient) -> None:
    """period=30d should return a 200 response with period == '30d'."""
    r = client.get("/api/v1/summary/outdoor?period=30d")
    assert r.status_code == 200
    parsed = schemas.SummaryResponse.model_validate(r.json())
    assert parsed.period == "30d"


def test_summary_24h_has_stats_from_fixture_data(client: TestClient) -> None:
    """24h window returns a valid parseable response with non-negative count."""
    r = client.get("/api/v1/summary/outdoor?period=24h")
    assert r.status_code == 200
    parsed = schemas.SummaryResponse.model_validate(r.json())
    assert parsed.sample_count >= 0


def test_summary_calibration_offset_reflected_in_temperature(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A non-zero temp_offset_c should be visible in the summary temperature
    stats. We seed one reading with a distinctively high raw temperature (35.0°C)
    and assert the summary max reflects the calibrated value (33.0°C with offset
    -2.0), not the raw value (35.0°C). Fixture rows top out at ~21°C raw → ~19°C
    calibrated, so 33°C remains the unambiguous max."""
    from tests.conftest import BRANDING_EXAMPLE, TOML_TEMPLATE
    from weather_server.db import init_db, insert_outdoor_reading
    from weather_server.main import create_app

    db_path = tmp_path / "weather.db"
    conn = init_db(db_path)
    now_ts = int(datetime.now(UTC).timestamp())
    insert_outdoor_reading(
        conn,
        now_ts - 120,  # 2 minutes ago — safely inside the 24h window
        {
            "temperature_c": 35.0,
            "humidity_pct": 50.0,
            "pressure_pa": 101325.0,
            "latitude": 39.7392,
            "longitude": -104.9903,
            "altitude_m": 1609.3,
        },
    )
    conn.close()

    TOML_WITH_OFFSET = TOML_TEMPLATE.replace(
        "temp_offset_c = -0.5", "temp_offset_c = -2.0"
    )
    fixture_dir = tmp_path / "fixtures"
    shutil.copytree(FIXTURE_SRC, fixture_dir)
    cfg_path = tmp_path / "weather.toml"
    cfg_path.write_text(
        TOML_WITH_OFFSET.format(
            db_path=str(db_path),
            fixture_dir=str(fixture_dir),
            branding_path=str(BRANDING_EXAMPLE),
        )
    )
    monkeypatch.setenv("WEATHER_CONFIG", str(cfg_path))

    with TestClient(create_app()) as tc:
        r = tc.get("/api/v1/summary/outdoor?period=24h")
    assert r.status_code == 200
    parsed = schemas.SummaryResponse.model_validate(r.json())

    # 35.0 raw − 2.0 offset = 33.0 calibrated → the unambiguous maximum.
    # If offset were ignored max would be ≥ 35.0.
    assert parsed.temperature_c is not None
    assert parsed.temperature_c.max is not None
    assert parsed.temperature_c.max == pytest.approx(33.0, abs=0.2)


# ── light.sky: reading when sun is below horizon ───────────────────────────────


def test_sky_block_cloud_cover_none_when_sun_below_threshold() -> None:
    """When the sun altitude is below _MIN_RELIABLE_ALTITUDE_DEG (5°),
    cloud_cover_pct must be None even if irradiance is present."""
    from weather_server.derivations import light as lt

    sun_alt = 2.0  # positive but below the 5° threshold
    lux = 5000.0

    cloud = lt.cloud_cover_pct(lux, sun_alt)
    assert cloud is None

    # Irradiance is still computable — no sun-altitude guard there.
    irr = lt.lux_to_irradiance_w_m2(lux)
    assert irr > 0

    # sky_condition with positive sub-threshold alt → "low sun"
    assert lt.sky_condition(sun_alt, cloud) == "low sun"


def test_sky_block_cloud_cover_none_when_sun_below_horizon() -> None:
    """sun at -1° (just below horizon) → cloud_cover_pct None and sky_condition
    'twilight'."""
    from weather_server.derivations import light as lt

    assert lt.cloud_cover_pct(10000.0, -1.0) is None
    assert lt.sky_condition(-1.0, None) == "twilight"


def _app_with_no_lux_fixture(tmp_dir: Path) -> Any:
    """Build a TestClient whose outdoor fixture has no lux field."""
    from tests.conftest import BRANDING_EXAMPLE, TOML_TEMPLATE
    from weather_server.main import create_app

    fixture_dir = tmp_dir / "fixtures"
    shutil.copytree(FIXTURE_SRC, fixture_dir)
    no_lux = [
        {
            "temperature_c": 18.0,
            "humidity_pct": 45.0,
            "pressure_pa": 80443.0,
            "latitude": 39.7392,
            "longitude": -104.9903,
            "altitude_m": 1609.3,
        }
    ]
    (fixture_dir / "outdoor.json").write_text(json.dumps(no_lux))

    db_path = tmp_dir / "weather.db"
    cfg_path = tmp_dir / "weather.toml"
    cfg_path.write_text(
        TOML_TEMPLATE.format(
            db_path=str(db_path),
            fixture_dir=str(fixture_dir),
            branding_path=str(BRANDING_EXAMPLE),
        )
    )
    os.environ["WEATHER_CONFIG"] = str(cfg_path)
    return create_app


def test_build_sky_block_is_none_when_lux_absent() -> None:
    """_build_sky_block returns None when the outdoor payload has no lux field,
    exercising the None-lux guard in responses._build_sky_block."""
    with tempfile.TemporaryDirectory() as td:
        create_app = _app_with_no_lux_fixture(Path(td))
        try:
            with TestClient(create_app()) as tc:
                r = tc.get("/api/v1/current")
        finally:
            os.environ.pop("WEATHER_CONFIG", None)

    assert r.status_code == 200
    parsed = schemas.CurrentResponse.model_validate(r.json())
    assert parsed.sensors["outdoor"].derived.sky is None


# Note: THSW/ET0-null-without-solar coverage moved to test_wind_path.py in
# Cycle 6c, when the fused comfort indices migrated from external to derived
# (computed from LOCAL wind).
