import asyncio
from pathlib import Path

import pytest

from weather_server import db as db_module
from weather_server.config import load_config_from_dict
from weather_server.logger_task import (
    _prefill_from_fixture,
    outdoor_logger_loop,
    wind_logger_loop,
)
from weather_server.sensors import FixtureSensorSource


def _config(fixture_dir: str) -> object:
    return load_config_from_dict(
        {
            "logger": {"interval_seconds": 1},
            "development": {"fixture_dir": fixture_dir},
            "sensors": [
                {"id": "outdoor", "role": "outdoor", "ip": "x"},
                {"id": "aux", "role": "aux", "ip": "y"},
            ],
        }
    )


def _write_outdoor(d: Path, n: int = 3) -> None:
    rows = ",".join(f'{{"temperature_c": {i}}}' for i in range(1, n + 1))
    (d / "outdoor.json").write_text(f"[{rows}]")


def test_prefill_inserts_all_fixture_rows(tmp_path: Path) -> None:
    _write_outdoor(tmp_path, 5)
    conn = db_module.init_db(tmp_path / "t.db")
    src = FixtureSensorSource(tmp_path)
    _prefill_from_fixture(conn, src, interval=60)
    rows = conn.execute(
        "SELECT temperature_c FROM outdoor_readings ORDER BY timestamp"
    ).fetchall()
    assert [r[0] for r in rows] == [1.0, 2.0, 3.0, 4.0, 5.0]
    conn.close()


def test_prefill_noop_when_db_already_has_rows(tmp_path: Path) -> None:
    _write_outdoor(tmp_path, 5)
    conn = db_module.init_db(tmp_path / "t.db")
    db_module.insert_outdoor_reading(conn, timestamp=999, payload={"temperature_c": 99.0})
    src = FixtureSensorSource(tmp_path)
    _prefill_from_fixture(conn, src, interval=60)
    rows = conn.execute("SELECT COUNT(*) FROM outdoor_readings").fetchone()[0]
    assert rows == 1
    conn.close()


def test_prefill_skips_offline_marker(tmp_path: Path) -> None:
    (tmp_path / "outdoor.json").write_text(
        '[{"temperature_c": 1}, {"offline": true}, {"temperature_c": 3}]'
    )
    conn = db_module.init_db(tmp_path / "t.db")
    src = FixtureSensorSource(tmp_path)
    _prefill_from_fixture(conn, src, interval=60)
    rows = conn.execute("SELECT temperature_c FROM outdoor_readings").fetchall()
    assert [r[0] for r in rows] == [1.0, 3.0]
    conn.close()


@pytest.mark.asyncio
async def test_logger_loop_writes_rows_and_cancels_cleanly(tmp_path: Path) -> None:
    _write_outdoor(tmp_path, 2)
    config = _config(str(tmp_path))
    conn = db_module.init_db(tmp_path / "t.db")
    src = FixtureSensorSource(tmp_path)

    task = asyncio.create_task(outdoor_logger_loop(config, src, conn))  # type: ignore[arg-type]
    await asyncio.sleep(0.05)  # let prefill + first poll happen
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    count = conn.execute("SELECT COUNT(*) FROM outdoor_readings").fetchone()[0]
    assert count >= 2  # 2 prefilled, plus possibly the first live tick
    conn.close()


# ── wind logger loop (ADR-0006 / topology 3) ─────────────────────────────────


def _wind_config(fixture_dir: str, *, source: str) -> object:
    return load_config_from_dict(
        {
            "logger": {"interval_seconds": 1},
            "development": {"fixture_dir": fixture_dir},
            "wind": {"source": source},
            "sensors": [
                {"id": "outdoor", "role": "outdoor", "ip": "x"},
                {"id": "windy", "role": "wind_station", "ip": "y", "has_wind": True},
            ],
        }
    )


@pytest.mark.asyncio
async def test_wind_logger_loop_writes_wind_rows(tmp_path: Path) -> None:
    (tmp_path / "windy.json").write_text(
        '{"wind_speed_ms": 6.4, "wind_gust_ms": 9.1, "wind_direction_deg": 200.0}'
    )
    config = _wind_config(str(tmp_path), source="windy")
    conn = db_module.init_db(tmp_path / "t.db")
    src = FixtureSensorSource(tmp_path)

    task = asyncio.create_task(wind_logger_loop(config, src, conn))  # type: ignore[arg-type]
    await asyncio.sleep(0.05)  # let the first poll + insert happen
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    row = db_module.latest_wind_reading(conn)
    assert row is not None
    assert row["wind_speed_ms"] == pytest.approx(6.4)
    assert row["wind_direction_deg"] == pytest.approx(200.0)
    conn.close()


@pytest.mark.asyncio
async def test_wind_logger_loop_inert_when_source_outdoor(tmp_path: Path) -> None:
    # Default topology: wind rides the outdoor payload, so the wind logger
    # returns immediately and writes nothing to wind_readings.
    config = _wind_config(str(tmp_path), source="outdoor")
    conn = db_module.init_db(tmp_path / "t.db")
    src = FixtureSensorSource(tmp_path)

    await wind_logger_loop(config, src, conn)  # type: ignore[arg-type]  # returns at once

    count = conn.execute("SELECT COUNT(*) FROM wind_readings").fetchone()[0]
    assert count == 0
    conn.close()
