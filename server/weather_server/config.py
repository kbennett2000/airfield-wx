"""TOML config loading for the weather server.

Loads weather.toml into typed dataclasses. No defaults are inferred from the
environment — every value that the rest of the code reads is either present
in the TOML file (with its example default visible in weather.toml.example)
or has an explicit fallback documented here.
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class ServerConfig:
    host: str = "0.0.0.0"
    port: int = 8005
    db_path: str = "weather.db"
    dashboard_dir: str = "../dashboard"
    branding_path: str = "../branding.toml"


@dataclass(frozen=True)
class LoggerConfig:
    interval_seconds: int = 60
    http_timeout_seconds: int = 10


@dataclass(frozen=True)
class CacheConfig:
    ttl_seconds: int = 5


@dataclass(frozen=True)
class DevelopmentConfig:
    fixture_dir: str | None = None


# Optional internet-sourced regional conditions (wind etc.). Disabled by
# default: when the [external] section is absent the fetch task never
# spawns and the server behaves exactly as an offline-only deployment.
_EXTERNAL_PROVIDERS = ("open-meteo", "nws", "wunderground")
_EXTERNAL_FETCH_DEFAULT = ("wind", "cloud", "uv", "precip", "visibility")


@dataclass(frozen=True)
class ExternalConfig:
    enabled: bool = False
    provider: str = "open-meteo"
    refresh_interval_seconds: int = 300
    http_timeout_seconds: int = 8
    fetch: tuple[str, ...] = _EXTERNAL_FETCH_DEFAULT
    cross_check: bool = False
    station_id: str | None = None
    api_key: str | None = None
    # Override the reference coordinates. When unset, the outdoor sensor's
    # GPS (or its configured fallback_lat/lon) is used.
    lat_override: float | None = None
    lon_override: float | None = None


@dataclass(frozen=True)
class SensorConfig:
    id: str
    role: str
    ip: str
    has_gps: bool = False
    has_light: bool = False
    online_threshold_seconds: int = 120
    temp_offset_c: float = 0.0
    fallback_altitude_m: float | None = None
    fallback_lat: float | None = None
    fallback_lon: float | None = None


@dataclass(frozen=True)
class Config:
    server: ServerConfig
    logger: LoggerConfig
    cache: CacheConfig
    development: DevelopmentConfig
    external: ExternalConfig = field(default_factory=ExternalConfig)
    sensors: list[SensorConfig] = field(default_factory=list)

    def sensor_by_id(self, sensor_id: str) -> SensorConfig | None:
        for s in self.sensors:
            if s.id == sensor_id:
                return s
        return None

    @property
    def outdoor(self) -> SensorConfig | None:
        for s in self.sensors:
            if s.role == "outdoor":
                return s
        return None

    @property
    def fixture_mode(self) -> bool:
        return self.development.fixture_dir is not None


def load_config(path: str | Path) -> Config:
    """Read a TOML file and return a fully-populated Config."""
    path = Path(path)
    with path.open("rb") as f:
        raw: dict[str, Any] = tomllib.load(f)
    return _parse(raw)


def load_config_from_dict(raw: dict[str, Any]) -> Config:
    """Useful in tests — bypass the TOML parser."""
    return _parse(raw)


def _parse(raw: dict[str, Any]) -> Config:
    server = ServerConfig(**raw.get("server", {}))
    logger = LoggerConfig(**raw.get("logger", {}))
    cache = CacheConfig(**raw.get("cache", {}))
    development = DevelopmentConfig(**raw.get("development", {}))
    external = _parse_external(raw.get("external", {}))
    sensors_raw = raw.get("sensors", [])
    sensors = [SensorConfig(**s) for s in sensors_raw]
    if not sensors:
        raise ValueError("config must declare at least one [[sensors]] entry")
    seen_ids: set[str] = set()
    for s in sensors:
        if s.id in seen_ids:
            raise ValueError(f"duplicate sensor id: {s.id!r}")
        seen_ids.add(s.id)
    return Config(
        server=server,
        logger=logger,
        cache=cache,
        development=development,
        external=external,
        sensors=sensors,
    )


def _parse_external(raw: dict[str, Any]) -> ExternalConfig:
    data = dict(raw)
    if "fetch" in data and data["fetch"] is not None:
        data["fetch"] = tuple(data["fetch"])
    cfg = ExternalConfig(**data)
    if cfg.provider not in _EXTERNAL_PROVIDERS:
        raise ValueError(
            f"[external] provider must be one of {_EXTERNAL_PROVIDERS}, got {cfg.provider!r}"
        )
    if cfg.enabled and cfg.provider == "wunderground":
        if not cfg.station_id or not cfg.api_key:
            raise ValueError(
                "[external] provider 'wunderground' requires both station_id and api_key"
            )
    return cfg
