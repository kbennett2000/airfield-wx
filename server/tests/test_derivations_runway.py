"""Runway component calculator (pure math, TRUE frame).

Head/cross/tailwind per runway end, favored end, crosswind-limit flag.
Wind is FROM-direction in degrees TRUE, speed in knots.
"""

from __future__ import annotations

import pytest

from weather_server.derivations.airport import RunwayInfo
from weather_server.derivations.runway import runway_solution


def _one_runway(le_ident: str, le_true: float, he_ident: str, he_true: float) -> RunwayInfo:
    return RunwayInfo(
        le_ident=le_ident,
        he_ident=he_ident,
        le_heading_true_deg=le_true,
        he_heading_true_deg=he_true,
        le_heading_mag_deg=None,
        he_heading_mag_deg=None,
        length_ft=5000.0,
        surface="ASP",
    )


def _end(sol, ident):  # type: ignore[no-untyped-def]
    return next(e for e in sol.ends if e.ident == ident)


# ── single-end component cases (V = 10 kt) ─────────────────────────────────


def test_pure_headwind() -> None:
    sol = runway_solution([_one_runway("27", 270.0, "09", 90.0)], 270.0, 10.0, None)
    assert sol is not None
    end = _end(sol, "27")
    assert end.headwind_kt == pytest.approx(10.0)
    assert end.crosswind_kt == pytest.approx(0.0, abs=1e-9)
    assert end.crosswind_side is None
    assert end.tailwind is False


def test_pure_tailwind() -> None:
    sol = runway_solution([_one_runway("27", 270.0, "09", 90.0)], 90.0, 10.0, None)
    assert sol is not None
    end = _end(sol, "27")
    assert end.headwind_kt == pytest.approx(-10.0)
    assert end.crosswind_kt == pytest.approx(0.0, abs=1e-9)
    assert end.tailwind is True


def test_crosswind_from_left() -> None:
    # wind 360 onto heading 090 → pure crosswind from the LEFT.
    sol = runway_solution([_one_runway("09", 90.0, "27", 270.0)], 360.0, 10.0, None)
    assert sol is not None
    end = _end(sol, "09")
    assert end.headwind_kt == pytest.approx(0.0, abs=1e-9)
    assert end.crosswind_kt == pytest.approx(10.0)
    assert end.crosswind_side == "L"


def test_crosswind_from_right() -> None:
    # wind 180 onto heading 090 → pure crosswind from the RIGHT.
    sol = runway_solution([_one_runway("09", 90.0, "27", 270.0)], 180.0, 10.0, None)
    assert sol is not None
    end = _end(sol, "09")
    assert end.headwind_kt == pytest.approx(0.0, abs=1e-9)
    assert end.crosswind_kt == pytest.approx(10.0)
    assert end.crosswind_side == "R"


def test_quartering_headwind() -> None:
    # wind 045 onto heading 090 → equal head/cross, crosswind from the LEFT.
    sol = runway_solution([_one_runway("09", 90.0, "27", 270.0)], 45.0, 10.0, None)
    assert sol is not None
    end = _end(sol, "09")
    assert end.headwind_kt == pytest.approx(7.0711, abs=1e-3)
    assert end.crosswind_kt == pytest.approx(7.0711, abs=1e-3)
    assert end.crosswind_side == "L"


# ── favored end ────────────────────────────────────────────────────────────


def test_favored_end() -> None:
    sol = runway_solution([_one_runway("09", 90.0, "27", 270.0)], 45.0, 10.0, None)
    assert sol is not None
    assert sol.favored == "09"
    # The opposite end takes a tailwind.
    assert _end(sol, "27").tailwind is True
    assert _end(sol, "09").headwind_kt > 0


# ── crosswind limit ────────────────────────────────────────────────────────


def test_crosswind_exceeds_limit() -> None:
    # crosswind ≈7.07 kt.
    sol = runway_solution([_one_runway("09", 90.0, "27", 270.0)], 45.0, 10.0, 5.0)
    assert sol is not None and _end(sol, "09").crosswind_exceeds_limit is True


def test_crosswind_within_limit() -> None:
    sol = runway_solution([_one_runway("09", 90.0, "27", 270.0)], 45.0, 10.0, 10.0)
    assert sol is not None and _end(sol, "09").crosswind_exceeds_limit is False


def test_no_limit_never_exceeds() -> None:
    sol = runway_solution([_one_runway("09", 90.0, "27", 270.0)], 45.0, 10.0, None)
    assert sol is not None and _end(sol, "09").crosswind_exceeds_limit is False


# ── calm + null + edges ─────────────────────────────────────────────────────


def test_calm_has_no_favored() -> None:
    sol = runway_solution([_one_runway("09", 90.0, "27", 270.0)], 45.0, 0.0, 15.0)
    assert sol is not None
    assert sol.favored is None
    assert sol.ends  # ends still listed
    assert all(not e.crosswind_exceeds_limit for e in sol.ends)


def test_none_when_no_wind_direction() -> None:
    assert runway_solution([_one_runway("09", 90.0, "27", 270.0)], None, 10.0, None) is None


def test_none_when_no_wind_speed() -> None:
    assert runway_solution([_one_runway("09", 90.0, "27", 270.0)], 270.0, None, None) is None


def test_none_when_no_usable_runways() -> None:
    # A runway with no true headings yields no computable ends.
    rw = RunwayInfo(
        le_ident="H1",
        he_ident=None,
        le_heading_true_deg=None,
        he_heading_true_deg=None,
        le_heading_mag_deg=None,
        he_heading_mag_deg=None,
        length_ft=None,
        surface=None,
    )
    assert runway_solution([rw], 270.0, 10.0, None) is None


def test_both_ends_present() -> None:
    sol = runway_solution([_one_runway("09", 90.0, "27", 270.0)], 270.0, 10.0, None)
    assert sol is not None
    idents = {e.ident for e in sol.ends}
    assert idents == {"09", "27"}
