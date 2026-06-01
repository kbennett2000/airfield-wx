import sqlite3
from pathlib import Path

import pytest

from weather_server import db

# The v1 schema (pre-anemometer): the original 17 columns, no wind. Used to
# build a realistic old-deployment DB and prove the v1→v2 migration is lossless.
_V1_SCHEMA_SQL = """
CREATE TABLE outdoor_readings (
    id              INTEGER PRIMARY KEY,
    timestamp       INTEGER NOT NULL,
    temperature_c   REAL,
    humidity_pct    REAL,
    pressure_pa     REAL,
    lux             REAL,
    ir              INTEGER,
    visible         INTEGER,
    full_spectrum   INTEGER,
    latitude        REAL,
    longitude       REAL,
    altitude_m      REAL,
    satellites      INTEGER,
    speed_kmh       REAL,
    course_deg      REAL,
    rssi_dbm        INTEGER,
    uptime_s        INTEGER,
    free_heap_bytes INTEGER
);
"""


def _build_v1_db(path: Path, rows: list[tuple[int, float]]) -> None:
    """Create a populated v1-shaped DB (user_version=1, no wind columns)."""
    c = sqlite3.connect(str(path), isolation_level=None)
    c.executescript(_V1_SCHEMA_SQL)
    for ts, temp in rows:
        c.execute(
            "INSERT INTO outdoor_readings (timestamp, temperature_c) VALUES (?, ?)",
            (ts, temp),
        )
    c.execute("PRAGMA user_version = 1")
    c.close()


def _columns(conn: sqlite3.Connection) -> set[str]:
    return {r[1] for r in conn.execute("PRAGMA table_info(outdoor_readings)").fetchall()}


@pytest.fixture
def conn(tmp_path: Path):
    c = db.init_db(tmp_path / "test.db")
    yield c
    c.close()


def test_init_creates_schema_and_sets_version(tmp_path: Path) -> None:
    c = db.init_db(tmp_path / "fresh.db")
    version = c.execute("PRAGMA user_version").fetchone()[0]
    assert version == db.SCHEMA_VERSION
    journal = c.execute("PRAGMA journal_mode").fetchone()[0]
    assert journal == "wal"
    c.close()


def test_init_is_idempotent(tmp_path: Path) -> None:
    p = tmp_path / "idem.db"
    db.init_db(p).close()
    c2 = db.init_db(p)
    assert c2.execute("PRAGMA user_version").fetchone()[0] == db.SCHEMA_VERSION
    c2.close()


def test_insert_and_latest_round_trip(conn) -> None:
    payload = {
        "temperature_c": 18.4,
        "humidity_pct": 42.1,
        "pressure_pa": 80443,
        "lux": 12450.0,
        "ir": 230,
        "visible": 8200,
        "full_spectrum": 8430,
        "latitude": 39.7392,
        "longitude": -104.9903,
        "altitude_m": 1609.3,
        "satellites": 9,
        "speed_kmh": 0.0,
        "course_deg": 0.0,
        "rssi_dbm": -62,
        "uptime_s": 84320,
        "free_heap_bytes": 178432,
    }
    rowid = db.insert_outdoor_reading(conn, timestamp=1716393000, payload=payload)
    assert rowid > 0

    row = db.latest_outdoor_reading(conn)
    assert row is not None
    assert row["timestamp"] == 1716393000
    assert row["temperature_c"] == pytest.approx(18.4)
    assert row["full_spectrum"] == 8430
    assert row["altitude_m"] == pytest.approx(1609.3)


def test_latest_returns_none_on_empty_table(conn) -> None:
    assert db.latest_outdoor_reading(conn) is None
    assert db.latest_outdoor_timestamp(conn) is None


def test_range_query_orders_ascending(conn) -> None:
    for ts in (1000, 3000, 2000):
        db.insert_outdoor_reading(conn, timestamp=ts, payload={"temperature_c": float(ts)})
    rows = db.outdoor_readings_in_range(conn, 1500, 3500)
    assert [r["timestamp"] for r in rows] == [2000, 3000]


