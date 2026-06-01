"""Compose API response objects from DB rows, live payloads, and config.

Centralized here so all routes produce identical shapes for the same
inputs. Each `build_*` function returns a Pydantic model from schemas.py.
"""

from __future__ import annotations

import functools
import sqlite3
from datetime import UTC, datetime, timedelta
from typing import Any

from .config import AirportConfig, Config, SensorConfig
from .derivations import astronomy as astro
from .derivations import fused
from .derivations import light as lt
from .derivations import location as loc
from .derivations import readings as rd
from .derivations.airport import AirportInfo, resolve_airport
from .derivations.runway import RunwaySolution as RunwaySolutionData
from .derivations.runway import runway_solution
from .external import Observation, cardinal_from_deg
from .schemas import (
    Airport,
    Astronomy,
    CalibrationBlock,
    DerivedReading,
    DeviceBlock,
    ExternalBlock,
    LocationBlock,
    MoonBlock,
    RawReading,
    ReferenceLocation,
    Runway,
    RunwayEnd,
    RunwaySolution,
    SensorReading,
    SkyBlock,
    SkyLayer,
    SunBlock,
)

MS_TO_KMH = 3.6
MS_TO_MPH = 2.236936
MS_TO_KT = 1.943844


def utc_now() -> datetime:
    return datetime.now(UTC)


# ── sensor readings ─────────────────────────────────────────────────────────


def build_outdoor_reading_from_db_row(
    sensor: SensorConfig,
    row: sqlite3.Row,
    server_time: datetime,
) -> SensorReading:
    reading_ts = datetime.fromtimestamp(int(row["timestamp"]), tz=UTC)
    age = (server_time - reading_ts).total_seconds()
    online = age < sensor.online_threshold_seconds

    payload = _row_to_payload(row)
    return _build_reading(sensor, payload, reading_ts, age, online)


def build_live_reading(
    sensor: SensorConfig,
    payload: dict[str, Any],
    last_seen_at: datetime,
    server_time: datetime,
) -> SensorReading:
    age = (server_time - last_seen_at).total_seconds()
    online = age < sensor.online_threshold_seconds
    return _build_reading(sensor, payload, last_seen_at, age, online)


def _build_reading(
    sensor: SensorConfig,
    payload: dict[str, Any],
    reading_ts: datetime,
    age: float,
    online: bool,
) -> SensorReading:
    derived = rd.derive_reading(
        payload,
        temp_offset_c=sensor.temp_offset_c,
        fallback_altitude_m=sensor.fallback_altitude_m,
    )
    raw_block = rd.map_raw(payload)
    location_block = _build_location_block(payload) if sensor.has_gps else None
    device_block = _build_device_block(payload)
    sky_block = _build_sky_block(sensor, payload, reading_ts)

    return SensorReading(
        sensor_id=sensor.id,
        role=sensor.role,
        online=online,
        reading_timestamp=reading_ts,
        age_seconds=round(age, 1),
        raw=RawReading(**raw_block),
        calibration=CalibrationBlock(temp_offset_c=sensor.temp_offset_c),
        derived=DerivedReading(**derived, sky=sky_block),
        location=location_block,
        device=device_block,
    )


def _build_sky_block(
    sensor: SensorConfig, payload: dict[str, Any], reading_ts: datetime
) -> SkyBlock | None:
    """Light-sensor estimates. Needs a light sensor, a lux reading, and GPS so
    the sun's altitude at reading time can be computed. Otherwise None."""
    if not sensor.has_light:
        return None
    lux = payload.get("lux")
    lat = payload.get("latitude")
    lon = payload.get("longitude")
    if lux is None or lat is None or lon is None:
        return None
    sun_alt = astro.sun_position(reading_ts, lat, lon).altitude_deg
    cloud = lt.cloud_cover_pct(lux, sun_alt)
    return SkyBlock(
        sun_altitude_deg=sun_alt,
        solar_irradiance_w_m2=lt.lux_to_irradiance_w_m2(lux),
        cloud_cover_pct=cloud,
        uv_index_estimate=lt.uv_index_estimate(sun_alt, cloud),
        sky_condition=lt.sky_condition(sun_alt, cloud),
    )


