"""In-memory holder for the latest external observation.

The background fetch task writes here; request handlers read. There is no
TTL eviction — the last good value is kept indefinitely and the consumer
decides staleness from ``age_seconds``. A holder (rather than the request-
driven TTLCache) is the right fit because the producer is a timer, not a
cache miss.
"""

from __future__ import annotations

from datetime import datetime

from .providers import Observation


class ExternalStore:
    def __init__(self) -> None:
        self._observation: Observation | None = None
        self._fetched_at: datetime | None = None

    def set(self, observation: Observation, fetched_at: datetime) -> None:
        self._observation = observation
        self._fetched_at = fetched_at

    def get(self) -> tuple[Observation, datetime] | None:
        if self._observation is None or self._fetched_at is None:
            return None
        return self._observation, self._fetched_at
