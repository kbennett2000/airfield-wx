"""Parsing + validation of the optional [external] config section."""

from __future__ import annotations

import pytest

from weather_server.config import load_config_from_dict

_SENSORS = [{"id": "outdoor", "role": "outdoor", "ip": "10.0.0.1"}]


def _load(external: dict | None) -> object:
    raw: dict = {"sensors": _SENSORS}
    if external is not None:
        raw["external"] = external
    return load_config_from_dict(raw)


def test_external_defaults_disabled_when_absent() -> None:
    cfg = _load(None)
    assert cfg.external.enabled is False
    assert cfg.external.provider == "open-meteo"
    assert "wind" in cfg.external.fetch


def test_external_fetch_list_coerced_to_tuple() -> None:
    cfg = _load({"enabled": True, "fetch": ["wind", "cloud"]})
    assert cfg.external.fetch == ("wind", "cloud")


def test_external_unknown_provider_rejected() -> None:
    with pytest.raises(ValueError, match="provider must be one of"):
        _load({"provider": "accuweather"})


def test_wunderground_requires_station_and_key() -> None:
    with pytest.raises(ValueError, match="requires both station_id and api_key"):
        _load({"enabled": True, "provider": "wunderground", "station_id": "KX"})


def test_wunderground_ok_with_station_and_key() -> None:
    cfg = _load(
        {"enabled": True, "provider": "wunderground", "station_id": "KX", "api_key": "k"}
    )
    assert cfg.external.provider == "wunderground"


def test_wunderground_missing_key_allowed_when_disabled() -> None:
    # Validation only fires when enabled — a half-filled disabled block is fine.
    cfg = _load({"enabled": False, "provider": "wunderground"})
    assert cfg.external.enabled is False
