"""Light-sensor derivations (D-READING, ESTIMATES).

From the outdoor light sensor (lux) plus the sun's altitude we can estimate
solar irradiance, fractional cloud cover, a clear-sky UV index, and a sky
condition label. These are genuinely useful but ARE estimates — the sensor is
not a pyranometer or a UV meter — so the API flags the whole block as such.
"""

from __future__ import annotations

import math

# Luminous efficacy of daylight: ~120 lumens per watt. Rough by nature.
_LUX_PER_W_M2 = 120.0
# Clear-sky horizontal illuminance at the zenith sun (lux), empirical.
_CLEAR_SKY_ZENITH_LUX = 128000.0
# Below this solar elevation the lux-based cloud estimate is unreliable.
_MIN_RELIABLE_ALTITUDE_DEG = 5.0


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def lux_to_irradiance_w_m2(lux: float) -> float:
    """Estimated global horizontal solar irradiance (W/m²) from illuminance."""
    return lux / _LUX_PER_W_M2


def clear_sky_illuminance_lux(sun_altitude_deg: float) -> float | None:
    """Theoretical clear-sky horizontal illuminance for the sun's elevation.
    None when the sun is at or below the horizon."""
    if sun_altitude_deg <= 0:
        return None
    return float(_CLEAR_SKY_ZENITH_LUX * math.sin(math.radians(sun_altitude_deg)) ** 1.15)


def cloud_cover_pct(lux: float, sun_altitude_deg: float) -> float | None:
    """Fractional cloud cover (%) — measured illuminance vs the clear-sky
    expectation. None when the sun is too low for the ratio to be meaningful."""
    if sun_altitude_deg < _MIN_RELIABLE_ALTITUDE_DEG:
        return None
    clear = clear_sky_illuminance_lux(sun_altitude_deg)
    if clear is None or clear <= 0:
        return None
    return _clamp(1.0 - lux / clear, 0.0, 1.0) * 100.0


def uv_index_estimate(sun_altitude_deg: float, cloud_pct: float | None = None) -> float | None:
    """Clear-sky UV index from solar elevation, attenuated by estimated cloud.
    ROUGH — the sensor measures no UV; this is a geometry-based model."""
    if sun_altitude_deg <= 0:
        return 0.0
    clear = 12.5 * math.sin(math.radians(sun_altitude_deg)) ** 1.6
    if cloud_pct is not None:
        clear *= 1.0 - 0.5 * _clamp(cloud_pct / 100.0, 0.0, 1.0)
    return float(clear)


def sky_condition(sun_altitude_deg: float, cloud_pct: float | None) -> str:
    """Human-readable sky/daylight label."""
    if sun_altitude_deg < -6.0:
        return "night"
    if sun_altitude_deg < 0.0:
        return "twilight"
    if sun_altitude_deg < _MIN_RELIABLE_ALTITUDE_DEG or cloud_pct is None:
        return "low sun"
    if cloud_pct < 10.0:
        return "clear"
    if cloud_pct < 40.0:
        return "mostly clear"
    if cloud_pct < 70.0:
        return "partly cloudy"
    if cloud_pct < 90.0:
        return "mostly cloudy"
    return "overcast"
