"""GET /api/v1/health."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Request

from .. import db as db_module
from ..responses import utc_now
from ..schemas import HealthLoggerEntry, HealthResponse, HealthSensorEntry

router = APIRouter()


@router.get(
    "/api/v1/health",
    response_model=HealthResponse,
    response_model_exclude_none=False,
)
async def get_health(request: Request) -> HealthResponse:
    server_time = utc_now()
    config = request.app.state.config
    db = request.app.state.db
    last_seen: dict[str, Any] = request.app.state.last_seen

    db_reachable = db_module.db_ok(db)

    sensor_entries: list[HealthSensorEntry] = []
    for s in config.sensors:
        last_dt: datetime | None = None
        if s.role == "outdoor":
            ts = db_module.latest_outdoor_timestamp(db)
            if ts is not None:
                last_dt = datetime.fromtimestamp(ts, tz=UTC)
        else:
            entry = last_seen.get(s.id)
            if entry is not None:
                _, last_dt = entry
        age = (server_time - last_dt).total_seconds() if last_dt is not None else None
        sensor_entries.append(
            HealthSensorEntry(
                sensor_id=s.id,
                online=age is not None and age < s.online_threshold_seconds,
                age_seconds=round(age, 1) if age is not None else None,
            )
        )

    logger_entries: list[HealthLoggerEntry] = []
    outdoor_cfg = config.outdoor
    if outdoor_cfg is not None:
        last_ts = db_module.latest_outdoor_timestamp(db)
        if last_ts is None:
            logger_entries.append(
                HealthLoggerEntry(sensor_id=outdoor_cfg.id, last_write_seconds_ago=None, ok=False)
            )
        else:
            age = (server_time - datetime.fromtimestamp(last_ts, tz=UTC)).total_seconds()
            # ok if writes are still happening within 3 * configured interval.
            ok = age < 3 * config.logger.interval_seconds
            logger_entries.append(
                HealthLoggerEntry(
                    sensor_id=outdoor_cfg.id,
                    last_write_seconds_ago=round(age, 1),
                    ok=ok,
                )
            )

    overall_ok = db_reachable and all(le.ok for le in logger_entries)
    return HealthResponse(
        ok=overall_ok,
        server_time=server_time,
        db_reachable=db_reachable,
        sensors=sensor_entries,
        loggers=logger_entries,
    )
