from pathlib import Path

import pytest

from weather_server import db


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
