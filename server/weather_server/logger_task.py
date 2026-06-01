"""Outdoor logger background task.

Polls the outdoor sensor on a fixed cadence and writes one row per poll
to outdoor_readings. In fixture mode, on first run, prefills the DB with
the entire fixture sweep at staggered backdated timestamps so the
history endpoint returns useful data immediately after startup.
"""

from __future__ import annotations

import asyncio
import logging
import sqlite3
import time

from .config import Config
from .db import insert_outdoor_reading, latest_outdoor_timestamp
from .sensors import SensorSource

log = logging.getLogger(__name__)


async def outdoor_logger_loop(
    config: Config,
    source: SensorSource,
    db: sqlite3.Connection,
) -> None:
    # Phase 1 / fixture-mode quirk: on first start (empty DB), prefill the
    # outdoor table with the entire fixture sweep at backdated timestamps
    # so /api/v1/history returns useful data immediately for dashboard
    # development. No-op in real-polling mode or if the DB already has
    # rows. See _prefill_from_fixture.
    outdoor = config.outdoor
    if outdoor is None:
        log.warning("no outdoor sensor configured; logger task exiting")
        return

    interval = config.logger.interval_seconds

    if config.fixture_mode:
        _prefill_from_fixture(db, source, interval)

    while True:
        try:
            payload = await source.poll(outdoor)
            if payload is not None:
                ts = int(time.time())
                insert_outdoor_reading(db, timestamp=ts, payload=payload)
                log.debug("logger wrote outdoor row at ts=%s", ts)
            else:
                log.info("outdoor poll returned offline; no row written")
        except asyncio.CancelledError:
            log.info("outdoor logger cancelled")
            raise
        except Exception:
            log.exception("outdoor logger iteration failed")
        await asyncio.sleep(interval)


def _prefill_from_fixture(
    db: sqlite3.Connection,
    source: SensorSource,
    interval: int,
) -> None:
    """If the DB is empty, insert every fixture outdoor row backdated by
    `interval` seconds each so history starts populated."""
    if latest_outdoor_timestamp(db) is not None:
        return
    rows = source.all_outdoor_rows()
    if not rows:
        return
    now = int(time.time())
    base = now - (len(rows) - 1) * interval
    for i, row in enumerate(rows):
        if row.get("offline"):
            continue
        payload = {k: v for k, v in row.items() if k != "offline"}
        insert_outdoor_reading(db, timestamp=base + i * interval, payload=payload)
    log.info("prefilled %d outdoor rows from fixture", len(rows))
