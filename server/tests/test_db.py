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


# The v2 schema (pre-separate-wind-station): outdoor_readings WITH the wind
# columns, but no wind_readings table. Used to prove the v2→v3 migration is
# additive and lossless against a populated old deployment.
_V2_SCHEMA_SQL = """
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
    wind_speed_ms       REAL,
    wind_gust_ms        REAL,
    wind_direction_deg  REAL,
    rssi_dbm        INTEGER,
    uptime_s        INTEGER,
    free_heap_bytes INTEGER
);
"""


def _build_v2_db(path: Path, rows: list[tuple[int, float, float]]) -> None:
    """Create a populated v2-shaped DB (user_version=2, wind columns present
    on outdoor_readings, no wind_readings table). Each row is
    (timestamp, temperature_c, wind_speed_ms)."""
    c = sqlite3.connect(str(path), isolation_level=None)
    c.executescript(_V2_SCHEMA_SQL)
    for ts, temp, wind in rows:
        c.execute(
            "INSERT INTO outdoor_readings (timestamp, temperature_c, wind_speed_ms) "
            "VALUES (?, ?, ?)",
            (ts, temp, wind),
        )
    c.execute("PRAGMA user_version = 2")
    c.close()


def _columns(conn: sqlite3.Connection) -> set[str]:
    return {r[1] for r in conn.execute("PRAGMA table_info(outdoor_readings)").fetchall()}


def _tables(conn: sqlite3.Connection) -> set[str]:
    return {
        r[0]
        for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        ).fetchall()
    }


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


def test_fresh_init_is_v3_with_wind_columns_and_table(tmp_path: Path) -> None:
    c = db.init_db(tmp_path / "fresh.db")
    assert c.execute("PRAGMA user_version").fetchone()[0] == 3
    cols = _columns(c)
    assert {"wind_speed_ms", "wind_gust_ms", "wind_direction_deg"} <= cols
    assert "wind_readings" in _tables(c)
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

    c = db.init_db(p)  # migrates forward v1 → v3
    assert c.execute("PRAGMA user_version").fetchone()[0] == 3
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
    db.init_db(p).close()  # v1 → v3
    # Opening the already-migrated DB must not re-run ALTERs/CREATEs or error.
    c2 = db.init_db(p)
    assert c2.execute("PRAGMA user_version").fetchone()[0] == 3
    assert c2.execute("SELECT COUNT(*) FROM outdoor_readings").fetchone()[0] == 1
    c2.close()


def test_newer_db_version_raises(tmp_path: Path) -> None:
    p = tmp_path / "future.db"
    db.init_db(p).close()  # v3
    c = sqlite3.connect(str(p), isolation_level=None)
    c.execute("PRAGMA user_version = 4")
    c.close()
    with pytest.raises(RuntimeError, match="version"):
        db.init_db(p)


# ── schema migration (v2 → v3: separate wind station) ──────────────────────────


def test_migrate_v2_to_v3_adds_wind_table_and_preserves_rows(tmp_path: Path) -> None:
    p = tmp_path / "legacy_v2.db"
    rows = [(1000, 10.0, 3.0), (2000, 11.5, 4.2), (3000, 12.25, 0.0)]
    _build_v2_db(p, rows)

    # Sanity: a real v2 DB — wind columns present, no wind_readings table.
    pre = sqlite3.connect(str(p))
    assert pre.execute("PRAGMA user_version").fetchone()[0] == 2
    assert "wind_speed_ms" in _columns(pre)
    assert "wind_readings" not in _tables(pre)
    pre.close()

    c = db.init_db(p)  # migrates forward v2 → v3
    assert c.execute("PRAGMA user_version").fetchone()[0] == 3
    assert "wind_readings" in _tables(c)
    # The new table starts empty.
    assert c.execute("SELECT COUNT(*) FROM wind_readings").fetchone()[0] == 0

    # Every outdoor row preserved untouched, including its wind columns.
    got = c.execute(
        "SELECT timestamp, temperature_c, wind_speed_ms FROM outdoor_readings "
        "ORDER BY timestamp"
    ).fetchall()
    assert [(r["timestamp"], r["temperature_c"], r["wind_speed_ms"]) for r in got] == rows
    c.close()


def test_migrate_v2_to_v3_is_idempotent(tmp_path: Path) -> None:
    p = tmp_path / "legacy_v2.db"
    _build_v2_db(p, [(1000, 10.0, 3.0)])
    db.init_db(p).close()  # v2 → v3
    c2 = db.init_db(p)
    assert c2.execute("PRAGMA user_version").fetchone()[0] == 3
    assert c2.execute("SELECT COUNT(*) FROM outdoor_readings").fetchone()[0] == 1
    assert "wind_readings" in _tables(c2)
    c2.close()


def test_wind_readings_insert_and_latest_round_trip(conn) -> None:
    db.insert_wind_reading(
        conn,
        timestamp=7000,
        payload={
            "wind_speed_ms": 6.4,
            "wind_gust_ms": 9.1,
            "wind_direction_deg": 200.0,
            "rssi_dbm": -55,
            "uptime_s": 12345,
        },
    )
    row = db.latest_wind_reading(conn)
    assert row is not None
    assert row["timestamp"] == 7000
    assert row["wind_speed_ms"] == pytest.approx(6.4)
    assert row["wind_direction_deg"] == pytest.approx(200.0)
    assert row["rssi_dbm"] == -55


def test_latest_wind_reading_none_on_empty(conn) -> None:
    assert db.latest_wind_reading(conn) is None


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
