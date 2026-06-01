"""External fetch task: disabled no-op, failure tolerance, ref-location."""

from __future__ import annotations

import asyncio

import pytest

from weather_server.config import load_config_from_dict
from weather_server.db import init_db, insert_outdoor_reading
from weather_server.external.store import ExternalStore
from weather_server.external.task import external_fetch_loop, resolve_reference_location

_BASE = {
    "server": {"db_path": ":memory:"},
    "sensors": [
        {
            "id": "outdoor",
            "role": "outdoor",
            "ip": "10.0.0.1",
            "has_gps": True,
            "fallback_lat": 39.4,
            "fallback_lon": -104.5,
        }
    ],
}


def _config(external: dict | None = None) -> object:
    raw = dict(_BASE)
    if external is not None:
        raw = {**_BASE, "external": external}
    return load_config_from_dict(raw)


def test_resolve_ref_location_prefers_override(tmp_path) -> None:
    db = init_db(tmp_path / "w.db")
    cfg = _config({"enabled": True, "lat_override": 1.0, "lon_override": 2.0})
    assert resolve_reference_location(cfg, db) == (1.0, 2.0)


def test_resolve_ref_location_uses_latest_gps(tmp_path) -> None:
    db = init_db(tmp_path / "w.db")
    insert_outdoor_reading(db, timestamp=1000, payload={"latitude": 12.0, "longitude": 34.0})
    cfg = _config({"enabled": True})
    assert resolve_reference_location(cfg, db) == (12.0, 34.0)


def test_resolve_ref_location_falls_back_to_config(tmp_path) -> None:
    db = init_db(tmp_path / "w.db")
    cfg = _config({"enabled": True})
    assert resolve_reference_location(cfg, db) == (39.4, -104.5)


async def test_loop_disabled_exits_immediately(tmp_path) -> None:
    db = init_db(tmp_path / "w.db")
    cfg = _config(None)  # external absent ⇒ disabled
    store = ExternalStore()
    # Should return promptly without ever fetching.
    await asyncio.wait_for(external_fetch_loop(cfg, db, store), timeout=1.0)
    assert store.get() is None


async def test_loop_survives_fetch_failure(tmp_path, monkeypatch) -> None:
    db = init_db(tmp_path / "w.db")
    cfg = _config({"enabled": True, "refresh_interval_seconds": 600})
    store = ExternalStore()

    calls = {"n": 0}

    def boom(*args, **kwargs):
        calls["n"] += 1
        raise ConnectionError("offline")

    monkeypatch.setattr("weather_server.external.task.fetch_external", boom)

    task = asyncio.create_task(external_fetch_loop(cfg, db, store))
    # Give the loop a moment to run its first (failing) iteration.
    for _ in range(50):
        await asyncio.sleep(0.01)
        if calls["n"] >= 1:
            break
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert calls["n"] >= 1  # it tried
    assert store.get() is None  # failure left the store empty, no crash


async def test_loop_keeps_empty_when_fetch_returns_none(tmp_path, monkeypatch) -> None:
    db = init_db(tmp_path / "w.db")
    cfg = _config({"enabled": True, "refresh_interval_seconds": 600})
    store = ExternalStore()

    calls = {"n": 0}

    def none_fetch(*args, **kwargs):
        calls["n"] += 1
        return None  # provider up, but no data (e.g. station calm/missing)

    monkeypatch.setattr("weather_server.external.task.fetch_external", none_fetch)

    task = asyncio.create_task(external_fetch_loop(cfg, db, store))
    for _ in range(50):
        await asyncio.sleep(0.01)
        if calls["n"] >= 1:
            break
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert calls["n"] >= 1
    assert store.get() is None  # nothing stored, loop kept running
