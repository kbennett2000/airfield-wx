"""TTL cache for sensor poll results.

Per the architecture doc: a 5-second TTL is enough to deduplicate
concurrent requests from multiple clients while keeping data effectively
fresh. The cache also serializes concurrent fetches for the same key
behind a per-key lock, so a thundering herd produces exactly one upstream
poll, not N.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Generic, TypeVar

T = TypeVar("T")


@dataclass
class _Entry(Generic[T]):
    value: T
    expires_at: float


class TTLCache:
    def __init__(self, default_ttl: float = 5.0) -> None:
        self._default_ttl = default_ttl
        self._entries: dict[str, _Entry[object]] = {}
        self._locks: dict[str, asyncio.Lock] = {}
        self._meta_lock = asyncio.Lock()

    async def get_or_fetch(
        self,
        key: str,
        fetcher: Callable[[], Awaitable[T]],
        ttl: float | None = None,
    ) -> T:
        """Return the cached value, or call `fetcher` to populate it.

        Concurrent calls for the same key wait on a per-key lock so the
        upstream is hit exactly once during a cache miss.
        """
        ttl = self._default_ttl if ttl is None else ttl
        now = time.monotonic()

        entry = self._entries.get(key)
        if entry is not None and entry.expires_at > now:
            return entry.value  # type: ignore[return-value]

        lock = await self._get_lock(key)
        async with lock:
            entry = self._entries.get(key)
            now = time.monotonic()
            if entry is not None and entry.expires_at > now:
                return entry.value  # type: ignore[return-value]
            value = await fetcher()
            self._entries[key] = _Entry(value=value, expires_at=now + ttl)
            return value

    def invalidate(self, key: str) -> None:
        self._entries.pop(key, None)

    def clear(self) -> None:
        self._entries.clear()

    async def _get_lock(self, key: str) -> asyncio.Lock:
        async with self._meta_lock:
            lock = self._locks.get(key)
            if lock is None:
                lock = asyncio.Lock()
                self._locks[key] = lock
            return lock