def test_partial_payload_stores_nulls(conn) -> None:
    db.insert_outdoor_reading(
        conn,
        timestamp=2000,
        payload={"temperature_c": 20.0, "humidity_pct": 50.0},
    )
    row = db.latest_outdoor_reading(conn)
    assert row is not None
    assert row["temperature_c"] == pytest.approx(20.0)
    assert row["pressure_pa"] is None
    assert row["latitude"] is None
    assert row["rssi_dbm"] is None


def test_db_ok(conn) -> None:
    assert db.db_ok(conn) is True


# ── schema migration (v1 → v2) ────────────────────────────────────────────────


def test_fresh_init_is_v2_with_wind_columns(tmp_path: Path) -> None:
    c = db.init_db(tmp_path / "fresh.db")
    assert c.execute("PRAGMA user_version").fetchone()[0] == 2
    cols = _columns(c)
    assert {"wind_speed_ms", "wind_gust_ms", "wind_direction_deg"} <= cols
    c.close()


def test_migrate_v1_to_v2_preserves_all_rows(tmp_path: Path) -> None:
    p = tmp_path / "legacy.db"
    rows = [(1000, 10.0), (2000, 11.5), (3000, 12.25), (4000, 9.0)]
    _build_v1_db(p, rows)

    # Sanity: it really is a v1 DB with no wind columns.
    pre = sqlite3.connect(str(p))
    assert pre.execute("PRAGMA user_version").fetchone()[0] == 1
    assert "wind_speed_ms" not in _columns(pre)
    pre.close()

    c = db.init_db(p)  # migrates forward
    assert c.execute("PRAGMA user_version").fetchone()[0] == 2
    assert {"wind_speed_ms", "wind_gust_ms", "wind_direction_deg"} <= _columns(c)

    # Every original row preserved, in order, with NULL wind.
    got = c.execute(
        "SELECT timestamp, temperature_c, wind_speed_ms, wind_gust_ms, "
        "wind_direction_deg FROM outdoor_readings ORDER BY timestamp"
    ).fetchall()
    assert [(r["timestamp"], r["temperature_c"]) for r in got] == rows
    assert all(
        r["wind_speed_ms"] is None
        and r["wind_gust_ms"] is None
        and r["wind_direction_deg"] is None
        for r in got
    )
    c.close()


def test_migration_is_idempotent(tmp_path: Path) -> None:
    p = tmp_path / "legacy.db"
    _build_v1_db(p, [(1000, 10.0)])
    db.init_db(p).close()  # v1 → v2
    # Opening the already-migrated DB must not re-run ALTERs or error.
    c2 = db.init_db(p)
    assert c2.execute("PRAGMA user_version").fetchone()[0] == 2
    assert c2.execute("SELECT COUNT(*) FROM outdoor_readings").fetchone()[0] == 1
    c2.close()


def test_newer_db_version_raises(tmp_path: Path) -> None:
    p = tmp_path / "future.db"
    db.init_db(p).close()  # v2
    c = sqlite3.connect(str(p), isolation_level=None)
    c.execute("PRAGMA user_version = 3")
    c.close()
    with pytest.raises(RuntimeError, match="version"):
        db.init_db(p)


def test_logger_persists_wind_columns(conn) -> None:
    db.insert_outdoor_reading(
        conn,
        timestamp=5000,
        payload={
            "temperature_c": 18.0,
            "wind_speed_ms": 5.2,
            "wind_gust_ms": 8.1,
            "wind_direction_deg": 270.0,
        },
    )
    row = db.latest_outdoor_reading(conn)
    assert row is not None
    assert row["wind_speed_ms"] == pytest.approx(5.2)
    assert row["wind_gust_ms"] == pytest.approx(8.1)
    assert row["wind_direction_deg"] == pytest.approx(270.0)


def test_reading_without_wind_stores_null(conn) -> None:
    db.insert_outdoor_reading(conn, timestamp=6000, payload={"temperature_c": 18.0})
    row = db.latest_outdoor_reading(conn)
    assert row is not None
    assert row["wind_speed_ms"] is None
    assert row["wind_direction_deg"] is None
