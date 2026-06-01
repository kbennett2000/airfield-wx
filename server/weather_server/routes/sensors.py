"""GET /api/v1/sensors — list of registered sensors and their status."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Request

from .. import db as db_module
from ..responses import utc_now
from ..schemas import SensorListEntry, SensorListResponse

router = APIRouter()


@router.get(
    "/api/v1/sensors",
    response_model=SensorListResponse,
    response_model_exclude_none=False,
)
async def list_sensors(request: Request) -> SensorListResponse:
    server_time = utc_now()
    config = request.app.state.config
    db = request.app.state.db
    last_seen: dict[str, Any] = request.app.state.last_seen

    entries: list[SensorListEntry] = []
    for s in config.sensors:
        last_seen_dt: datetime | None = None
        if s.role == "outdoor":
            ts = db_module.latest_outdoor_timestamp(db)
            if ts is not None:
                last_seen_dt = datetime.fromtimestamp(ts, tz=UTC)
        else:
            entry = last_seen.get(s.id)
            if entry is not None:
                _, last_seen_dt = entry

        age = (server_time - last_seen_dt).total_seconds() if last_seen_dt is not None else None
        online = age is not None and age < s.online_threshold_seconds

        entries.append(
            SensorListEntry(
                sensor_id=s.id,
                role=s.role,
                ip=s.ip,
                has_gps=s.has_gps,
                has_light=s.has_light,
                logged=(s.role == "outdoor"),
                last_seen=last_seen_dt,
                age_seconds=round(age, 1) if age is not None else None,
                online=online,
            )
        )

    return SensorListResponse(server_time=server_time, sensors=entries)
