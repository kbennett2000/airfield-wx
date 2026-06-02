from pathlib import Path

import pytest

from weather_server.config import Config, load_config, load_config_from_dict


def test_load_example_config() -> None:
    example = Path(__file__).parent.parent / "weather.toml.example"
    config = load_config(example)
    assert isinstance(config, Config)
    assert config.server.port == 8005
    assert config.logger.interval_seconds == 60
    assert config.cache.ttl_seconds == 5
    assert config.development.fixture_dir == "fixtures"
    assert config.fixture_mode is True
    ids = [s.id for s in config.sensors]
    assert ids == ["outdoor"]


def test_sensor_by_id_and_outdoor() -> None:
    config = load_config(Path(__file__).parent.parent / "weather.toml.example")
    assert config.sensor_by_id("outdoor") is not None
    assert config.sensor_by_id("kitchen") is None
    assert config.outdoor is not None
    assert config.outdoor.has_gps is True
    assert config.outdoor.fallback_altitude_m == pytest.approx(1609.3)
    # Coordinates are redacted in the public example; just verify they parse as
    # in-range floats rather than pinning a specific location.
    assert config.outdoor.fallback_lat is not None
    assert -90.0 <= config.outdoor.fallback_lat <= 90.0
    assert config.outdoor.fallback_lon is not None
    assert -180.0 <= config.outdoor.fallback_lon <= 180.0


def test_fixture_mode_false_when_development_block_absent() -> None:
    config = load_config_from_dict(
        {
            "server": {"port": 8005},
            "sensors": [{"id": "outdoor", "role": "outdoor", "ip": "10.0.0.1"}],
        }
    )
    assert config.fixture_mode is False
    assert config.development.fixture_dir is None


def test_requires_at_least_one_sensor() -> None:
    with pytest.raises(ValueError, match="at least one"):
        load_config_from_dict({"server": {"port": 8005}})


def test_rejects_duplicate_sensor_ids() -> None:
    with pytest.raises(ValueError, match="duplicate"):
        load_config_from_dict(
            {
                "sensors": [
                    {"id": "x", "role": "outdoor", "ip": "1.1.1.1"},
                    {"id": "x", "role": "aux", "ip": "1.1.1.2"},
                ]
            }
        )


# ── [wind] block (flexible anemometer topology, ADR-0006) ──────────────────


def test_wind_defaults_to_outdoor_source() -> None:
    config = load_config_from_dict(
        {"sensors": [{"id": "outdoor", "role": "outdoor", "ip": "1.1.1.1"}]}
    )
    assert config.wind.source == "outdoor"
    assert config.wind.max_age_s == pytest.approx(90.0)


def test_wind_block_parsed() -> None:
    config = load_config_from_dict(
        {
            "wind": {"source": "windy", "max_age_s": 120},
            "logging": {"enabled": True},  # a separate station requires logging (Cycle 14)
            "sensors": [
                {"id": "outdoor", "role": "outdoor", "ip": "1.1.1.1"},
                {"id": "windy", "role": "wind_station", "ip": "1.1.1.2", "has_wind": True},
            ],
        }
    )
    assert config.wind.source == "windy"
    assert config.wind.max_age_s == pytest.approx(120.0)


def test_wind_source_naming_unknown_sensor_raises() -> None:
    with pytest.raises(ValueError, match="no configured sensor"):
        load_config_from_dict(
            {
                "wind": {"source": "ghost"},
                "sensors": [{"id": "outdoor", "role": "outdoor", "ip": "1.1.1.1"}],
            }
        )


# ── separate wind station (topology 3) requires logging enabled (Cycle 14) ──


def _station_config(*, logging_enabled: bool) -> dict:
    cfg: dict = {
        "wind": {"source": "windy"},
        "sensors": [
            {"id": "outdoor", "role": "outdoor", "ip": "1.1.1.1"},
            {"id": "windy", "role": "wind_station", "ip": "1.1.1.2", "has_wind": True},
        ],
    }
    if logging_enabled:
        cfg["logging"] = {"enabled": True}
    return cfg


def test_separate_wind_station_without_logging_raises() -> None:
    # A topology-3 station's wind comes only from the wind_readings log, so with
    # logging off it would silently resolve no wind — refuse to start instead.
    with pytest.raises(ValueError) as exc:
        load_config_from_dict(_station_config(logging_enabled=False))
    msg = str(exc.value)
    assert "requires history logging" in msg  # names the problem
    assert "wind_readings" in msg
    assert "[logging] enabled = true" in msg  # remedy 1
    assert "outdoor" in msg  # remedy 2


def test_separate_wind_station_with_logging_ok() -> None:
    config = load_config_from_dict(_station_config(logging_enabled=True))
    assert config.wind.source == "windy"
    assert config.logging.enabled is True


def test_outdoor_wind_logging_off_ok() -> None:
    # The default stateless appliance must start fine.
    config = load_config_from_dict(
        {"sensors": [{"id": "outdoor", "role": "outdoor", "ip": "1.1.1.1"}]}
    )
    assert config.wind.source == "outdoor"
    assert config.logging.enabled is False


def test_outdoor_wind_logging_on_ok() -> None:
    config = load_config_from_dict(
        {
            "logging": {"enabled": True},
            "sensors": [{"id": "outdoor", "role": "outdoor", "ip": "1.1.1.1"}],
        }
    )
    assert config.wind.source == "outdoor"
    assert config.logging.enabled is True
