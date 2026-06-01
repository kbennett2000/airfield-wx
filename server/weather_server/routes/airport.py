"""GET /api/v1/airport."""

from __future__ import annotations

from fastapi import APIRouter, Query, Request

from .. import db as db_module
from ..responses import build_airport, build_outdoor_reading_from_db_row, utc_now
from ..schemas import AirportResponse

router = APIRouter()


@router.get(
    "/api/v1/airport",
    response_model=AirportResponse,
    response_model_exclude_none=False,
)
async def get_airport(
    request: Request,
    lat: float | None = Query(None, ge=-90, le=90),
    lon: float | None = Query(None, ge=-180, le=180),
) -> AirportResponse:
    server_time = utc_now()
    config = request.app.state.config
    db = request.app.state.db

    outdoor_reading = None
    if config.outdoor is not None:
        row = db_module.latest_outdoor_reading(db)
        if row is not None:
            outdoor_reading = build_outdoor_reading_from_db_row(config.outdoor, row, server_time)

    airport = build_airport(
        server_time,
        config,
        outdoor_reading,
        lat_override=lat,
        lon_override=lon,
    )
    return AirportResponse(server_time=server_time, airport=airport)
