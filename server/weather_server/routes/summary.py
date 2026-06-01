"""GET /api/v1/summary/{sensor_id} — windowed history summary (D-HISTORY).

Outdoor only (mirrors /history). Aggregates logged readings into extremes,
pressure tendency, trends, accumulated degree days, daily light integral, and a
temperature-only reference ET₀. Calibration and per-reading derivations are
applied via the shared derivations so the numbers match /current and /history.
"""

from __future__ import annotations

import sqlite3
from collections import defaultdict
from datetime import UTC, datetime
from typing import Literal

from fastapi import APIRouter, HTTPException, Request

from .. import db as db_module
from ..config import Config, SensorConfig
from ..derivations import astronomy as astro
from ..derivations import readings as rd
from ..derivations import summary as sm
from ..responses import _row_to_payload, utc_now
from ..schemas import Stat as StatModel
from ..schemas import SummaryResponse

router = APIRouter()

PeriodLiteral = Literal["today", "24h", "7d", "30d"]

_PERIOD_SECONDS = {"24h": 86400, "7d": 7 * 86400, "30d": 30 * 86400}


@router.get(
    "/api/v1/summary/{sensor_id}",
    response_model=SummaryResponse,
    response_model_exclude_none=False,
)
async def get_summary(
    sensor_id: str,
    request: Request,
    period: PeriodLiteral = "today",
) -> SummaryResponse:
    config = request.app.state.config
    db = request.app.state.db

    sensor_cfg = config.sensor_by_id(sensor_id)
    if sensor_cfg is None:
        raise HTTPException(status_code=404, detail=("sensor_not_found", sensor_id))
    if sensor_cfg.role != "outdoor":
        raise HTTPException(status_code=404, detail=("history_not_available", sensor_id))

    now = utc_now()
    lat, lon = _reference_location(config, db)
    tz_name = astro.resolve_timezone(lat, lon)
    from_ts, to_ts = _resolve_window(period, now, tz_name)

    rows = db_module.outdoor_readings_in_range(db, from_ts, to_ts)
    return _summarize(sensor_id, sensor_cfg, rows, period, from_ts, to_ts, tz_name, lat)


def _reference_location(
    config: Config, db: sqlite3.Connection
) -> tuple[float | None, float | None]:
    outdoor = config.outdoor
    row = db_module.latest_outdoor_reading(db)
    if row is not None and row["latitude"] is not None and row["longitude"] is not None:
        return float(row["latitude"]), float(row["longitude"])
    if (
        outdoor is not None
        and outdoor.fallback_lat is not None
        and outdoor.fallback_lon is not None
    ):
        return outdoor.fallback_lat, outdoor.fallback_lon
    return None, None


def _resolve_window(period: str, now: datetime, tz_name: str) -> tuple[int, int]:
    to_ts = int(now.timestamp())
    if period == "today":
        local_now = astro.to_local(now, tz_name) if tz_name != "UTC" else now
        local_midnight = local_now.replace(hour=0, minute=0, second=0, microsecond=0)
        return int(local_midnight.astimezone(UTC).timestamp()), to_ts
    return to_ts - _PERIOD_SECONDS[period], to_ts


