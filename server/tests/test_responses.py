"""Unit tests for builders in responses.py that aren't naturally covered
by the endpoint tests — chiefly the astronomy reference-location
resolver's fallback paths.
"""

from datetime import UTC, datetime

from weather_server.config import load_config_from_dict
from weather_server.responses import build_astronomy


def _config(outdoor: dict) -> object:
    return load_config_from_dict(
        {
            "sensors": [outdoor],
        }
    )


def test_astronomy_uses_outdoor_sensor_gps_when_present() -> None:
    config = _config(
        {
            "id": "outdoor",
            "role": "outdoor",
            "ip": "1.1.1.1",
            "has_gps": True,
            "fallback_lat": 0.0,
            "fallback_lon": 0.0,
        }
    )
    server_time = datetime(2026, 6, 21, 19, 0, 0, tzinfo=UTC)

    # Build a SensorReading-like object with a real GPS location.
    from weather_server.schemas import (
        LocationBlock,
        SensorReading,
    )

    reading = SensorReading(
        sensor_id="outdoor",
        role="outdoor",
        online=True,
        location=LocationBlock(lat=39.7392, lon=-104.9903),
    )
    a = build_astronomy(server_time, config, reading)  # type: ignore[arg-type]
    assert a.reference_location.source == "outdoor_sensor"
    assert a.reference_location.lat == 39.7392
    assert a.timezone == "America/Denver"


def test_astronomy_falls_back_to_config_when_no_gps_fix() -> None:
    config = _config(
        {
            "id": "outdoor",
            "role": "outdoor",
            "ip": "1.1.1.1",
            "has_gps": True,
            "fallback_lat": 51.5074,
            "fallback_lon": -0.1278,
        }
    )
    server_time = datetime(2026, 6, 21, 12, 0, 0, tzinfo=UTC)
    a = build_astronomy(server_time, config, outdoor_reading=None)  # type: ignore[arg-type]
    assert a.reference_location.source == "config_default"
    assert a.reference_location.lat == 51.5074
    assert a.reference_location.lon == -0.1278
    assert a.timezone == "Europe/London"
    # Astronomy populated — sun position exists.
    assert a.sun.altitude_deg is not None


def test_astronomy_last_resort_when_no_gps_and_no_fallback() -> None:
    config = _config(
        {
            "id": "outdoor",
            "role": "outdoor",
            "ip": "1.1.1.1",
            "has_gps": True,
        }
    )
    server_time = datetime(2026, 6, 21, 12, 0, 0, tzinfo=UTC)
    a = build_astronomy(server_time, config, outdoor_reading=None)  # type: ignore[arg-type]
    assert a.reference_location.source == "config_default"
    assert a.reference_location.lat is None
    assert a.reference_location.lon is None
    assert a.timezone == "UTC"
    assert a.sun.altitude_deg is None


def test_astronomy_query_override_takes_precedence() -> None:
    config = _config(
        {
            "id": "outdoor",
            "role": "outdoor",
            "ip": "1.1.1.1",
            "has_gps": True,
            "fallback_lat": 0.0,
            "fallback_lon": 0.0,
        }
    )
    server_time = datetime(2026, 6, 21, 12, 0, 0, tzinfo=UTC)
    a = build_astronomy(
        server_time,
        config,  # type: ignore[arg-type]
        outdoor_reading=None,
        lat_override=35.6762,
        lon_override=139.6503,
    )
    assert a.reference_location.source == "query_override"
    assert a.timezone == "Asia/Tokyo"


def test_sun_and_moon_events_are_emitted_in_resolved_local_zone() -> None:
    """Per 02-api-design.md line 49: sunrise/sunset/etc. must be in the
    resolved local timezone, not UTC. Both clients depended on this and
    were doing the conversion themselves until Phase 4.5; lock it in
    here so a server-side regression can't quietly re-introduce drift."""
    config = _config(
        {
            "id": "outdoor",
            "role": "outdoor",
            "ip": "1.1.1.1",
            "has_gps": True,
            "fallback_lat": 0.0,
            "fallback_lon": 0.0,
        }
    )
    server_time = datetime(2026, 6, 21, 12, 0, 0, tzinfo=UTC)
    a = build_astronomy(
        server_time,
        config,  # type: ignore[arg-type]
        outdoor_reading=None,
        lat_override=39.7392,
        lon_override=-104.9903,
    )
    assert a.timezone == "America/Denver"
    # America/Denver is UTC-06:00 in late June (MDT). Every sun/moon
    # event timestamp should carry that offset, not Z/+00:00.
    sun_events = [a.sun.sunrise, a.sun.sunset, a.sun.solar_noon, a.sun.dawn, a.sun.dusk]
    moon_events = [a.moon.moonrise, a.moon.moonset]
    for dt in sun_events + moon_events:
        if dt is None:
            continue
        assert dt.utcoffset() is not None
        # MDT = -6h; allow either MDT or MST in case of fixture edge cases.
        assert dt.utcoffset().total_seconds() in (-6 * 3600, -7 * 3600), (
            f"event {dt.isoformat()} not projected into America/Denver"
        )
