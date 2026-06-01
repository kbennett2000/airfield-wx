"""Light-sensor derivations (irradiance, cloud %, UV estimate, sky label)."""

from __future__ import annotations

import pytest

from weather_server.derivations import light as lt


def test_lux_to_irradiance_full_sun() -> None:
    # ~120,000 lux full daylight ≈ 1000 W/m².
    assert lt.lux_to_irradiance_w_m2(120000) == pytest.approx(1000.0, abs=1.0)


def test_clear_sky_illuminance() -> None:
    assert lt.clear_sky_illuminance_lux(90.0) == pytest.approx(128000.0, rel=0.01)
    assert lt.clear_sky_illuminance_lux(0.0) is None
    assert lt.clear_sky_illuminance_lux(-5.0) is None


def test_cloud_cover_bounds() -> None:
    # Measured == clear-sky ⇒ 0% cloud.
    assert lt.cloud_cover_pct(128000, 90.0) == pytest.approx(0.0, abs=0.5)
    # Total darkness under high sun ⇒ 100%.
    assert lt.cloud_cover_pct(0.0, 90.0) == pytest.approx(100.0)
    # Half the expected light ⇒ ~50%.
    assert lt.cloud_cover_pct(64000, 90.0) == pytest.approx(50.0, abs=1.0)


def test_cloud_cover_none_when_sun_low() -> None:
    assert lt.cloud_cover_pct(5000, 2.0) is None


def test_uv_index_estimate() -> None:
    assert lt.uv_index_estimate(90.0, 0.0) == pytest.approx(12.5, abs=0.1)
    assert lt.uv_index_estimate(90.0, None) == pytest.approx(12.5, abs=0.1)
    # Full overcast halves the clear-sky value (per the simple model).
    assert lt.uv_index_estimate(90.0, 100.0) == pytest.approx(6.25, abs=0.1)
    assert lt.uv_index_estimate(-5.0) == 0.0


@pytest.mark.parametrize(
    ("alt", "cloud", "expected"),
    [
        (-10.0, None, "night"),
        (-3.0, None, "twilight"),
        (3.0, None, "low sun"),
        (30.0, 5.0, "clear"),
        (30.0, 25.0, "mostly clear"),
        (30.0, 50.0, "partly cloudy"),
        (30.0, 80.0, "mostly cloudy"),
        (30.0, 95.0, "overcast"),
    ],
)
def test_sky_condition(alt: float, cloud: float | None, expected: str) -> None:
    assert lt.sky_condition(alt, cloud) == expected
