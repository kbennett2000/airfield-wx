"""Read-time wind resolver (Cycle 10a / ADR-0006).

Source selection (outdoor payload vs a dedicated wind station), vane-offset →
true direction, observation age, and the single freshness guard. Pure — fed
synthetic rows, no DB or app.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from weather_server.config import load_config_from_dict
from weather_server.derivations.wind import resolve_wind

WHEN = datetime(2026, 6, 1, 12, 0, 0, tzinfo=UTC)
NOW_TS = int(WHEN.timestamp())


def _config(source: str = "outdoor", max_age_s: float = 90.0, *, station: bool = False):  # type: ignore[no-untyped-def]
    sensors = [
        {
            "id": "outdoor",
            "role": "outdoor",
            "ip": "1.2.3.4",
            "has_gps": True,
            "has_wind": not station,  # topology 3 moves the anemometer off outdoor
            "wind_vane_offset_deg": 20.0,
        }
    ]
    if station:
        sensors.append(
            {
                "id": "windy",
                "role": "wind_station",
                "ip": "1.2.3.5",
                "has_wind": True,
                "wind_vane_offset_deg": 5.0,
            }
        )
    return load_config_from_dict(
        {"sensors": sensors, "wind": {"source": source, "max_age_s": max_age_s}}
    )


def _row(age_s: float, *, speed=5.2, gust=8.1, direction=350.0):  # type: ignore[no-untyped-def]
    return {
        "timestamp": NOW_TS - age_s,
        "wind_speed_ms": speed,
        "wind_gust_ms": gust,
        "wind_direction_deg": direction,
    }


# ── source selection ────────────────────────────────────────────────────────


def test_outdoor_source_reads_outdoor_row() -> None:
    config = _config(source="outdoor")
    rw = resolve_wind(config, WHEN, outdoor_row=_row(5), wind_row=None)
    assert rw.source_id == "outdoor"
    assert rw.speed_ms == pytest.approx(5.2)
    assert rw.gust_ms == pytest.approx(8.1)
    # vane offset 20 applied: (350 + 20) % 360 = 10.
    assert rw.direction_true_deg == pytest.approx(10.0)
    assert rw.stale is False


def test_station_source_reads_wind_row_not_outdoor() -> None:
    config = _config(source="windy", station=True)
    # Outdoor row carries different wind; the resolver must ignore it.
    outdoor = _row(5, speed=99.0, direction=180.0)
    wind = _row(5, speed=3.3, gust=4.4, direction=100.0)
    rw = resolve_wind(config, WHEN, outdoor_row=outdoor, wind_row=wind)
    assert rw.source_id == "windy"
    assert rw.speed_ms == pytest.approx(3.3)
    # the wind station's own vane offset (5) is applied: (100 + 5) % 360 = 105.
    assert rw.direction_true_deg == pytest.approx(105.0)


def test_station_source_with_no_wind_row_is_null() -> None:
    config = _config(source="windy", station=True)
    rw = resolve_wind(config, WHEN, outdoor_row=_row(5), wind_row=None)
    assert rw.source_id == "windy"
    assert rw.speed_ms is None
    assert rw.direction_true_deg is None
    assert rw.stale is False  # no data, not a staleness event


def test_outdoor_source_with_no_row_is_null() -> None:
    config = _config(source="outdoor")
    rw = resolve_wind(config, WHEN, outdoor_row=None, wind_row=None)
    assert rw.speed_ms is None
    assert rw.direction_true_deg is None
    assert rw.stale is False


def test_outdoor_without_anemometer_yields_no_wind() -> None:
    # has_wind=false on the source device ⇒ no field wind even if the row
    # happens to carry wind columns.
    config = _config(source="outdoor", station=True)  # station=True makes outdoor has_wind false
    rw = resolve_wind(config, WHEN, outdoor_row=_row(5), wind_row=None)
    assert rw.speed_ms is None
    assert rw.direction_true_deg is None
    assert rw.stale is False


# ── vane offset → true direction ─────────────────────────────────────────────


def test_vane_offset_wraps_past_360() -> None:
    config = _config(source="outdoor")  # offset 20
    rw = resolve_wind(config, WHEN, outdoor_row=_row(5, direction=350.0), wind_row=None)
    assert rw.direction_true_deg == pytest.approx(10.0)


def test_absent_raw_direction_yields_none_true() -> None:
    config = _config(source="outdoor")
    row = {"timestamp": NOW_TS - 5, "wind_speed_ms": 5.0}  # speed but no direction
    rw = resolve_wind(config, WHEN, outdoor_row=row, wind_row=None)
    assert rw.speed_ms == pytest.approx(5.0)
    assert rw.direction_true_deg is None


# ── freshness guard ──────────────────────────────────────────────────────────


def test_fresh_just_under_threshold() -> None:
    config = _config(max_age_s=90.0)
    rw = resolve_wind(config, WHEN, outdoor_row=_row(89), wind_row=None)
    assert rw.stale is False
    assert rw.direction_true_deg is not None
    assert rw.age_s == pytest.approx(89.0)


def test_boundary_exactly_at_threshold_is_fresh() -> None:
    config = _config(max_age_s=90.0)
    rw = resolve_wind(config, WHEN, outdoor_row=_row(90), wind_row=None)
    assert rw.stale is False
    assert rw.speed_ms == pytest.approx(5.2)


def test_stale_past_threshold_nulls_wind() -> None:
    config = _config(max_age_s=90.0)
    rw = resolve_wind(config, WHEN, outdoor_row=_row(91), wind_row=None)
    assert rw.stale is True
    assert rw.speed_ms is None
    assert rw.gust_ms is None
    assert rw.direction_true_deg is None
    # The age is still reported even though the values were nulled.
    assert rw.age_s == pytest.approx(91.0)


def test_freshness_applies_to_station_source_too() -> None:
    config = _config(source="windy", station=True, max_age_s=90.0)
    stale = resolve_wind(config, WHEN, outdoor_row=None, wind_row=_row(120))
    assert stale.stale is True
    assert stale.direction_true_deg is None
    fresh = resolve_wind(config, WHEN, outdoor_row=None, wind_row=_row(10))
    assert fresh.stale is False
    assert fresh.direction_true_deg is not None