def _row_to_payload(row: sqlite3.Row | dict[str, Any]) -> dict[str, Any]:
    """Accepts either a real sqlite3.Row from the DB or a bucketed dict
    synthesised by the history endpoint; both support keys() + [k] access."""
    payload = {k: row[k] for k in row.keys() if k not in {"id", "timestamp"}}
    return {k: v for k, v in payload.items() if v is not None}


def _build_location_block(payload: dict[str, Any]) -> LocationBlock | None:
    lat = payload.get("latitude")
    lon = payload.get("longitude")
    alt_m = payload.get("altitude_m")
    block: dict[str, Any] = {
        "lat": lat,
        "lon": lon,
        "altitude_m": alt_m,
        "satellites": payload.get("satellites"),
        "speed_kmh": payload.get("speed_kmh"),
        "course_deg": payload.get("course_deg"),
    }
    if alt_m is not None:
        block["altitude_ft"] = loc.altitude_m_to_ft(alt_m)
    if lat is not None and lon is not None:
        block["dms"] = loc.decimal_to_dms(lat, lon)
        block["maidenhead"] = loc.maidenhead(lat, lon)
    return LocationBlock(**block)


def _build_device_block(payload: dict[str, Any]) -> DeviceBlock:
    return DeviceBlock(
        rssi_dbm=payload.get("rssi_dbm"),
        uptime_s=payload.get("uptime_s"),
        free_heap_bytes=payload.get("free_heap_bytes"),
    )


# ── astronomy ───────────────────────────────────────────────────────────────


def build_astronomy(
    server_time: datetime,
    config: Config,
    outdoor_reading: SensorReading | None,
    *,
    lat_override: float | None = None,
    lon_override: float | None = None,
) -> Astronomy:
    lat, lon, source = _resolve_reference_location(
        config, outdoor_reading, lat_override, lon_override
    )
    tz_name = astro.resolve_timezone(lat, lon)
    local_time = astro.to_local(server_time, tz_name) if tz_name != "UTC" else server_time

    if lat is None or lon is None:
        sun = SunBlock()
        moon = MoonBlock()
    else:
        sun = _build_sun_block(server_time, lat, lon, tz_name)
        moon = _build_moon_block(server_time, lat, lon, tz_name)

    return Astronomy(
        server_time=server_time,
        local_time=local_time,
        timezone=tz_name,
        reference_location=ReferenceLocation(lat=lat, lon=lon, source=source),
        sun=sun,
        moon=moon,
    )


def _resolve_reference_location(
    config: Config,
    outdoor_reading: SensorReading | None,
    lat_override: float | None,
    lon_override: float | None,
) -> tuple[float | None, float | None, str]:
    if lat_override is not None and lon_override is not None:
        return lat_override, lon_override, "query_override"

    if (
        outdoor_reading is not None
        and outdoor_reading.location is not None
        and outdoor_reading.location.lat is not None
        and outdoor_reading.location.lon is not None
    ):
        return (
            outdoor_reading.location.lat,
            outdoor_reading.location.lon,
            "outdoor_sensor",
        )

    outdoor_cfg = config.outdoor
    if (
        outdoor_cfg is not None
        and outdoor_cfg.fallback_lat is not None
        and outdoor_cfg.fallback_lon is not None
    ):
        return outdoor_cfg.fallback_lat, outdoor_cfg.fallback_lon, "config_default"

    return None, None, "config_default"


def _to_local(d: datetime | None, tz_name: str) -> datetime | None:
    """Project a UTC datetime into the resolved IANA zone, leaving None alone.
    Sun and moon event timestamps go out to clients as local-zoned ISO
    strings per 02-api-design.md; deltas (day length, seconds to sunset)
    are computed from the original UTC values before this projection."""
    if d is None or tz_name == "UTC":
        return d
    return astro.to_local(d, tz_name)


