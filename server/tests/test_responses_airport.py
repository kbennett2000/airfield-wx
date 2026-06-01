"""Airport block composition + the /api/v1/airport and /current wiring.

runway_solution reads the LOCAL derived wind, which the anemometer adds in
Cycle 6; until then it's null in the live API. These tests inject a synthetic
wind to exercise the composer path.
"""

from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from weather_server import responses
from weather_server.config import Config, load_config_from_dict


def _config(**airport: object) -> Config:
    return load_config_from_dict(
        {
            "sensors": [
                {"id": "outdoor", "role": "outdoor", "ip": "1.2.3.4", "has_gps": True},
            ],
            "airport": airport,
        }
    )


WHEN = datetime(2026, 6, 1)


# ── _local_wind: m/s -> kt and the Cycle 6 field contract ──────────────────


def test_local_wind_converts_ms_to_kt() -> None:
    reading = SimpleNamespace(
        derived=SimpleNamespace(wind_direction_true_deg=270.0),
        raw=SimpleNamespace(wind_speed_ms=5.0),
    )
    direction, speed_kt = responses._local_wind(reading)  # type: ignore[arg-type]
    assert direction == pytest.approx(270.0)
    assert speed_kt == pytest.approx(5.0 * responses.MS_TO_KT)


def test_local_wind_absent_today() -> None:
    # A real reading has no wind fields yet (Cycle 6) -> (None, None).
    reading = SimpleNamespace(derived=SimpleNamespace(), raw=SimpleNamespace())
    assert responses._local_wind(reading) == (None, None)  # type: ignore[arg-type]


# ── build_airport composer ─────────────────────────────────────────────────


def test_build_airport_override_with_injected_wind(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(responses, "_local_wind", lambda _r: (270.0, 10.0))
    config = _config(ident="KSEA", crosswind_limit_kt=15)
    air = responses.build_airport(WHEN, config, None)
    assert air is not None
    assert air.ident == "KSEA"
    assert air.source == "config_override"
    assert air.runways  # KSEA has runways
    assert air.runway_solution is not None
    assert air.runway_solution.favored is not None
    assert air.runway_solution.ends


def test_build_airport_null_solution_without_wind() -> None:
    config = _config(ident="KSEA")
    air = responses.build_airport(WHEN, config, None)
    assert air is not None
    assert air.ident == "KSEA"
    assert air.runways
    assert air.runway_solution is None  # no local wind


def test_build_airport_none_when_unresolvable() -> None:
    # No GPS reading, empty airport config -> nothing resolves.
    config = _config()
    assert responses.build_airport(WHEN, config, None) is None


# ── endpoints ──────────────────────────────────────────────────────────────


def test_current_includes_airport_block(client: TestClient) -> None:
    body = client.get("/api/v1/current").json()
    air = body["airport"]
    assert air is not None
    assert isinstance(air["ident"], str)
    assert air["source"] == "gps_nearest"
    assert air["magnetic_model"] == "WMM2025"
    # No anemometer yet -> no runway solution.
    assert air["runway_solution"] is None


def test_airport_endpoint_returns_block(client: TestClient) -> None:
    r = client.get("/api/v1/airport")
    assert r.status_code == 200
    body = r.json()
    assert "airport" in body
    assert body["airport"] is not None
    assert isinstance(body["airport"]["ident"], str)


def test_airport_endpoint_lat_lon_override(client: TestClient) -> None:
    # Override to a point next to KSEA; expect a nearby field resolves.
    r = client.get("/api/v1/airport", params={"lat": 47.447943, "lon": -122.310276})
    assert r.status_code == 200
    air = r.json()["airport"]
    assert air is not None
    assert air["source"] == "gps_nearest"
    assert air["distance_nm"] is not None
