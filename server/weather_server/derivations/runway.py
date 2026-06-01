"""Runway component calculator (pure math, TRUE frame).

Given the resolved runways and the current LOCAL wind (FROM-direction in
degrees TRUE, speed in knots), compute head/cross/tailwind per runway end, the
favored end, and a crosswind-limit flag.

Local wind only — never the external/model feed (decision 6 / ADR-0003/0005):
a favored runway from a distant model point would contradict the local-first,
unofficial stance. Until the anemometer lands (Cycle 6) the caller passes a
None wind and this returns None.

Pure and schema-free: returns plain dataclasses (the composer maps them to the
pydantic Airport model and rounds for display). Returns unrounded floats.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass

from .airport import RunwayInfo

# Below this wind speed there's no meaningful favored runway.
CALM_KT = 1.0


@dataclass(frozen=True)
class RunwayEndSolution:
    ident: str | None
    headwind_kt: float
    crosswind_kt: float  # magnitude (>= 0)
    crosswind_side: str | None  # "L" | "R" | None (when ~0)
    tailwind: bool
    crosswind_exceeds_limit: bool


@dataclass(frozen=True)
class RunwaySolution:
    favored: str | None
    ends: tuple[RunwayEndSolution, ...]


def _norm_180(deg: float) -> float:
    """Normalize to (-180, 180]: -180 maps to +180."""
    a = (deg + 180.0) % 360.0 - 180.0
    return 180.0 if a == -180.0 else a


def _solve_end(
    ident: str | None,
    true_heading: float,
    wind_from_true_deg: float,
    wind_speed_kt: float,
    crosswind_limit_kt: float | None,
) -> RunwayEndSolution:
    alpha = _norm_180(wind_from_true_deg - true_heading)
    rad = math.radians(alpha)
    headwind = wind_speed_kt * math.cos(rad)
    crosswind_signed = wind_speed_kt * math.sin(rad)
    crosswind = abs(crosswind_signed)
    if crosswind < 1e-9:
        side: str | None = None
    else:
        side = "R" if alpha > 0 else "L"
    exceeds = crosswind_limit_kt is not None and crosswind > crosswind_limit_kt
    return RunwayEndSolution(
        ident=ident,
        headwind_kt=headwind,
        crosswind_kt=crosswind,
        crosswind_side=side,
        tailwind=headwind < 0,
        crosswind_exceeds_limit=exceeds,
    )


def runway_solution(
    runways: Sequence[RunwayInfo],
    wind_from_true_deg: float | None,
    wind_speed_kt: float | None,
    crosswind_limit_kt: float | None,
) -> RunwaySolution | None:
    """Per-end head/cross/tailwind + favored end. None when there is no current
    wind (direction or speed missing) or no runway end has a true heading."""
    if wind_from_true_deg is None or wind_speed_kt is None:
        return None

    ends: list[RunwayEndSolution] = []
    for rw in runways:
        for ident, heading in (
            (rw.le_ident, rw.le_heading_true_deg),
            (rw.he_ident, rw.he_heading_true_deg),
        ):
            if heading is None:
                continue
            ends.append(
                _solve_end(
                    ident, heading, wind_from_true_deg, wind_speed_kt, crosswind_limit_kt
                )
            )

    if not ends:
        return None

    favored: str | None = None
    if wind_speed_kt >= CALM_KT:
        favored = max(ends, key=lambda e: e.headwind_kt).ident

    return RunwaySolution(favored=favored, ends=tuple(ends))
