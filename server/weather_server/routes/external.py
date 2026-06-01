"""GET /api/v1/external — internet-sourced regional conditions alone.

Mirrors /api/v1/astronomy: the same block that /api/v1/current embeds,
exposed standalone for consumers that only want regional data. Returns
``external: null`` when the feed is disabled or no data has arrived yet.
"""

from __future__ import annotations

from fastapi import APIRouter, Request

from .. import db as db_module
from ..responses import (
    build_external,
    build_outdoor_reading_from_db_row,
    external_stale_after,
    utc_now,
)
from ..schemas import ExternalResponse

router = APIRouter()


@router.get(
    "/api/v1/external",
    response_model=ExternalResponse,
    response_model_exclude_none=False,
)
async def get_external(request: Request) -> ExternalResponse:
    server_time = utc_now()
    config = request.app.state.config
    db = request.app.state.db

    # The fused indices (wind chill etc.) need the local outdoor reading.
    outdoor_reading = None
    if config.outdoor is not None:
        row = db_module.latest_outdoor_reading(db)
        if row is not None:
            outdoor_reading = build_outdoor_reading_from_db_row(config.outdoor, row, server_time)

    external = build_external(
        request.app.state.external_store.get(),
        server_time,
        stale_after_seconds=external_stale_after(config),
        outdoor_reading=outdoor_reading,
    )
    return ExternalResponse(server_time=server_time, external=external)