def _build_sun_block(server_time: datetime, lat: float, lon: float, tz_name: str) -> SunBlock:
    pos = astro.sun_position(server_time, lat, lon)
    times = astro.sun_times(server_time, lat, lon)

    day_length: float | None = None
    if times.sunrise is not None and times.sunset is not None:
        day_length = (times.sunset - times.sunrise).total_seconds()

    secs_to_sunset: float | None = None
    secs_to_sunrise: float | None = None
    if times.sunset is not None:
        delta = (times.sunset - server_time).total_seconds()
        if delta >= 0:
            secs_to_sunset = delta
    if secs_to_sunset is None and times.sunrise is not None:
        # After sunset → count to tomorrow's sunrise. Approximate by adding
        # 24h to today's sunrise if it's already past.
        tomorrow_sunrise = (
            times.sunrise if times.sunrise > server_time else times.sunrise + timedelta(days=1)
        )
        secs_to_sunrise = (tomorrow_sunrise - server_time).total_seconds()

    nautical_dawn, nautical_dusk = astro.sun_event(server_time, lat, lon, -12.0)
    astro_dawn, astro_dusk = astro.sun_event(server_time, lat, lon, -18.0)
    golden_dawn, golden_dusk = astro.sun_event(server_time, lat, lon, 6.0)
    blue_dawn, blue_dusk = astro.sun_event(server_time, lat, lon, -4.0)

    sunrise_az = (
        astro.sun_position(times.sunrise, lat, lon).azimuth_deg
        if times.sunrise is not None
        else None
    )
    sunset_az = (
        astro.sun_position(times.sunset, lat, lon).azimuth_deg
        if times.sunset is not None
        else None
    )

    day_length_change: float | None = None
    if day_length is not None:
        y = astro.sun_times(server_time - timedelta(days=1), lat, lon)
        if y.sunrise is not None and y.sunset is not None:
            day_length_change = day_length - (y.sunset - y.sunrise).total_seconds()

    season = astro.season_info(server_time, lat)

    return SunBlock(
        altitude_deg=pos.altitude_deg,
        azimuth_deg=pos.azimuth_deg,
        is_daytime=pos.altitude_deg > 0,
        sunrise=_to_local(times.sunrise, tz_name),
        sunset=_to_local(times.sunset, tz_name),
        solar_noon=_to_local(times.solar_noon, tz_name),
        dawn=_to_local(times.dawn, tz_name),
        dusk=_to_local(times.dusk, tz_name),
        day_length_seconds=day_length,
        seconds_to_sunset=secs_to_sunset,
        seconds_to_sunrise=secs_to_sunrise,
        nautical_dawn=_to_local(nautical_dawn, tz_name),
        nautical_dusk=_to_local(nautical_dusk, tz_name),
        astronomical_dawn=_to_local(astro_dawn, tz_name),
        astronomical_dusk=_to_local(astro_dusk, tz_name),
        golden_hour_dawn=_to_local(golden_dawn, tz_name),
        golden_hour_dusk=_to_local(golden_dusk, tz_name),
        blue_hour_dawn=_to_local(blue_dawn, tz_name),
        blue_hour_dusk=_to_local(blue_dusk, tz_name),
        sunrise_azimuth_deg=sunrise_az,
        sunset_azimuth_deg=sunset_az,
        shadow_multiplier=astro.shadow_multiplier(pos.altitude_deg),
        day_length_change_seconds=day_length_change,
        season=season.season,
        next_solar_event=season.next_event,
        next_solar_event_time=_to_local(season.next_event_time, tz_name),
        seconds_to_next_solar_event=season.seconds_to_next_event,
    )


def _build_moon_block(server_time: datetime, lat: float, lon: float, tz_name: str) -> MoonBlock:
    pos = astro.moon_position(server_time, lat, lon)
    illum = astro.moon_illumination(server_time)
    times = astro.moon_times(server_time, lat, lon)
    next_new, next_full = astro.upcoming_moon_phases(server_time)
    return MoonBlock(
        altitude_deg=pos.altitude_deg,
        azimuth_deg=pos.azimuth_deg,
        distance_km=pos.distance_km,
        illumination_pct=illum.fraction * 100.0,
        phase_name=astro.moon_phase_name(illum.phase),
        phase_icon=astro.moon_phase_icon(illum.phase),
        moonrise=_to_local(times.get("rise"), tz_name),
        moonset=_to_local(times.get("set"), tz_name),
        always_up=bool(times.get("always_up", False)),
        always_down=bool(times.get("always_down", False)),
        next_new_moon=_to_local(next_new, tz_name),
        next_full_moon=_to_local(next_full, tz_name),
    )


