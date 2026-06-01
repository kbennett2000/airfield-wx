"""History-summary pure aggregations (D-HISTORY)."""

from __future__ import annotations

import pytest

from weather_server.derivations import summary as sm


def test_stat_basic() -> None:
    s = sm.stat([1.0, 2.0, 3.0, None])
    assert s is not None
    assert (s.min, s.max, s.avg) == (1.0, 3.0, 2.0)


def test_stat_all_none() -> None:
    assert sm.stat([None, None]) is None


def test_linear_trend_per_hour() -> None:
    # +2°C over one hour → +2.0 °C/h.
    assert sm.linear_trend_per_hour([0.0, 3600.0], [10.0, 12.0]) == pytest.approx(2.0)


def test_linear_trend_needs_two_points() -> None:
    assert sm.linear_trend_per_hour([0.0], [10.0]) is None


def test_pressure_tendency_rising() -> None:
    times = [0.0, 3600.0, 7200.0, 10800.0]
    pressures = [1000.0, 1000.5, 1001.0, 1002.0]
    delta, trend = sm.pressure_tendency(times, pressures)
    assert delta == pytest.approx(2.0)
    assert trend == "rising"


def test_pressure_tendency_steady() -> None:
    delta, trend = sm.pressure_tendency([0.0, 3600.0], [1000.0, 1000.2])
    assert trend == "steady"


def test_light_integral_constant() -> None:
    # 54 lux → 1 µmol·m⁻²·s⁻¹ over 3600 s → 3600 µmol = 0.0036 mol.
    dli = sm.light_integral_mol_m2([0.0, 3600.0], [54.0, 54.0])
    assert dli == pytest.approx(0.0036, abs=1e-5)


def test_extraterrestrial_radiation_midsummer_midlat() -> None:
    # ~40°N near summer solstice → Ra ≈ 16–18 mm/day.
    ra = sm.extraterrestrial_radiation_mm(40.0, 172)
    assert 15.0 < ra < 19.0


def test_hargreaves_et0_plausible() -> None:
    ra = sm.extraterrestrial_radiation_mm(40.0, 172)
    et0 = sm.hargreaves_et0_mm(10.0, 25.0, 17.5, ra)
    assert 3.0 < et0 < 8.0


def test_degree_day_contributions() -> None:
    hdd, cdd, gdd = sm.degree_day_contributions(50.0, 70.0)  # mean 60°F
    assert hdd == pytest.approx(5.0)  # 65 - 60
    assert cdd == pytest.approx(0.0)
    assert gdd == pytest.approx(10.0)  # 60 - 50