def _summarize(
    sensor_id: str,
    sensor: SensorConfig,
    rows: list[sqlite3.Row],
    period: str,
    from_ts: int,
    to_ts: int,
    tz_name: str,
    lat: float | None,
) -> SummaryResponse:
    times: list[float] = []
    temp_c: list[float | None] = []
    temp_f: list[float | None] = []
    humidity: list[float | None] = []
    p_station: list[float | None] = []
    p_sealevel: list[float | None] = []
    dewpoint: list[float | None] = []
    lux: list[float | None] = []
    # local-date → [temps_c] for degree days / Hargreaves
    by_day: dict[str, list[float]] = defaultdict(list)

    for row in rows:
        ts = int(row["timestamp"])
        payload = _row_to_payload(row)
        derived = rd.derive_reading(
            payload,
            temp_offset_c=sensor.temp_offset_c,
            fallback_altitude_m=sensor.fallback_altitude_m,
        )
        times.append(float(ts))
        tc = derived.get("temperature_c")
        temp_c.append(tc)
        temp_f.append(derived.get("temperature_f"))
        humidity.append(payload.get("humidity_pct"))
        p_station.append(derived.get("pressure_station_hpa"))
        p_sealevel.append(derived.get("pressure_sealevel_hpa"))
        dewpoint.append(derived.get("dewpoint_c"))
        lux.append(payload.get("lux"))
        if tc is not None:
            dt = datetime.fromtimestamp(ts, tz=UTC)
            local_date = (astro.to_local(dt, tz_name) if tz_name != "UTC" else dt).date()
            by_day[local_date.isoformat()].append(tc)

    temp_stat = sm.stat(temp_c)
    dewpoint_stat = sm.stat(dewpoint)
    tendency, trend = sm.pressure_tendency(times, p_station)

    hdd, cdd, gdd, hargreaves = _accumulate_daily(by_day, lat)

    return SummaryResponse(
        sensor_id=sensor_id,
        period=period,
        from_=datetime.fromtimestamp(from_ts, tz=UTC),
        to=datetime.fromtimestamp(to_ts, tz=UTC),
        timezone=tz_name,
        sample_count=len(rows),
        temperature_c=_to_model(temp_stat),
        temperature_f=_to_model(sm.stat(temp_f)),
        humidity_pct=_to_model(sm.stat(humidity)),
        pressure_station_hpa=_to_model(sm.stat(p_station)),
        pressure_sealevel_hpa=_to_model(sm.stat(p_sealevel)),
        dewpoint_avg_c=_round(dewpoint_stat.avg) if dewpoint_stat else None,
        diurnal_range_c=(
            _round(temp_stat.max - temp_stat.min) if temp_stat else None
        ),
        pressure_tendency_hpa_3h=_round(tendency, 2),
        pressure_trend=trend,
        temperature_trend_c_per_hour=_round(sm.linear_trend_per_hour(times, temp_c), 3),
        heating_degree_days_f=_round(hdd, 2),
        cooling_degree_days_f=_round(cdd, 2),
        growing_degree_days_f=_round(gdd, 2),
        light_integral_mol_m2=_round(sm.light_integral_mol_m2(times, lux), 2),
        hargreaves_et0_mm=_round(hargreaves, 2),
    )


def _accumulate_daily(
    by_day: dict[str, list[float]], lat: float | None
) -> tuple[float | None, float | None, float | None, float | None]:
    """Sum per-day degree days and Hargreaves ET₀ across the window."""
    if not by_day:
        return None, None, None, None
    hdd = cdd = gdd = 0.0
    et0 = 0.0
    have_et0 = lat is not None
    for date_iso, temps in by_day.items():
        tmin_c, tmax_c = min(temps), max(temps)
        tmean_c = sum(temps) / len(temps)
        h, c, g = sm.degree_day_contributions(rd.c_to_f(tmin_c), rd.c_to_f(tmax_c))
        hdd += h
        cdd += c
        gdd += g
        if have_et0 and lat is not None:
            doy = datetime.fromisoformat(date_iso).timetuple().tm_yday
            ra = sm.extraterrestrial_radiation_mm(lat, doy)
            et0 += sm.hargreaves_et0_mm(tmin_c, tmax_c, tmean_c, ra)
    return hdd, cdd, gdd, (et0 if have_et0 else None)


def _to_model(s: sm.Stat | None) -> StatModel | None:
    if s is None:
        return None
    return StatModel(min=_round(s.min), max=_round(s.max), avg=_round(s.avg))


def _round(value: float | None, ndigits: int = 1) -> float | None:
    return None if value is None else round(value, ndigits)
