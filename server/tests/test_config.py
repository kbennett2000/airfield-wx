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
