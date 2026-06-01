"""Optional internet-sourced regional conditions (the EXTERNAL provenance).

Everything in this package is OPTIONAL and degrades to nothing when there's
no internet: a background task fetches a normalized `Observation` on a timer
and stores it; request handlers read the last-known value (or None). A failed
fetch is caught and leaves the previous value in place until it goes stale.

This is the ONLY part of the server that touches the network at read time's
behest, and it never runs in the request path — see external/task.py.
"""

from __future__ import annotations

from .providers import Observation, cardinal_from_deg, fetch_external
from .store import ExternalStore

__all__ = [
    "ExternalStore",
    "Observation",
    "cardinal_from_deg",
    "fetch_external",
]