# ── external (internet-sourced regional conditions) ──────────────────────────


def _round(value: float | None, ndigits: int = 1) -> float | None:
    return None if value is None else round(value, ndigits)


def build_external(
    store_result: tuple[Observation, datetime] | None,
    server_time: datetime,
    *,
    stale_after_seconds: float,
    outdoor_reading: SensorReading | None = None,
) -> ExternalBlock | None:
    """Map the last-known external observation to an ExternalBlock.

    Returns None when nothing has ever been fetched (feed disabled or no
    successful fetch yet) — that is the offline/absent state. Wind speed is
    converted from the internal m/s to every display unit, and the fused
    indices (wind chill, apparent temp, THSW, ET₀) are computed by combining
    the external wind with the local outdoor reading.
    """
    if store_result is None:
        return None
    obs, fetched_at = store_result

    reference_ts = obs.observed_at or fetched_at
    age = (server_time - reference_ts).total_seconds()
    stale = age > stale_after_seconds

    ws = obs.wind_speed_ms
    wg = obs.wind_gust_ms
    vis = obs.visibility_m
    fused = _fused_indices(ws, obs.wind_direction_deg, outdoor_reading)
    aviation = _aviation_fields(obs, outdoor_reading)

    return ExternalBlock(
        provider=obs.provider,
        source=obs.source,
        station_id=obs.station_id,
        distance_km=obs.distance_km,
        observed_at=obs.observed_at,
        fetched_at=fetched_at,
        age_seconds=round(age, 1),
        stale=stale,
        confidence=obs.confidence,
        wind_speed_ms=_round(ws),
        wind_speed_kmh=_round(None if ws is None else ws * MS_TO_KMH),
        wind_speed_mph=_round(None if ws is None else ws * MS_TO_MPH),
        wind_speed_kt=_round(None if ws is None else ws * MS_TO_KT),
        wind_gust_ms=_round(wg),
        wind_gust_kmh=_round(None if wg is None else wg * MS_TO_KMH),
        wind_gust_mph=_round(None if wg is None else wg * MS_TO_MPH),
        wind_direction_deg=_round(obs.wind_direction_deg),
        wind_direction_cardinal=cardinal_from_deg(obs.wind_direction_deg),
        cloud_cover_pct=_round(obs.cloud_cover_pct),
        uv_index=_round(obs.uv_index),
        precip_mm=_round(obs.precip_mm, 2),
        visibility_m=_round(vis, 0),
        visibility_km=_round(None if vis is None else vis / 1000.0, 1),
        **fused,
        **aviation,
    )


def _fused_indices(
    wind_speed_ms: float | None,
    wind_direction_deg: float | None,  # noqa: ARG001 - kept for symmetry/future use
    outdoor_reading: SensorReading | None,
) -> dict[str, Any]:
    """Indices that need external wind + the local outdoor reading. Empty when
    wind or the required local inputs are missing."""
    if wind_speed_ms is None:
        return {}

    out: dict[str, Any] = {}
    force, description = fused.beaufort(wind_speed_ms)
    out["beaufort_force"] = force
    out["beaufort_description"] = description

    if outdoor_reading is None:
        return out
    temp_c = outdoor_reading.derived.temperature_c
    humidity = outdoor_reading.raw.humidity_pct
    pressure_pa = outdoor_reading.raw.pressure_pa
    if temp_c is None or humidity is None:
        return out

    wc = fused.wind_chill_c(temp_c, wind_speed_ms)
    out["wind_chill_c"] = _round(wc)
    out["wind_chill_f"] = _round(rd.c_to_f(wc))

    at = fused.apparent_temperature_c(temp_c, humidity, wind_speed_ms)
    out["apparent_temperature_c"] = _round(at)
    out["apparent_temperature_f"] = _round(rd.c_to_f(at))

    sky = outdoor_reading.derived.sky
    solar = sky.solar_irradiance_w_m2 if sky else None
    if solar is not None:
        thsw = fused.thsw_index_c(temp_c, humidity, wind_speed_ms, solar)
        out["thsw_index_c"] = _round(thsw)
        out["thsw_index_f"] = _round(rd.c_to_f(thsw))
        if pressure_pa is not None:
            out["et0_mm_hour"] = _round(
                fused.et0_hourly_mm(temp_c, humidity, wind_speed_ms, solar, pressure_pa), 3
            )
    return out


