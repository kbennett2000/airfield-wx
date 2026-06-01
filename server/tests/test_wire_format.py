"""Wire-format parser tests, including BUG-08 nan-token handling.

The sample fixtures under server/fixtures/wire_samples/ exist so this
test survives sketch changes — capture a new file and add a case."""

from __future__ import annotations

from pathlib import Path

import pytest

from weather_server import wire_format

SAMPLES = Path(__file__).parent.parent / "fixtures" / "wire_samples"


def _read(name: str) -> str:
    return (SAMPLES / name).read_text()


def test_sanitize_nan_token_simple() -> None:
    assert wire_format.sanitize_nan_tokens('{"x": nan}') == '{"x": null}'
    assert wire_format.sanitize_nan_tokens('{"x": NaN}') == '{"x": null}'
    assert wire_format.sanitize_nan_tokens('{"x": undefined}') == '{"x": null}'


def test_sanitize_nan_does_not_eat_words_containing_nan() -> None:
    # Make sure we don't replace the "nan" in "banana" or in string literals.
    text = '{"name": "banana", "x": nan, "key_nan_y": nan}'
    out = wire_format.sanitize_nan_tokens(text)
    assert '"banana"' in out
    assert '"key_nan_y": null' in out
    assert '"x": null' in out


def test_parse_outdoor_happy_sample() -> None:
    payload = wire_format.parse_outdoor(_read("outdoor_happy.json"))
    assert payload is not None
    assert payload["temperature_c"] == pytest.approx(18.94)
    assert payload["humidity_pct"] == pytest.approx(42.13)
    # Wire hPa → payload Pa (×100)
    assert payload["pressure_pa"] == pytest.approx(80443.0)
    # Wire uptime ms → payload uptime_s (//1000)
    assert payload["uptime_s"] == 84320
    # full_spectrum derived from visible + ir
    assert payload["full_spectrum"] == 230 + 8200
    assert payload["altitude_m"] == pytest.approx(1609.3)
    assert payload["rssi_dbm"] == -62
    # tempOffset is intentionally ignored — server applies calibration now.
    assert "temp_offset_c" not in payload


def test_parse_outdoor_nan_sample_drops_nan_fields() -> None:
    payload = wire_format.parse_outdoor(_read("outdoor_nan.json"))
    assert payload is not None
    # Still-valid fields survive.
    assert payload["temperature_c"] == pytest.approx(18.94)
    assert payload["humidity_pct"] == pytest.approx(42.13)
    assert payload["pressure_pa"] == pytest.approx(80443.0)
    # Fields with nan are absent from the payload.
    for nan_field in ("lux", "ir", "visible", "latitude", "longitude", "altitude_m"):
        assert nan_field not in payload, f"{nan_field} should be dropped when wire value is nan"
    # full_spectrum not derivable because ir/visible are both missing.
    assert "full_spectrum" not in payload


def test_parse_outdoor_error_envelope_returns_none() -> None:
    assert wire_format.parse_outdoor(_read("outdoor_error.json")) is None


def test_parse_indoor_happy_sample() -> None:
    payload = wire_format.parse_indoor(_read("indoor_happy.json"))
    assert payload is not None
    assert payload["temperature_c"] == pytest.approx(22.41)
    assert payload["pressure_pa"] == pytest.approx(80520.0)
    # Indoor wire format has no GPS or device telemetry fields.
    assert "latitude" not in payload


def test_parse_indoor_error_envelope() -> None:
    assert wire_format.parse_indoor('{"error":"sensor failure"}') is None


def test_parse_dispatch_by_role() -> None:
    out = wire_format.parse(_read("outdoor_happy.json"), "outdoor")
    in_ = wire_format.parse(_read("indoor_happy.json"), "indoor")
    assert out is not None and "altitude_m" in out
    assert in_ is not None and "altitude_m" not in in_


def test_parse_malformed_json_returns_none() -> None:
    assert wire_format.parse_outdoor("{not json}") is None
    assert wire_format.parse_outdoor("") is None


def test_parse_non_object_json_returns_none() -> None:
    assert wire_format.parse_outdoor("[1, 2, 3]") is None


def test_parse_outdoor_with_inf_drops_field() -> None:
    # Infinity isn't valid JSON but sanity-check the cleaners.
    payload = wire_format.parse_outdoor(
        '{"temperatureC": 20.0, "humidity": Infinity, "pressure": 800.0}'
    )
    assert payload is not None
    assert "humidity_pct" not in payload  # Infinity → dropped
    assert payload["temperature_c"] == pytest.approx(20.0)


def test_partial_outdoor_payload_is_acceptable() -> None:
    # Only the BME280 succeeded; TSL2591 missing entirely.
    payload = wire_format.parse_outdoor(
        '{"temperatureC": 20.0, "humidity": 50.0, "pressure": 800.0}'
    )
    assert payload is not None
    assert payload["temperature_c"] == pytest.approx(20.0)
    assert "lux" not in payload
