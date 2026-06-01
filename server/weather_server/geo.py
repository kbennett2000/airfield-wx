"""Shared geographic helpers.

Small, dependency-free geometry used across the server: the great-circle
distance (nearest-station discovery in external providers, nearest-airport
resolution in the location engine) and the nautical-mile conversion aviation
distances are reported in.
"""

from __future__ import annotations

import math

KM_PER_NM = 1.852  # exact, by definition


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return r * 2 * math.asin(math.sqrt(a))