def _aviation_fields(
    obs: Observation, outdoor_reading: SensorReading | None
) -> dict[str, Any]:
    """METAR aviation fields + the altimeter cross-check. For the model
    providers these obs fields are None, so everything maps to None."""
    out: dict[str, Any] = {
        "metar_raw": obs.metar_raw,
        "ceiling_ft_agl": obs.ceiling_ft_agl,
        "visibility_sm": _round(obs.visibility_sm),
        "flight_category": obs.flight_category,
        "altimeter_inhg": _round(obs.altimeter_inhg, 2),
        "altimeter_hpa": _round(obs.altimeter_hpa),
        "temp_c": _round(obs.temp_c),
        "dewpoint_c": _round(obs.dewpoint_c),
    }
    if obs.sky_layers is not None:
        out["sky_layers"] = [
            SkyLayer(cover=layer.cover, base_ft_agl=layer.base_ft_agl)
            for layer in obs.sky_layers
        ]
    # Cross-check the station altimeter against our locally-derived setting.
    diff: float | None = None
    if obs.altimeter_inhg is not None and outdoor_reading is not None:
        local = outdoor_reading.derived.altimeter_setting_inhg
        if local is not None:
            diff = round(obs.altimeter_inhg - local, 2)
    out["altimeter_diff_inhg"] = diff
    return out


def external_stale_after(config: Config) -> float:
    """How old an external observation may get before it's flagged stale:
    three refresh intervals, floored at 15 minutes."""
    return max(900.0, 3.0 * config.external.refresh_interval_seconds)


# ── airport / runways ─────────────────────────────────────────────────────────


def _resolve_reference_altitude(
    config: Config, outdoor_reading: SensorReading | None
) -> float | None:
    """Altitude (m) for the location engine: live GPS vertical, then the
    outdoor sensor's configured fallback."""
    if (
        outdoor_reading is not None
        and outdoor_reading.location is not None
        and outdoor_reading.location.altitude_m is not None
    ):
        return outdoor_reading.location.altitude_m
    outdoor_cfg = config.outdoor
    if outdoor_cfg is not None:
        return outdoor_cfg.fallback_altitude_m
    return None


def _local_wind(outdoor_reading: SensorReading | None) -> tuple[float | None, float | None]:
    """The current LOCAL wind as (FROM-direction TRUE deg, speed kt).

    Reads the derived true-direction wind and the raw m/s speed that the
    anemometer adds in Cycle 6. Until then those fields are absent, so this
    returns (None, None) and the runway solution stays null. Never the
    external/model wind (decision 6)."""
    if outdoor_reading is None:
        return None, None
    direction: float | None = getattr(outdoor_reading.derived, "wind_direction_true_deg", None)
    speed_ms: float | None = getattr(outdoor_reading.raw, "wind_speed_ms", None)
    speed_kt = speed_ms * MS_TO_KT if speed_ms is not None else None
    return direction, speed_kt


@functools.lru_cache(maxsize=16)
def _resolve_airport_cached(
    lat: float | None,
    lon: float | None,
    alt_m: float | None,
    year: int,
    airport_cfg: AirportConfig,
) -> AirportInfo | None:
    """Cache the static airport per (rounded location, year, config). The
    airport — identity, runways, elevation, declination — doesn't change for a
    fixed station, so this avoids rescanning the 85k-row dataset every request.
    The wind-dependent runway solution is computed fresh by the caller."""
    when = datetime(year, 7, 1, tzinfo=UTC)
    return resolve_airport(lat, lon, alt_m, when, airport_cfg)


def build_airport(
    server_time: datetime,
    config: Config,
    outdoor_reading: SensorReading | None,
    *,
    lat_override: float | None = None,
    lon_override: float | None = None,
) -> Airport | None:
    lat, lon, _source = _resolve_reference_location(
        config, outdoor_reading, lat_override, lon_override
    )
    alt_m = _resolve_reference_altitude(config, outdoor_reading)
    info = _resolve_airport_cached(
        None if lat is None else round(lat, 4),
        None if lon is None else round(lon, 4),
        None if alt_m is None else round(alt_m, 1),
        server_time.year,
        config.airport,
    )
    if info is None:
        return None

    wind_from_true_deg, wind_speed_kt = _local_wind(outdoor_reading)
    solution = runway_solution(
        info.runways, wind_from_true_deg, wind_speed_kt, config.airport.crosswind_limit_kt
    )
    return _airport_model(info, solution)


