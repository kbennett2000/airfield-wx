"""FastAPI app entry point.

Lifespan responsibilities:
- Load config from weather.toml (or $WEATHER_CONFIG).
- Open SQLite, apply pragmas, ensure schema.
- Build a SensorSource (fixture mode in Phase 1).
- Spawn the outdoor logger task; cancel and await on shutdown.
"""

from __future__ import annotations

import asyncio
import logging
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles

from . import __version__
from .branding import load_branding
from .cache import TTLCache
from .config import load_config
from .db import init_db
from .external import ExternalStore
from .external.task import external_fetch_loop
from .logger_task import outdoor_logger_loop, wind_logger_loop
from .routes import (
    airport,
    astronomy,
    branding,
    current,
    external,
    health,
    history,
    sensors,
    summary,
)
from .schemas import ErrorBody, ErrorResponse
from .sensors import make_source

log = logging.getLogger(__name__)

CONFIG_ENV = "WEATHER_CONFIG"
DEFAULT_CONFIG_PATH = "weather.toml"


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    config_path = os.environ.get(CONFIG_ENV, DEFAULT_CONFIG_PATH)
    log.info("loading config from %s", config_path)
    config = load_config(config_path)

    db_conn = init_db(config.server.db_path)
    cache = TTLCache(default_ttl=config.cache.ttl_seconds)
    source = make_source(
        config.development.fixture_dir,
        http_timeout_seconds=config.logger.http_timeout_seconds,
    )

    external_store = ExternalStore()

    app.state.config = config
    app.state.db = db_conn
    app.state.cache = cache
    app.state.source = source
    app.state.last_seen = {}
    app.state.external_store = external_store
    app.state.branding = load_branding(config.server.branding_path)

    # Optional: only spawns a live loop when [external] is enabled; otherwise
    # returns immediately and the store stays empty (external block ⇒ null).
    external_task = asyncio.create_task(
        external_fetch_loop(config, db_conn, external_store)
    )
    app.state.external_task = external_task

    tasks = [external_task]

    # History logging is OFF by default (Cycle 13): airfield-wx is a
    # live-conditions instrument, so by default the station is stateless — no
    # logger loops run and nothing is written. Both the outdoor logger and the
    # separate wind-station logger (ADR-0006 / topology 3) are gated on it.
    logger_task: asyncio.Task[None] | None = None
    wind_logger_task: asyncio.Task[None] | None = None
    if config.logging.enabled:
        logger_task = asyncio.create_task(outdoor_logger_loop(config, source, db_conn))
        tasks.append(logger_task)
        if config.wind.source != "outdoor":
            wind_logger_task = asyncio.create_task(wind_logger_loop(config, source, db_conn))
            tasks.append(wind_logger_task)
    app.state.logger_task = logger_task
    app.state.wind_logger_task = wind_logger_task

    try:
        yield
    finally:
        for task in tasks:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        db_conn.close()
        log.info("shutdown complete")


def create_app() -> FastAPI:
    app = FastAPI(
        title="airfield-wx API",
        version=__version__,
        description=(
            "Read-only HTTP API for weather sensor data. "
            "See docs/design/02-api-design.md for the full contract."
        ),
        lifespan=lifespan,
        responses={
            400: {"model": ErrorResponse},
            404: {"model": ErrorResponse},
            500: {"model": ErrorResponse},
            503: {"model": ErrorResponse},
        },
    )

    app.include_router(current.router, tags=["current"])
    app.include_router(history.router, tags=["history"])
    app.include_router(summary.router, tags=["summary"])
    app.include_router(sensors.router, tags=["sensors"])
    app.include_router(astronomy.router, tags=["astronomy"])
    app.include_router(airport.router, tags=["airport"])
    app.include_router(external.router, tags=["external"])
    app.include_router(branding.router, tags=["branding"])
    app.include_router(health.router, tags=["health"])

    @app.get("/", include_in_schema=False)
    async def _root_redirect() -> RedirectResponse:
        return RedirectResponse(url="/dashboard/")

    _mount_dashboard(app)

    @app.exception_handler(HTTPException)
    async def _http_exception_handler(_: Request, exc: HTTPException) -> JSONResponse:
        code, message = _classify(exc)
        body = ErrorResponse(error=ErrorBody(code=code, message=message))
        return JSONResponse(status_code=exc.status_code, content=body.model_dump(mode="json"))

    return app


