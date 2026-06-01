"""Pydantic response models for the weather API.

These models are the runtime expression of the contract in
docs/design/02-api-design.md. FastAPI uses them to drive both response
validation and the OpenAPI schema at /docs.

Field provenance taxonomy from the API design doc:
- RAW           — direct sensor reading
- CALIBRATED    — raw + offset
- D-READING     — derived from a single reading
- D-LOCATION    — derived from GPS coords
- D-TIME        — derived from clock + location
- META          — server bookkeeping

Two tags extend the original taxonomy (documented in docs/design/02-api-design.md;
rationale in docs/adr/0001-optional-internet-external-data-feed.md):
- EXTERNAL      — internet-sourced regional data (wind etc.) and anything
                  fused from it; OPTIONAL — absent/null when offline
- D-HISTORY     — derived from the logged time series (see SummaryResponse)
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


# ── SensorReading ────────────────────────────────────────────────────────────


class RawReading(_StrictModel):
    temperature_c: float | None = None
    humidity_pct: float | None = None
    pressure_pa: float | None = None
    lux: float | None = None
    ir: int | None = None
    visible: int | None = None
    full: int | None = None


class CalibrationBlock(_StrictModel):
    temp_offset_c: float | None = None


class SkyBlock(_StrictModel):
    """Light-sensor derivations (D-READING). `estimated` is always True — these
    are modeled from illuminance + sun geometry, not measured by instruments."""

    estimated: bool = True
    sun_altitude_deg: float | None = None
    solar_irradiance_w_m2: float | None = None
    cloud_cover_pct: float | None = None
    uv_index_estimate: float | None = None
    sky_condition: str | None = None


class DerivedReading(_StrictModel):
    temperature_c: float | None = None
    temperature_f: float | None = None
    feels_like_c: float | None = None
    feels_like_f: float | None = None
    dewpoint_c: float | None = None
    dewpoint_f: float | None = None
    absolute_humidity_g_m3: float | None = None
    pressure_station_hpa: float | None = None
    pressure_station_inhg: float | None = None
    pressure_sealevel_hpa: float | None = None
    pressure_sealevel_inhg: float | None = None
    # Altimeter setting (QNH): ISA reduction to field elevation,
    # temperature-INDEPENDENT — distinct from pressure_sealevel_* above.
    altimeter_setting_hpa: float | None = None
    altimeter_setting_inhg: float | None = None

    # Extended thermodynamics (D-READING) — local, always available.
    wet_bulb_c: float | None = None
    wet_bulb_f: float | None = None
    humidex_c: float | None = None
    humidex_f: float | None = None
    frost_point_c: float | None = None
    frost_point_f: float | None = None
    saturation_vapor_pressure_hpa: float | None = None
    vapor_pressure_hpa: float | None = None
    vapor_pressure_deficit_kpa: float | None = None
    mixing_ratio_g_kg: float | None = None
    specific_humidity_g_kg: float | None = None
    air_density_kg_m3: float | None = None
    pressure_altitude_m: float | None = None
    pressure_altitude_ft: float | None = None
    density_altitude_m: float | None = None
    density_altitude_ft: float | None = None
    cloud_base_m: float | None = None
    cloud_base_ft: float | None = None

    # Light-sensor estimates (present only when the sensor has a light sensor
    # and the sun is up enough to be meaningful).
    sky: SkyBlock | None = None


class LocationBlock(_StrictModel):
    lat: float | None = None
    lon: float | None = None
    altitude_m: float | None = None
    altitude_ft: float | None = None
    satellites: int | None = None
    speed_kmh: float | None = None
    course_deg: float | None = None
    dms: str | None = None
    maidenhead: str | None = None


class DeviceBlock(_StrictModel):
    rssi_dbm: int | None = None
    uptime_s: int | None = None
    free_heap_bytes: int | None = None


class SensorReading(_StrictModel):
    sensor_id: str
    role: str
    online: bool
    reading_timestamp: datetime | None = None
    age_seconds: float | None = None

    raw: RawReading = Field(default_factory=RawReading)
    calibration: CalibrationBlock = Field(default_factory=CalibrationBlock)
    derived: DerivedReading = Field(default_factory=DerivedReading)
    location: LocationBlock | None = None
    device: DeviceBlock = Field(default_factory=DeviceBlock)


# ── Astronomy ────────────────────────────────────────────────────────────────


class ReferenceLocation(_StrictModel):
    lat: float | None = None
    lon: float | None = None
    source: str  # "outdoor_sensor" | "config_default" | "query_override"


class SunBlock(_StrictModel):
    altitude_deg: float | None = None
    azimuth_deg: float | None = None
    is_daytime: bool | None = None
    sunrise: datetime | None = None
    sunset: datetime | None = None
    solar_noon: datetime | None = None
    dawn: datetime | None = None
    dusk: datetime | None = None
    day_length_seconds: float | None = None
    seconds_to_sunset: float | None = None
    seconds_to_sunrise: float | None = None

    # Extended twilight bands and photography windows (D-TIME).
    nautical_dawn: datetime | None = None
    nautical_dusk: datetime | None = None
    astronomical_dawn: datetime | None = None
    astronomical_dusk: datetime | None = None
    golden_hour_dawn: datetime | None = None  # sun reaches +6° (morning)
    golden_hour_dusk: datetime | None = None  # sun drops to +6° (evening)
    blue_hour_dawn: datetime | None = None  # sun at -4° (morning)
    blue_hour_dusk: datetime | None = None  # sun at -4° (evening)

    sunrise_azimuth_deg: float | None = None
    sunset_azimuth_deg: float | None = None
    shadow_multiplier: float | None = None  # shadow length ÷ object height
    day_length_change_seconds: float | None = None  # vs. yesterday (+ = gaining)

    season: str | None = None
    next_solar_event: str | None = None
    next_solar_event_time: datetime | None = None
    seconds_to_next_solar_event: float | None = None


class MoonBlock(_StrictModel):
    altitude_deg: float | None = None
    azimuth_deg: float | None = None
    distance_km: float | None = None
    illumination_pct: float | None = None
    phase_name: str | None = None
    phase_icon: str | None = None
    moonrise: datetime | None = None
    moonset: datetime | None = None
    always_up: bool | None = None
    always_down: bool | None = None
    next_new_moon: datetime | None = None
    next_full_moon: datetime | None = None


class Astronomy(_StrictModel):
    server_time: datetime
    local_time: datetime | None = None
    timezone: str
    reference_location: ReferenceLocation
    sun: SunBlock
    moon: MoonBlock


# ── External (internet-sourced regional conditions) ──────────────────────────


class SkyLayer(_StrictModel):
    """One reported METAR sky layer (EXTERNAL)."""

    cover: str | None = None
    base_ft_agl: int | None = None


class ExternalBlock(_StrictModel):
    """Internet-sourced regional conditions (EXTERNAL provenance).

    OPTIONAL: the whole block is null/absent when the feed is disabled or no
    internet is available. Its presence is the only thing that differs between
    an online and an offline server — everything else is always present.
    """

    # provenance / freshness metadata
    provider: str | None = None  # "open-meteo" | "nws" | "wunderground"
    source: str | None = None  # human label, e.g. "nws:KBJC"
    station_id: str | None = None
    distance_km: float | None = None
    observed_at: datetime | None = None
    fetched_at: datetime | None = None
    age_seconds: float | None = None
    stale: bool | None = None
    confidence: str | None = None  # "normal" | "low"

    # wind
    wind_speed_ms: float | None = None
    wind_speed_kmh: float | None = None
    wind_speed_mph: float | None = None
    wind_speed_kt: float | None = None
    wind_gust_ms: float | None = None
    wind_gust_kmh: float | None = None
    wind_gust_mph: float | None = None
    wind_direction_deg: float | None = None
    wind_direction_cardinal: str | None = None

    # other regional conditions
    cloud_cover_pct: float | None = None
    uv_index: float | None = None
    precip_mm: float | None = None
    visibility_m: float | None = None
    visibility_km: float | None = None

    # fused indices — local sensors + external wind (null without wind)
    wind_chill_c: float | None = None
    wind_chill_f: float | None = None
    apparent_temperature_c: float | None = None
    apparent_temperature_f: float | None = None
    beaufort_force: int | None = None
    beaufort_description: str | None = None
    thsw_index_c: float | None = None
    thsw_index_f: float | None = None
    et0_mm_hour: float | None = None

    # aviation (METAR provider only) — ceiling/visibility/flight category are
    # the authoritative source for these; flight_category comes ONLY from the
    # METAR, never from our own sensors (decision 8).
    metar_raw: str | None = None
    sky_layers: list[SkyLayer] | None = None
    ceiling_ft_agl: int | None = None  # lowest BKN/OVC base; null = unlimited
    visibility_sm: float | None = None
    flight_category: str | None = None  # VFR | MVFR | IFR | LIFR
    altimeter_inhg: float | None = None  # station's reported QNH
    altimeter_hpa: float | None = None
    altimeter_diff_inhg: float | None = None  # station altimeter − our local setting
    temp_c: float | None = None
    dewpoint_c: float | None = None


# ── Airport / runways (D-LOCATION + wind-derived runway solution) ─────────────


class Runway(_StrictModel):
    """A runway, both ends. Headings carried in both frames (D-LOCATION).
    Internal canonical frame is TRUE; magnetic is exposed for the UI."""

    le_ident: str | None = None
    he_ident: str | None = None
    le_heading_true_deg: float | None = None
    he_heading_true_deg: float | None = None
    le_heading_mag_deg: float | None = None
    he_heading_mag_deg: float | None = None
    length_ft: float | None = None
    surface: str | None = None


class RunwayEnd(_StrictModel):
    """Wind components for one runway end (derived from the current local wind)."""

    ident: str | None = None
    headwind_kt: float | None = None
    crosswind_kt: float | None = None  # magnitude
    crosswind_side: str | None = None  # "L" | "R"
    tailwind: bool | None = None
    crosswind_exceeds_limit: bool | None = None


class RunwaySolution(_StrictModel):
    """Favored end + per-end components. Recomputed per request from the current
    LOCAL wind; null when there's no wind reading (e.g. no anemometer yet)."""

    favored: str | None = None
    ends: list[RunwayEnd] = Field(default_factory=list)


