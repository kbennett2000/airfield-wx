import asyncio

import pytest

from weather_server.cache import TTLCache


async def test_caches_value_for_ttl_window() -> None:
    cache = TTLCache(default_ttl=10)
    calls = 0

    async def fetch() -> int:
        nonlocal calls
        calls += 1
        return 42

    v1 = await cache.get_or_fetch("k", fetch)
    v2 = await cache.get_or_fetch("k", fetch)
    assert v1 == 42 and v2 == 42
    assert calls == 1


async def test_expired_entry_triggers_refetch() -> None:
    cache = TTLCache(default_ttl=0.01)
    calls = 0

    async def fetch() -> int:
        nonlocal calls
        calls += 1
        return calls

    assert await cache.get_or_fetch("k", fetch) == 1
    await asyncio.sleep(0.05)
    assert await cache.get_or_fetch("k", fetch) == 2


async def test_concurrent_misses_collapse_to_one_fetch() -> None:
    cache = TTLCache(default_ttl=10)
    started = 0
    barrier = asyncio.Event()

    async def slow_fetch() -> str:
        nonlocal started
        started += 1
        await barrier.wait()
        return "value"

    async def kick():
        return await cache.get_or_fetch("k", slow_fetch)

    waiters = [asyncio.create_task(kick()) for _ in range(5)]
    await asyncio.sleep(0.01)
    barrier.set()
    results = await asyncio.gather(*waiters)
    assert results == ["value"] * 5
    assert started == 1


async def test_invalidate_forces_refetch() -> None:
    cache = TTLCache(default_ttl=10)
    counter = 0

    async def fetch() -> int:
        nonlocal counter
        counter += 1
        return counter

    assert await cache.get_or_fetch("k", fetch) == 1
    cache.invalidate("k")
    assert await cache.get_or_fetch("k", fetch) == 2


async def test_per_key_ttl_override() -> None:
    cache = TTLCache(default_ttl=10)
    calls = 0

    async def fetch() -> int:
        nonlocal calls
        calls += 1
        return calls

    await cache.get_or_fetch("k", fetch, ttl=0.01)
    await asyncio.sleep(0.05)
    await cache.get_or_fetch("k", fetch, ttl=0.01)
    assert calls == 2


async def test_different_keys_are_independent() -> None:
    cache = TTLCache(default_ttl=10)

    async def fa() -> str:
        return "a"

    async def fb() -> str:
        return "b"

    assert await cache.get_or_fetch("a", fa) == "a"
    assert await cache.get_or_fetch("b", fb) == "b"


@pytest.mark.asyncio
async def test_clear_empties_cache() -> None:
    cache = TTLCache(default_ttl=10)
    calls = 0

    async def fetch() -> int:
        nonlocal calls
        calls += 1
        return calls

    await cache.get_or_fetch("k", fetch)
    cache.clear()
    await cache.get_or_fetch("k", fetch)
    assert calls == 2
