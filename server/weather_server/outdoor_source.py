"""Source the current outdoor reading, honoring the logging toggle (Cycle 13).

When history logging is ON, the current outdoor reading is the latest LOGGED
row (today's behavior). When logging is OFF, the station is stateless — there
is no logged row — so the reading comes from a live, TTL-cached poll of the
outdoor sensor, shaped as a row for the shared builder. This decouples the live
instrument (/current, /airport, /astronomy, /external) from history: the flight
outputs are read-time derivations off the *current* reading and never need the
log.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime
from typing import Any

from . import db as db_module
from .cache import TTLCache
from .config import SensorConfig
from .sensors import SensorPayload, SensorSource


async def poll_with_cache(
    cache: TTLCache,
    source: SensorSource,
    sensor: SensorConfig,
    *,
    ttl: float,
) -> SensorPayload | None:
    """Poll a sensor through the TTL cache (collapses near-simultaneous reads)."""

    async def fetch() -> SensorPayload | None:
        return await source.poll(sensor)

    return await cache.get_or_fetch(sensor.id, fetch, ttl=ttl)


async def outdoor_row_for_request(
    state: Any, server_time: datetime
) -> sqlite3.Row | dict[str, Any] | None:
    """The current outdoor reading as a row, for the read-time endpoints.

    `state` is `request.app.state`. Returns the latest logged row when logging
    is enabled, or a freshly polled (TTL-cached) row when it's disabled. None
    when there is no outdoor sensor or it has never reported. The returned
    Mapping carries a `timestamp` + the payload keys, so it feeds
    `build_outdoor_reading_from_db_row` and `resolve_wind` unchanged.
    """
    config = state.config
    outdoor = config.outdoor
    if outdoor is None:
        return None

    if config.logging.enabled:
        return db_module.latest_outdoor_reading(state.db)

    # Logging OFF: poll live, TTL-cached, tracked in last_seen so a sensor that
    # goes quiet ages into "offline" instead of looking perpetually fresh.
    last_seen: dict[str, Any] = state.last_seen
    payload = await poll_with_cache(
        state.cache, state.source, outdoor, ttl=config.cache.ttl_seconds
    )
    if payload is not None:
        last_seen[outdoor.id] = (payload, server_time)
        reading_ts = server_time
    else:
        entry = last_seen.get(outdoor.id)
        if entry is None:
            return None
        payload, reading_ts = entry
    return {**payload, "timestamp": int(reading_ts.timestamp())}
