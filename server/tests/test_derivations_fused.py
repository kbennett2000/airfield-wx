"""Fused indices: wind chill, apparent temp, Beaufort, THSW, ET0."""

from __future__ import annotations

import pytest

from weather_server.derivations import fused as f


def test_wind_chill_reference() -> None:
    # -5°C, 10 m/s wind → ≈ -13.7°C by the NWS formula.
    assert f.wind_chill_c(-5.0, 10.0) == pytest.approx(-13.7, abs=0.2)


def test_wind_chill_returns_air_temp_above_envelope() -> None:
    assert f.wind_chill_c(20.0, 10.0) == pytest.approx(20.0)  # too warm
    assert f.wind_chill_c(0.0, 0.5) == pytest.approx(0.0)  # too calm


def test_apparent_temperature_warm_and_cold() -> None:
    # Warm/humid/light wind → feels warmer than air.
    assert f.apparent_temperature_c(30.0, 60.0, 2.0) > 30.0
    # Cold/windy → feels much colder than air.
    assert f.apparent_temperature_c(5.0, 50.0, 10.0) < 5.0


@pytest.mark.parametrize(
    ("ms", "force"),
    [(0.2, 0), (1.0, 1), (3.0, 2), (5.0, 3), (7.0, 4), (9.0, 5), (35.0, 12)],
)
def test_beaufort_force(ms: float, force: int) -> None:
    assert f.beaufort(ms)[0] == force


def test_beaufort_descriptions() -> None:
    assert f.beaufort(0.2)[1] == "Calm"
    assert f.beaufort(35.0)[1] == "Hurricane force"


def test_thsw_adds_solar_load() -> None:
    base = f.apparent_temperature_c(25.0, 50.0, 1.0)
    thsw = f.thsw_index_c(25.0, 50.0, 1.0, 800.0)
    assert thsw > base  # sun makes it feel warmer
    assert thsw == pytest.approx(base + 0.8 * 8.0, abs=0.01)


def test_thsw_no_sun_equals_apparent() -> None:
    base = f.apparent_temperature_c(20.0, 50.0, 2.0)
    assert f.thsw_index_c(20.0, 50.0, 2.0, 0.0) == pytest.approx(base)


def test_et0_midday_plausible_rate() -> None:
    et0 = f.et0_hourly_mm(25.0, 50.0, 2.0, 600.0, 101325)
    assert et0 is not None and 0.3 < et0 < 0.7


def test_et0_night_near_zero() -> None:
    et0 = f.et0_hourly_mm(15.0, 70.0, 2.0, 0.0, 101325)
    assert et0 is not None and et0 < 0.1


def test_et0_invalid_humidity_none() -> None:
    assert f.et0_hourly_mm(25.0, 150.0, 2.0, 600.0, 101325) is None
