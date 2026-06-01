"""External block: endpoint shape, embedding in /current, and the
offline-acceptance guarantee (disabled ⇒ block null, everything else intact)."""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi.testclient import TestClient

from weather_server import schemas
from weather_server.external.providers import Observation


def _push(client: TestClient, **kw: object) -> None:
    obs = Observation(provider="open-meteo", source="open-meteo:best_match", **kw)
    client.app.state.external_store.set(obs, datetime.now(UTC))


# ── offline acceptance (the locked requirement) ──────────────────────────────


def test_external_null_when_disabled(client: TestClient) -> None:
    """conftest config has no [external] section ⇒ disabled ⇒ block is null."""
    r = client.get("/api/v1/external")
    assert r.status_code == 200
    parsed = schemas.ExternalResponse.model_validate(r.json())
    assert parsed.external is None


def test_current_has_external_null_when_disabled(client: TestClient) -> None:
    r = client.get("/api/v1/current")
    assert r.status_code == 200
    body = r.json()
    assert "external" in body
    assert body["external"] is None


def test_current_derived_and_astronomy_intact_when_external_disabled(client: TestClient) -> None:
    """Offline acceptance: the always-present blocks must be unaffected by the
    external feed being off."""
    parsed = schemas.CurrentResponse.model_validate(client.get("/api/v1/current").json())
    outdoor = parsed.sensors["outdoor"]
    assert outdoor.derived.pressure_station_hpa is not None
    assert outdoor.derived.pressure_sealevel_hpa is not None
    assert parsed.astronomy.sun is not None
    assert parsed.external is None


# ── populated block ───────────────────────────────────────────────────────────


def test_external_block_maps_observation(client: TestClient) -> None:
    _push(
        client,
        wind_speed_ms=5.0,
        wind_gust_ms=8.0,
        wind_direction_deg=90.0,
        cloud_cover_pct=42.0,
        uv_index=3.0,
        visibility_m=20000.0,
        station_id="KBJC",
        distance_km=13.0,
        observed_at=datetime.now(UTC),
    )
    parsed = schemas.ExternalResponse.model_validate(client.get("/api/v1/external").json())
    ext = parsed.external
    assert ext is not None
    assert ext.wind_speed_ms == 5.0
    assert ext.wind_speed_kmh == 18.0  # 5 * 3.6
    assert ext.wind_speed_mph == round(5.0 * 2.236936, 1)
    assert ext.wind_direction_cardinal == "E"
    assert ext.cloud_cover_pct == 42.0
    assert ext.visibility_km == 20.0
    assert ext.station_id == "KBJC"
    assert ext.stale is False


def test_external_embedded_in_current(client: TestClient) -> None:
    _push(client, wind_speed_ms=3.0, wind_direction_deg=180.0, observed_at=datetime.now(UTC))
    parsed = schemas.CurrentResponse.model_validate(client.get("/api/v1/current").json())
    assert parsed.external is not None
    assert parsed.external.wind_direction_cardinal == "S"


def test_external_stale_flag_for_old_observation(client: TestClient) -> None:
    old = datetime(2020, 1, 1, tzinfo=UTC)
    _push(client, wind_speed_ms=2.0, observed_at=old)
    parsed = schemas.ExternalResponse.model_validate(client.get("/api/v1/external").json())
    assert parsed.external is not None
    assert parsed.external.stale is True


def test_fused_indices_present_with_wind_and_outdoor(client: TestClient) -> None:
    # Wind present + outdoor reading (temp/humidity/lux from fixture) ⇒ fused.
    _push(client, wind_speed_ms=6.0, wind_direction_deg=270.0, observed_at=datetime.now(UTC))
    parsed = schemas.CurrentResponse.model_validate(client.get("/api/v1/current").json())
    ext = parsed.external
    assert ext is not None
    assert ext.beaufort_force is not None
    assert ext.beaufort_description is not None
    assert ext.apparent_temperature_c is not None
    assert ext.wind_chill_c is not None
    # Fixture lux is positive ⇒ solar estimate ⇒ THSW and ET0 computed.
    assert ext.thsw_index_c is not None
    assert ext.et0_mm_hour is not None


def test_fused_indices_absent_without_wind(client: TestClient) -> None:
    _push(client, cloud_cover_pct=50.0, observed_at=datetime.now(UTC))  # no wind
    parsed = schemas.CurrentResponse.model_validate(client.get("/api/v1/current").json())
    ext = parsed.external
    assert ext is not None
    assert ext.cloud_cover_pct == 50.0
    assert ext.beaufort_force is None
    assert ext.apparent_temperature_c is None
    assert ext.wind_chill_c is None


def test_openapi_includes_external_path(client: TestClient) -> None:
    schema = client.get("/openapi.json").json()
    assert "/api/v1/external" in schema["paths"]