class _NoCacheStaticFiles(StaticFiles):
    """Serve dashboard assets with ``Cache-Control: no-cache``.

    Plain StaticFiles emits only ETag/Last-Modified (no Cache-Control), which
    lets browsers heuristically cache app.js/style.css and run a stale build
    after a deploy. ``no-cache`` forces revalidation on every load — cheap on a
    LAN, where the existing ETag yields a 304 when nothing changed — so the
    dashboard can never execute an outdated script.
    """

    def file_response(self, *args: Any, **kwargs: Any) -> Response:
        response: Response = super().file_response(*args, **kwargs)
        response.headers["Cache-Control"] = "no-cache"
        return response


def _mount_dashboard(app: FastAPI) -> None:
    """Mount the dashboard static files at /dashboard/.

    The directory is resolved lazily from config at request time so the
    static mount survives a config swap during lifespan startup. If the
    directory doesn't exist yet (e.g. fresh checkout before Phase 3 is
    deployed), the mount is skipped with a warning rather than crashing.
    """
    config_path = os.environ.get(CONFIG_ENV, DEFAULT_CONFIG_PATH)
    try:
        config = load_config(config_path)
    except FileNotFoundError:
        log.warning("config not found at %s; dashboard mount skipped", config_path)
        return

    dashboard_dir = Path(config.server.dashboard_dir).resolve()
    if not dashboard_dir.is_dir():
        log.warning(
            "dashboard_dir %s does not exist; dashboard mount skipped", dashboard_dir
        )
        return

    app.mount(
        "/dashboard",
        _NoCacheStaticFiles(directory=str(dashboard_dir), html=True),
        name="dashboard",
    )
    log.info("dashboard mounted at /dashboard/ from %s", dashboard_dir)


def _classify(exc: HTTPException) -> tuple[str, str]:
    """Translate an HTTPException's detail tuple to the (code, message)
    pair documented in 02-api-design.md."""
    detail = exc.detail
    if isinstance(detail, tuple) and len(detail) >= 1:
        code = str(detail[0])
        arg = detail[1] if len(detail) > 1 else None
        return code, _message_for(code, arg)
    # Fallback for unstructured raises (e.g. FastAPI's own 422 validation).
    if exc.status_code == 404:
        return "not_found", str(detail)
    if exc.status_code == 400:
        return "bad_request", str(detail)
    if exc.status_code == 503:
        return "db_unavailable", str(detail)
    return "internal_error", str(detail)


def _message_for(code: str, arg: object) -> str:
    if code == "sensor_not_found":
        return f"No sensor with id {arg!r} is registered."
    if code == "history_not_available":
        return f"History is not available for sensor {arg!r} (only outdoor is logged)."
    if code == "logging_disabled":
        return (
            "History logging is disabled on this station. Enable it in "
            "weather.toml ([logging] enabled = true) to record trends."
        )
    if code == "sensor_no_data":
        return f"Sensor {arg!r} has not reported any data yet."
    if code == "bad_request":
        return str(arg) if arg is not None else "Bad request."
    return code.replace("_", " ").capitalize()


app = create_app()


def run() -> None:
    """Entry point for `weather-server` console script."""
    import uvicorn

    config_path = os.environ.get(CONFIG_ENV, DEFAULT_CONFIG_PATH)
    config = load_config(config_path)
    uvicorn.run(
        "weather_server.main:app",
        host=config.server.host,
        port=config.server.port,
        log_level="info",
    )


if __name__ == "__main__":
    run()