class Airport(_StrictModel):
    """Nearest/overridden airport. Identity, runway geometry, field elevation,
    and magnetic variation are D-LOCATION (functions of position; effectively
    static for a fixed station). runway_solution is derived from current wind."""

    ident: str | None = None
    name: str | None = None
    type: str | None = None
    field_elevation_ft: float | None = None
    distance_nm: float | None = None
    source: str | None = None  # "config_override" | "gps_nearest" | "manual"
    magnetic_variation_deg: float | None = None  # signed, +east
    magnetic_model: str | None = None
    runways: list[Runway] = Field(default_factory=list)
    runway_solution: RunwaySolution | None = None


# ── Endpoint responses ───────────────────────────────────────────────────────


class CurrentResponse(_StrictModel):
    server_time: datetime
    sensors: dict[str, SensorReading]
    astronomy: Astronomy
    external: ExternalBlock | None = None
    airport: Airport | None = None


class CurrentSensorResponse(_StrictModel):
    server_time: datetime
    sensor: SensorReading
    astronomy: Astronomy
    external: ExternalBlock | None = None


class ExternalResponse(_StrictModel):
    server_time: datetime
    external: ExternalBlock | None = None


class HistoryRow(_StrictModel):
    model_config = ConfigDict(extra="allow", frozen=True)
    timestamp: datetime


