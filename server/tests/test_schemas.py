"""Verify the Pydantic models accept the exact shapes documented in
02-api-design.md. If these tests break, either the spec changed or a model
drifted — both want a human eye."""

from datetime import UTC, datetime

import pytest

from weather_server import schemas


def test_sensor_reading_full_outdoor_shape() -> None:
    payload = {
        "sensor_id": "outdoor",
        "role": "outdoor",
        "online": True,
        "reading_timestamp": "2026-05-22T15:30:00Z",
        "age_seconds": 28,
        "raw": {
            "temperature_c": 18.9,
            "humidity_pct": 42.1,
            "pressure_pa": 80443,
            "lux": 12450.0,
            "ir": 230,
            "visible": 8200,
            "full": 8430,
        },
        "calibration": {"temp_offset_c": -0.5},
        "derived": {
            "temperature_c": 18.4,
            "temperature_f": 65.1,
            "dewpoint_c": 5.1,
            "dewpoint_f": 41.2,
            "absolute_humidity_g_m3": 6.7,
            "pressure_station_hpa": 804.43,
            "pressure_station_inhg": 23.75,
            "pressure_sealevel_hpa": 1023.0,
            "pressure_sealevel_inhg": 30.21,
        },
        "location": {
            "lat": 39.7392,
            "lon": -104.9903,
            "altitude_m": 1609.3,
            "altitude_ft": 5279.9,
            "satellites": 9,
            "speed_kmh": 0.0,
            "course_deg": 0.0,
            "dms": '39°44\'21.1"N  104°59\'25.1"W',
            "maidenhead": "DM79mr",
        },
        "device": {
            "rssi_dbm": -62,
            "uptime_s": 84320,
            "free_heap_bytes": 178432,
        },
    }
    sr = schemas.SensorReading.model_validate(payload)
    assert sr.derived.pressure_sealevel_inhg == pytest.approx(30.21)
    assert sr.location is not None and sr.location.maidenhead == "DM79mr"


def test_sensor_reading_without_gps_has_no_location_or_light() -> None:
    # A non-GPS station omits the location block and carries no light.
    payload = {
        "sensor_id": "aux",
        "role": "aux",
        "online": True,
        "reading_timestamp": "2026-05-22T15:30:16Z",
        "age_seconds": 12,
        "raw": {"temperature_c": 22.4, "humidity_pct": 38.7, "pressure_pa": 80520},
        "derived": {"temperature_c": 22.4, "temperature_f": 72.32},
    }
    sr = schemas.SensorReading.model_validate(payload)
    assert sr.location is None
    assert sr.raw.lux is None


def test_extra_field_in_strict_model_rejected() -> None:
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        schemas.SensorReading.model_validate(
            {
                "sensor_id": "x",
                "role": "outdoor",
                "online": True,
                "raw": {"bogus_field": 1},
            }
        )


def test_history_response_uses_from_alias() -> None:
    payload = {
        "sensor_id": "outdoor",
        "from": "2026-05-21T15:30:00Z",
        "to": "2026-05-22T15:30:00Z",
        "bucket_seconds": 300,
        "row_count": 1,
        "rows": [
            {
                "timestamp": "2026-05-21T15:30:00Z",
                "temperature_c": 18.4,
                "temperature_f": 65.1,
                "humidity_pct": 42.1,
                "pressure_sealevel_hpa": 1023.0,
                "pressure_station_hpa": 804.4,
                "dewpoint_c": 5.1,
            }
        ],
    }
    resp = schemas.HistoryResponse.model_validate(payload)
    assert resp.from_.year == 2026
    serialized = resp.model_dump(by_alias=True, mode="json")
    assert "from" in serialized
    assert "from_" not in serialized
    assert "lux" not in serialized["rows"][0]


def test_astronomy_full_shape() -> None:
    payload = {
        "server_time": "2026-05-22T15:30:28Z",
        "local_time": "2026-05-22T09:30:28-06:00",
        "timezone": "America/Denver",
        "reference_location": {
            "lat": 39.7392,
            "lon": -104.9903,
            "source": "outdoor_sensor",
        },
        "sun": {
            "altitude_deg": 42.3,
            "azimuth_deg": 156.7,
            "is_daytime": True,
            "sunrise": "2026-05-22T05:38:00-06:00",
            "sunset": "2026-05-22T20:15:00-06:00",
            "solar_noon": "2026-05-22T12:56:00-06:00",
            "dawn": "2026-05-22T05:08:00-06:00",
            "dusk": "2026-05-22T20:45:00-06:00",
            "day_length_seconds": 52620,
            "seconds_to_sunset": 38612,
            "seconds_to_sunrise": None,
        },
        "moon": {
            "altitude_deg": -23.1,
            "azimuth_deg": 87.0,
            "distance_km": 384720,
            "illumination_pct": 73.4,
            "phase_name": "Waxing Gibbous",
            "phase_icon": "🌔",
            "moonrise": "2026-05-22T14:23:00-06:00",
            "moonset": "2026-05-23T03:45:00-06:00",
            "always_up": False,
            "always_down": False,
        },
    }
    a = schemas.Astronomy.model_validate(payload)
    assert a.timezone == "America/Denver"
    assert a.sun.day_length_seconds == 52620
    assert a.moon.phase_icon == "🌔"


def test_health_response_shape() -> None:
    payload = {
        "ok": True,
        "server_time": datetime(2026, 5, 22, 15, 30, 28, tzinfo=UTC),
        "db_reachable": True,
        "sensors": [
            {"sensor_id": "outdoor", "online": True, "age_seconds": 28},
            {"sensor_id": "aux", "online": True, "age_seconds": 12},
            {"sensor_id": "aux2", "online": False, "age_seconds": 1828},
        ],
        "loggers": [
            {"sensor_id": "outdoor", "last_write_seconds_ago": 28, "ok": True},
        ],
    }
    h = schemas.HealthResponse.model_validate(payload)
    assert h.ok is True
    assert len(h.loggers) == 1


def test_error_response_shape() -> None:
    payload = {
        "error": {
            "code": "sensor_not_found",
            "message": "No sensor with id 'kitchen' is registered.",
            "details": {},
        }
    }
    e = schemas.ErrorResponse.model_validate(payload)
    assert e.error.code == "sensor_not_found"
