"""Background task that refreshes the external observation on a timer.

Mirrors logger_task.outdoor_logger_loop: a cancellable loop that catches
every exception so a flaky network can never take the server down. It runs
OUTSIDE the request path, so a slow upstream never delays /api/v1/current.
When [external] is disabled the loop is never spawned.
"""

from __future__ import annotations

import asyncio
import logging
import sqlite3
from datetime import UTC, datetime

from ..config import Config
from ..db import latest_outdoor_reading
from .providers import fetch_external
from .store import ExternalStore

log = logging.getLogger(__name__)


def resolve_reference_location(
    config: Config, db: sqlite3.Connection
) -> tuple[float, float] | None:
    """Reference coords for the external fetch: explicit override, then the
    outdoor sensor's latest GPS, then its configured fallback. None if no
    location is known (⇒ skip the fetch)."""
    ext = config.external
    if ext.lat_override is not None and ext.lon_override is not None:
        return ext.lat_override, ext.lon_override

    try:
        row = latest_outdoor_reading(db)
    except sqlite3.Error:
        row = None
    if row is not None:
        lat = row["latitude"]
        lon = row["longitude"]
        if lat is not None and lon is not None:
            return float(lat), float(lon)

    outdoor = config.outdoor
    if (
        outdoor is not None
        and outdoor.fallback_lat is not None
        and outdoor.fallback_lon is not None
    ):
        return outdoor.fallback_lat, outdoor.fallback_lon
    return None


async def external_fetch_loop(
    config: Config, db: sqlite3.Connection, store: ExternalStore
) -> None:
    ext = config.external
    if not ext.enabled:
        log.info("external feed disabled; fetch task exiting")
        return

    interval = ext.refresh_interval_seconds
    log.info("external feed enabled (provider=%s, every %ss)", ext.provider, interval)

    while True:
        try:
            ref = resolve_reference_location(config, db)
            if ref is None:
                log.info("external fetch skipped: no reference location yet")
            else:
                lat, lon = ref
                obs = await asyncio.to_thread(fetch_external, ext, lat, lon)
                if obs is not None:
                    store.set(obs, datetime.now(UTC))
                    log.debug("external observation refreshed from %s", obs.source)
                else:
                    log.info("external fetch returned no data; keeping last-known")
        except asyncio.CancelledError:
            log.info("external fetch task cancelled")
            raise
        except Exception:
            log.exception("external fetch iteration failed")
        await asyncio.sleep(interval)
