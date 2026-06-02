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
class LoggingConfig:
    """[logging] block — optional history logging, OFF by default.

    airfield-wx is a live-conditions instrument: every flight-relevant output
    is a read-time derivation off the *current* reading, so nothing needs
    history. With logging off the station is stateless — the logger loop never
    runs, nothing is written, and /history + /summary disappear (404). Turn it
    on only if you want trend charts / the daily summary. It CANNOT be applied
    retroactively: enabling it starts logging from then; past readings can't be
    recovered. It is also the prerequisite for the wind-history-enrichment work
    in docs/future-work.md."""

    enabled: bool = False


@dataclass(frozen=True)
class WindConfig:
    """[wind] block — flexible anemometer topology (decision 16 / ADR-0006).

    `source` selects where field wind is read from at request time:
    `"outdoor"` (default — wind rides the outdoor payload, all-in-one or
    remote-cable) or the id of a dedicated, logged wind-station sensor.
    `max_age_s` is the freshness threshold: wind older than this is treated
    as stale and the wind-derived fields (incl. the runway solution) are
    nulled, never presented as current. One threshold; no broader staleness
    model (ADR-0006)."""

    source: str = "outdoor"
    max_age_s: float = 90.0


@dataclass(frozen=True)
class DevelopmentConfig:
    fixture_dir: str | None = None


# Optional internet-sourced regional conditions (wind etc.). Disabled by
# default: when the [external] section is absent the fetch task never
# spawns and the server behaves exactly as an offline-only deployment.
_EXTERNAL_PROVIDERS = ("open-meteo", "nws", "wunderground", "metar")
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
class AirportRunwayConfig:
    """A manually-entered runway for a strip not in OurAirports. Headings are
    given as magnetic (what's painted on the runway); the engine derives true
    via the WMM variation."""

    ident: str
    le_heading_magnetic_deg: float | None = None
    length_ft: float | None = None
    surface: str | None = None


@dataclass(frozen=True)
class AirportConfig:
    """[airport] block. All optional: default is GPS-nearest from OurAirports.
    `crosswind_limit_kt` is parsed now and consumed in Cycle 4."""

    ident: str | None = None
    field_elevation_ft: float | None = None
    crosswind_limit_kt: float | None = None
    runways: tuple[AirportRunwayConfig, ...] = ()


@dataclass(frozen=True)
class SensorConfig:
    id: str
    role: str
    ip: str
    has_gps: bool = False
    has_light: bool = False
    has_wind: bool = False
    online_threshold_seconds: int = 120
    temp_offset_c: float = 0.0
    wind_vane_offset_deg: float = 0.0
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
    airport: AirportConfig = field(default_factory=AirportConfig)
    wind: WindConfig = field(default_factory=WindConfig)
    logging: LoggingConfig = field(default_factory=LoggingConfig)
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
    airport = _parse_airport(raw.get("airport", {}))
    wind = WindConfig(**raw.get("wind", {}))
    logging_cfg = LoggingConfig(**raw.get("logging", {}))
    sensors_raw = raw.get("sensors", [])
    sensors = [SensorConfig(**s) for s in sensors_raw]
    if not sensors:
        raise ValueError("config must declare at least one [[sensors]] entry")
    seen_ids: set[str] = set()
    for s in sensors:
        if s.id in seen_ids:
            raise ValueError(f"duplicate sensor id: {s.id!r}")
        seen_ids.add(s.id)
    # The wind source must be either the default outdoor payload or a
    # configured sensor id (the dedicated wind station — topology 3).
    if wind.source != "outdoor" and not any(s.id == wind.source for s in sensors):
        raise ValueError(
            f"[wind] source {wind.source!r} names no configured sensor"
        )
    return Config(
        server=server,
        logger=logger,
        cache=cache,
        development=development,
        external=external,
        airport=airport,
        wind=wind,
        logging=logging_cfg,
        sensors=sensors,
    )


def _parse_airport(raw: dict[str, Any]) -> AirportConfig:
    data = dict(raw)
    runways_raw = data.pop("runways", [])
    runways = tuple(AirportRunwayConfig(**r) for r in runways_raw)
    return AirportConfig(**data, runways=runways)


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
