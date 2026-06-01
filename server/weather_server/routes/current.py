"""GET /api/v1/current and /api/v1/current/{sensor_id}."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Request

from .. import db as db_module
from ..cache import TTLCache
from ..config import SensorConfig
from ..derivations.wind import resolve_wind
from ..responses import (
    build_airport,
    build_astronomy,
    build_external,
    build_live_reading,
    build_outdoor_reading_from_db_row,
    external_stale_after,
    utc_now,
)
from ..schemas import CurrentResponse, CurrentSensorResponse, SensorReading
from ..sensors import SensorPayload, SensorSource

log = logging.getLogger(__name__)

router = APIRouter()


@router.get(
    "/api/v1/current",
    response_model=CurrentResponse,
    response_model_exclude_none=False,
)
async def get_current(request: Request) -> CurrentResponse:
    server_time = utc_now()
    config = request.app.state.config
    db = request.app.state.db
    source = request.app.state.source
    cache = request.app.state.cache
    last_seen: dict[str, Any] = request.app.state.last_seen

    sensors_out: dict[str, SensorReading] = {}

    outdoor_cfg = config.outdoor
    outdoor_reading: SensorReading | None = None
    outdoor_row = db_module.latest_outdoor_reading(db) if outdoor_cfg is not None else None
    wind_row = (
        db_module.latest_wind_reading(db) if config.wind.source != "outdoor" else None
    )
    resolved_wind = resolve_wind(
        config, server_time, outdoor_row=outdoor_row, wind_row=wind_row
    )
    if outdoor_cfg is not None and outdoor_row is not None:
        outdoor_reading = build_outdoor_reading_from_db_row(
            outdoor_cfg, outdoor_row, server_time, resolved_wind=resolved_wind
        )
        sensors_out[outdoor_cfg.id] = outdoor_reading

    # The wind-source station (topology 3) is a logged data source, not a
    # display sensor: it feeds the resolver and is not shown in /current.
    other = [s for s in config.sensors if s.role not in ("outdoor", "wind_station")]
    poll_results = await asyncio.gather(
        *(_poll_with_cache(cache, source, s, ttl=config.cache.ttl_seconds) for s in other),
        return_exceptions=True,
    )
    for sensor_cfg, result in zip(other, poll_results, strict=True):
        reading = _live_reading_from_poll_result(sensor_cfg, result, last_seen, server_time)
        if reading is not None:
            sensors_out[sensor_cfg.id] = reading

    astronomy = build_astronomy(server_time, config, outdoor_reading)
    external = build_external(
        request.app.state.external_store.get(),
        server_time,
        stale_after_seconds=external_stale_after(config),
        outdoor_reading=outdoor_reading,
    )
    airport = build_airport(
        server_time, config, outdoor_reading, resolved_wind=resolved_wind
    )
    return CurrentResponse(
        server_time=server_time,
        sensors=sensors_out,
        astronomy=astronomy,
        external=external,
        airport=airport,
    )


@router.get(
    "/api/v1/current/{sensor_id}",
    response_model=CurrentSensorResponse,
    response_model_exclude_none=False,
)
async def get_current_one(sensor_id: str, request: Request) -> CurrentSensorResponse:
    config = request.app.state.config
    sensor_cfg = config.sensor_by_id(sensor_id)
    # The wind-source station is a logged data source, not a display sensor —
    # it has no standalone /current view (it feeds the resolver instead).
    if sensor_cfg is None or sensor_cfg.role == "wind_station":
        raise HTTPException(status_code=404, detail=("sensor_not_found", sensor_id))

    server_time = utc_now()
    db = request.app.state.db
    source = request.app.state.source
    cache = request.app.state.cache
    last_seen: dict[str, Any] = request.app.state.last_seen

    reading: SensorReading
    if sensor_cfg.role == "outdoor":
        row = db_module.latest_outdoor_reading(db)
        if row is None:
            raise HTTPException(status_code=503, detail=("sensor_no_data", sensor_id))
        wind_row = (
            db_module.latest_wind_reading(db)
            if config.wind.source != "outdoor"
            else None
        )
        resolved_wind = resolve_wind(
            config, server_time, outdoor_row=row, wind_row=wind_row
        )
        reading = build_outdoor_reading_from_db_row(
            sensor_cfg, row, server_time, resolved_wind=resolved_wind
        )
    else:
        try:
            payload = await _poll_with_cache(
                cache, source, sensor_cfg, ttl=config.cache.ttl_seconds
            )
        except Exception:
            log.exception("poll failed for %s", sensor_id)
            payload = None
        live = _live_reading_from_poll_result(sensor_cfg, payload, last_seen, server_time)
        if live is None:
            raise HTTPException(status_code=503, detail=("sensor_no_data", sensor_id))
        reading = live

    astronomy = build_astronomy(
        server_time,
        config,
        reading if sensor_cfg.role == "outdoor" else None,
    )
    external = build_external(
        request.app.state.external_store.get(),
        server_time,
        stale_after_seconds=external_stale_after(config),
        outdoor_reading=reading if sensor_cfg.role == "outdoor" else None,
    )
    return CurrentSensorResponse(
        server_time=server_time,
        sensor=reading,
        astronomy=astronomy,
        external=external,
    )


async def _poll_with_cache(
    cache: TTLCache,
    source: SensorSource,
    sensor: SensorConfig,
    *,
    ttl: float,
) -> SensorPayload | None:
    async def fetch() -> SensorPayload | None:
        return await source.poll(sensor)

    return await cache.get_or_fetch(sensor.id, fetch, ttl=ttl)


def _live_reading_from_poll_result(
    sensor_cfg: SensorConfig,
    result: Any,
    last_seen: dict[str, Any],
    server_time: Any,
) -> SensorReading | None:
    if isinstance(result, Exception):
        log.warning("poll for %s raised %s", sensor_cfg.id, result)
        result = None

    if result is not None:
        last_seen[sensor_cfg.id] = (result, server_time)
        return build_live_reading(sensor_cfg, result, server_time, server_time)

    entry = last_seen.get(sensor_cfg.id)
    if entry is not None:
        saved_payload, saved_ts = entry
        return build_live_reading(sensor_cfg, saved_payload, saved_ts, server_time)

    return None