class HistoryResponse(_StrictModel):
    sensor_id: str
    from_: datetime = Field(serialization_alias="from", validation_alias="from")
    to: datetime
    bucket_seconds: int
    row_count: int
    rows: list[HistoryRow]

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        populate_by_name=True,
    )


class Stat(_StrictModel):
    min: float | None = None
    max: float | None = None
    avg: float | None = None


class SummaryResponse(_StrictModel):
    """Windowed history summary (D-HISTORY). Outdoor only."""

    sensor_id: str
    period: str
    from_: datetime = Field(serialization_alias="from", validation_alias="from")
    to: datetime
    timezone: str
    sample_count: int

    temperature_c: Stat | None = None
    temperature_f: Stat | None = None
    humidity_pct: Stat | None = None
    pressure_station_hpa: Stat | None = None
    pressure_sealevel_hpa: Stat | None = None
    dewpoint_avg_c: float | None = None
    diurnal_range_c: float | None = None

    pressure_tendency_hpa_3h: float | None = None
    pressure_trend: str | None = None  # "rising" | "falling" | "steady"
    temperature_trend_c_per_hour: float | None = None

    # Accumulated over the window (°F-days, US base 65/65/50).
    heating_degree_days_f: float | None = None
    cooling_degree_days_f: float | None = None
    growing_degree_days_f: float | None = None

    light_integral_mol_m2: float | None = None  # DLI over a single day
    hargreaves_et0_mm: float | None = None  # temperature-only reference ET₀

    model_config = ConfigDict(extra="forbid", frozen=True, populate_by_name=True)


class SensorListEntry(_StrictModel):
    sensor_id: str
    role: str
    ip: str
    has_gps: bool
    has_light: bool
    logged: bool
    last_seen: datetime | None
    age_seconds: float | None
    online: bool


class SensorListResponse(_StrictModel):
    server_time: datetime
    sensors: list[SensorListEntry]


class AstronomyResponse(_StrictModel):
    server_time: datetime
    astronomy: Astronomy


class AirportResponse(_StrictModel):
    server_time: datetime
    airport: Airport | None = None


class HealthSensorEntry(_StrictModel):
    sensor_id: str
    online: bool
    age_seconds: float | None


class HealthLoggerEntry(_StrictModel):
    sensor_id: str
    last_write_seconds_ago: float | None
    ok: bool


class HealthResponse(_StrictModel):
    ok: bool
    server_time: datetime
    db_reachable: bool
    sensors: list[HealthSensorEntry]
    loggers: list[HealthLoggerEntry]


# ── Errors ───────────────────────────────────────────────────────────────────


class ErrorBody(_StrictModel):
    code: str
    message: str
    details: dict[str, str] = Field(default_factory=dict)


class ErrorResponse(_StrictModel):
    error: ErrorBody
