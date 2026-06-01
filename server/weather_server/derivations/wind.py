"""Read-time wind resolver — flexible anemometer topology (ADR-0006).

Field wind may ride the outdoor payload (default — all-in-one or remote-cable)
or come from a dedicated, logged wind station. This module is the single
indirection that hides that choice: given the config and the latest candidate
readings, it returns one normalized `ResolvedWind` — speed, gust, and the
vane-corrected TRUE direction, with the wind's observation age.

The wind-consuming derivations (`wind_direction_true_deg`, the runway solution,
the wind-fused comfort indices) read this object and never learn where the wind
came from. Topology is a config choice, resolved here.

A single freshness threshold (`wind.max_age_s`) nulls the wind values when the
latest reading is stale — never present dead-sensor wind as current, and never
compute a favored runway from it. The age and source are still reported so a
caller could surface "wind unavailable". Pure: it takes already-fetched rows
(the DB read stays at the route boundary), so it is trivially testable.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING, Any

from .readings import wind_direction_true_deg

if TYPE_CHECKING:
    from ..config import Config


@dataclass(frozen=True)
class ResolvedWind:
    """The current field wind, origin-agnostic. `direction_true_deg` already
    has the source device's vane offset applied. When `stale` is True the
    speed/gust/direction are None (the freshness guard nulled them); `age_s`
    and `source_id` are still reported."""

    speed_ms: float | None
    gust_ms: float | None
    direction_true_deg: float | None
    age_s: float | None
    source_id: str | None
    stale: bool


def _get(row: Any, key: str) -> Any:
    try:
        return row[key]
    except (KeyError, IndexError):
        return None


def resolve_wind(
    config: Config,
    server_time: datetime,
    *,
    outdoor_row: Any | None,
    wind_row: Any | None,
) -> ResolvedWind:
    """Return the latest field wind from the configured source.

    `outdoor_row` is the latest `outdoor_readings` row; `wind_row` the latest
    `wind_readings` row. Either may be None (no data yet). Rows are anything
    supporting `row[key]` — a sqlite3.Row or a plain dict.
    """
    source = config.wind.source
    if source == "outdoor":
        row, sensor_cfg, source_id = outdoor_row, config.outdoor, "outdoor"
    else:
        row, sensor_cfg, source_id = wind_row, config.sensor_by_id(source), source

    # No anemometer on the source device (or no such sensor) ⇒ no field wind,
    # even if a row happens to carry wind columns. `stale` is reserved for the
    # freshness guard; this is simply "no wind source".
    if sensor_cfg is None or not sensor_cfg.has_wind:
        return ResolvedWind(None, None, None, None, source_id, False)

    if row is None:
        return ResolvedWind(None, None, None, None, source_id, False)

    age_s = server_time.timestamp() - float(row["timestamp"])
    # Boundary: age exactly == max_age_s is still FRESH; older is stale.
    if age_s > config.wind.max_age_s:
        return ResolvedWind(None, None, None, age_s, source_id, True)

    offset = sensor_cfg.wind_vane_offset_deg if sensor_cfg is not None else 0.0
    raw_dir = _get(row, "wind_direction_deg")
    direction_true = (
        wind_direction_true_deg(raw_dir, offset) if raw_dir is not None else None
    )
    return ResolvedWind(
        speed_ms=_get(row, "wind_speed_ms"),
        gust_ms=_get(row, "wind_gust_ms"),
        direction_true_deg=direction_true,
        age_s=age_s,
        source_id=source_id,
        stale=False,
    )