def _airport_model(info: AirportInfo, solution: RunwaySolutionData | None) -> Airport:
    return Airport(
        ident=info.ident,
        name=info.name,
        type=info.type,
        field_elevation_ft=_round(info.field_elevation_ft),
        distance_nm=_round(info.distance_nm, 2),
        source=info.source,
        magnetic_variation_deg=_round(info.magnetic_variation_deg),
        magnetic_model=info.magnetic_model,
        runways=[
            Runway(
                le_ident=r.le_ident,
                he_ident=r.he_ident,
                le_heading_true_deg=_round(r.le_heading_true_deg),
                he_heading_true_deg=_round(r.he_heading_true_deg),
                le_heading_mag_deg=_round(r.le_heading_mag_deg),
                he_heading_mag_deg=_round(r.he_heading_mag_deg),
                length_ft=r.length_ft,
                surface=r.surface,
            )
            for r in info.runways
        ],
        runway_solution=_runway_solution_model(solution),
    )


def _runway_solution_model(solution: RunwaySolutionData | None) -> RunwaySolution | None:
    if solution is None:
        return None
    return RunwaySolution(
        favored=solution.favored,
        ends=[
            RunwayEnd(
                ident=e.ident,
                headwind_kt=_round(e.headwind_kt),
                crosswind_kt=_round(e.crosswind_kt),
                crosswind_side=e.crosswind_side,
                tailwind=e.tailwind,
                crosswind_exceeds_limit=e.crosswind_exceeds_limit,
            )
            for e in solution.ends
        ],
    )


# ── history rows ────────────────────────────────────────────────────────────


HISTORY_GROUPS = {
    "weather": (
        "temperature_c",
        "temperature_f",
        "humidity_pct",
        "pressure_station_hpa",
        "pressure_sealevel_hpa",
        "dewpoint_c",
    ),
    "light": ("lux", "ir", "visible", "full"),
    "location": ("lat", "lon", "altitude_m", "satellites", "maidenhead"),
    "device": ("rssi_dbm", "uptime_s", "free_heap_bytes"),
}


def build_history_row(
    row: sqlite3.Row | dict[str, Any],
    sensor: SensorConfig,
    include_groups: set[str],
) -> dict[str, Any]:
    ts = datetime.fromtimestamp(int(row["timestamp"]), tz=UTC)
    payload = _row_to_payload(row)
    derived = rd.derive_reading(
        payload,
        temp_offset_c=sensor.temp_offset_c,
        fallback_altitude_m=sensor.fallback_altitude_m,
    )

    out: dict[str, Any] = {"timestamp": ts}

    if "weather" in include_groups:
        for k in HISTORY_GROUPS["weather"]:
            v = derived.get(k) if k in derived else payload.get(k)
            if v is not None:
                out[k] = v

    if "light" in include_groups:
        # `full_spectrum` in payload maps to `full` in the row.
        if "lux" in payload:
            out["lux"] = payload["lux"]
        if "ir" in payload:
            out["ir"] = payload["ir"]
        if "visible" in payload:
            out["visible"] = payload["visible"]
        if "full_spectrum" in payload:
            out["full"] = payload["full_spectrum"]

    if "location" in include_groups:
        if "latitude" in payload:
            out["lat"] = payload["latitude"]
        if "longitude" in payload:
            out["lon"] = payload["longitude"]
        if "altitude_m" in payload:
            out["altitude_m"] = payload["altitude_m"]
        if "satellites" in payload:
            out["satellites"] = payload["satellites"]
        if payload.get("latitude") is not None and payload.get("longitude") is not None:
            out["maidenhead"] = loc.maidenhead(payload["latitude"], payload["longitude"])

    if "device" in include_groups:
        for k in ("rssi_dbm", "uptime_s", "free_heap_bytes"):
            if k in payload:
                out[k] = payload[k]

    return out
