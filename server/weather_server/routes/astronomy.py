"""GET /api/v1/astronomy."""

from __future__ import annotations

from fastapi import APIRouter, Query, Request

from ..outdoor_source import outdoor_row_for_request
from ..responses import build_astronomy, build_outdoor_reading_from_db_row, utc_now
from ..schemas import AstronomyResponse

router = APIRouter()


@router.get(
    "/api/v1/astronomy",
    response_model=AstronomyResponse,
    response_model_exclude_none=False,
)
async def get_astronomy(
    request: Request,
    lat: float | None = Query(None, ge=-90, le=90),
    lon: float | None = Query(None, ge=-180, le=180),
) -> AstronomyResponse:
    server_time = utc_now()
    config = request.app.state.config

    outdoor_reading = None
    row = await outdoor_row_for_request(request.app.state, server_time)
    if config.outdoor is not None and row is not None:
        outdoor_reading = build_outdoor_reading_from_db_row(config.outdoor, row, server_time)

    astronomy = build_astronomy(
        server_time,
        config,
        outdoor_reading,
        lat_override=lat,
        lon_override=lon,
    )
    return AstronomyResponse(server_time=server_time, astronomy=astronomy)
