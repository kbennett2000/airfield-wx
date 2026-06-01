"""SQLite layer for outdoor readings.

Single table, one index, WAL mode. The CREATE TABLE statement below is
the canonical schema (an earlier docs/design/03-schema.md described it,
but that design doc was deleted post-rebuild). All conversions to
derived values happen in derivations/ at request time.

This module uses Python's stdlib sqlite3. Connections are not pooled: the
logger writes from one async task, request handlers read. SQLite + WAL
handles that pattern fine; `check_same_thread=False` is the only knob we
need so the event-loop thread can share a connection between the logger
task and the route handlers.
"""

from __future__ import annotations

import logging
import sqlite3
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

SCHEMA_VERSION = 2

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS outdoor_readings (
    id                  INTEGER PRIMARY KEY,
    timestamp           INTEGER NOT NULL,

    temperature_c       REAL,
    humidity_pct        REAL,
    pressure_pa         REAL,

    lux                 REAL,
    ir                  INTEGER,
    visible             INTEGER,
    full_spectrum       INTEGER,

    latitude            REAL,
    longitude           REAL,
    altitude_m          REAL,
    satellites          INTEGER,
    speed_kmh           REAL,
    course_deg          REAL,

    wind_speed_ms       REAL,
    wind_gust_ms        REAL,
    wind_direction_deg  REAL,

    rssi_dbm            INTEGER,
    uptime_s            INTEGER,
    free_heap_bytes     INTEGER
);

CREATE INDEX IF NOT EXISTS idx_outdoor_readings_timestamp
    ON outdoor_readings (timestamp);
"""

# Forward-only schema migrations, keyed by the from-version. Each opens the
# door to the next: applying _MIGRATIONS[1] takes a v1 DB to v2. ALTER TABLE
# ADD COLUMN leaves existing rows with NULL — no data rewrite, no loss.
_MIGRATIONS: dict[int, tuple[str, ...]] = {
    1: (  # v1 -> v2: local anemometer (decision 4 / ADR-0003)
        "ALTER TABLE outdoor_readings ADD COLUMN wind_speed_ms REAL",
        "ALTER TABLE outdoor_readings ADD COLUMN wind_gust_ms REAL",
        "ALTER TABLE outdoor_readings ADD COLUMN wind_direction_deg REAL",
    ),
}


def _migrate(conn: sqlite3.Connection, from_version: int, to_version: int) -> None:
    """Apply forward migrations from `from_version` to `to_version`, bumping the
    stored version after each step so a partial run can resume."""
    version = from_version
    while version < to_version:
        steps = _MIGRATIONS.get(version)
        if steps is None:
            raise RuntimeError(f"no migration path from schema v{version}")
        for stmt in steps:
            conn.execute(stmt)
        version += 1
        conn.execute(f"PRAGMA user_version = {version}")
    log.info("migrated db schema v%s → v%s", from_version, to_version)

PRAGMAS = (
    "PRAGMA journal_mode = WAL",
    "PRAGMA synchronous = NORMAL",
    "PRAGMA foreign_keys = ON",
    "PRAGMA busy_timeout = 5000",
)


def init_db(db_path: str | Path) -> sqlite3.Connection:
    """Open the DB, apply pragmas, ensure schema, return the connection.

    Safe to call repeatedly; uses CREATE IF NOT EXISTS.
    """
    db_path = Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path), check_same_thread=False, isolation_level=None)
    conn.row_factory = sqlite3.Row
    for pragma in PRAGMAS:
        conn.execute(pragma)
    conn.executescript(SCHEMA_SQL)  # no-op for an existing table; full schema for a fresh file
    current_version = conn.execute("PRAGMA user_version").fetchone()[0]
    if current_version == 0:
        # Brand-new file, just created at the latest schema.
        conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
    elif current_version < SCHEMA_VERSION:
        _migrate(conn, current_version, SCHEMA_VERSION)
    elif current_version > SCHEMA_VERSION:
        # Migrate forward only; never silently downgrade a newer DB.
        raise RuntimeError(
            f"DB schema version too new: file has {current_version}, "
            f"code expects {SCHEMA_VERSION}"
        )
    log.info("db opened at %s (schema v%s)", db_path, SCHEMA_VERSION)
    return conn


OUTDOOR_COLUMNS = (
    "timestamp",
    "temperature_c",
    "humidity_pct",
    "pressure_pa",
    "lux",
    "ir",
    "visible",
    "full_spectrum",
    "latitude",
    "longitude",
    "altitude_m",
    "satellites",
    "speed_kmh",
    "course_deg",
    "wind_speed_ms",
    "wind_gust_ms",
    "wind_direction_deg",
    "rssi_dbm",
    "uptime_s",
    "free_heap_bytes",
)


def insert_outdoor_reading(
    conn: sqlite3.Connection,
    timestamp: int,
    payload: dict[str, Any],
) -> int:
    """Insert one row. Returns the rowid.

    `payload` is a SensorPayload-shaped dict (the same shape the fixture files
    use). Any column not present in the payload is stored as NULL.
    """
    values = [timestamp] + [payload.get(c) for c in OUTDOOR_COLUMNS[1:]]
    placeholders = ", ".join("?" for _ in OUTDOOR_COLUMNS)
    cols = ", ".join(OUTDOOR_COLUMNS)
    cur = conn.execute(
        f"INSERT INTO outdoor_readings ({cols}) VALUES ({placeholders})",
        values,
    )
    return cur.lastrowid or 0


def latest_outdoor_reading(conn: sqlite3.Connection) -> sqlite3.Row | None:
    """Return the most recent row, or None if the table is empty."""
    row: sqlite3.Row | None = conn.execute(
        "SELECT * FROM outdoor_readings ORDER BY timestamp DESC LIMIT 1"
    ).fetchone()
    return row


def outdoor_readings_in_range(
    conn: sqlite3.Connection,
    from_ts: int,
    to_ts: int,
) -> list[sqlite3.Row]:
    """Return raw rows in [from_ts, to_ts], ordered ascending."""
    rows = conn.execute(
        "SELECT * FROM outdoor_readings "
        "WHERE timestamp BETWEEN ? AND ? "
        "ORDER BY timestamp ASC",
        (from_ts, to_ts),
    ).fetchall()
    return list(rows)


def db_ok(conn: sqlite3.Connection) -> bool:
    """Cheap liveness probe used by /api/v1/health."""
    try:
        conn.execute("SELECT 1").fetchone()
        return True
    except sqlite3.Error:
        log.exception("db liveness probe failed")
        return False


def latest_outdoor_timestamp(conn: sqlite3.Connection) -> int | None:
    """Used by /api/v1/health to report how stale the outdoor logger is."""
    row = conn.execute(
        "SELECT timestamp FROM outdoor_readings ORDER BY timestamp DESC LIMIT 1"
    ).fetchone()
    return int(row[0]) if row else None
