"""Fused indices that need BOTH local sensors and external wind (EXTERNAL).

These live in the external block, not `derived`, because every one of them is
null the moment wind data is gone — which keeps "offline ⇒ derived unchanged"
provable. Wind chill and apparent temperature are standard; Beaufort is a
lookup; ET₀ is FAO-56; THSW carries a documented, conservative solar term and
is explicitly an estimate.
"""

from __future__ import annotations

import math

from .readings import c_to_f, f_to_c

_MS_TO_MPH = 2.236936


def wind_chill_c(temp_c: float, wind_speed_ms: float) -> float:
    """NWS wind chill (°C). Defined only for T ≤ 50°F and wind > 3 mph; outside
    that envelope it returns the air temperature unchanged."""
    temp_f = c_to_f(temp_c)
    wind_mph = wind_speed_ms * _MS_TO_MPH
    if temp_f > 50.0 or wind_mph < 3.0:
        return temp_c
    v16 = wind_mph**0.16
    wc_f = 35.74 + 0.6215 * temp_f - 35.75 * v16 + 0.4275 * temp_f * v16
    return f_to_c(wc_f)


def apparent_temperature_c(temp_c: float, humidity_pct: float, wind_speed_ms: float) -> float:
    """Australian BOM apparent temperature (°C) — a full-range "feels like"
    that folds in humidity and wind across both hot and cold conditions."""
    e = (humidity_pct / 100.0) * 6.105 * math.exp(17.27 * temp_c / (237.7 + temp_c))
    return temp_c + 0.33 * e - 0.70 * wind_speed_ms - 4.00


_BEAUFORT: list[tuple[float, int, str]] = [
    (0.5, 0, "Calm"),
    (1.6, 1, "Light air"),
    (3.4, 2, "Light breeze"),
    (5.5, 3, "Gentle breeze"),
    (8.0, 4, "Moderate breeze"),
    (10.8, 5, "Fresh breeze"),
    (13.9, 6, "Strong breeze"),
    (17.2, 7, "Near gale"),
    (20.8, 8, "Gale"),
    (24.5, 9, "Strong gale"),
    (28.5, 10, "Storm"),
    (32.7, 11, "Violent storm"),
]


def beaufort(wind_speed_ms: float) -> tuple[int, str]:
    """Beaufort force number + description for a wind speed in m/s."""
    for threshold, force, name in _BEAUFORT:
        if wind_speed_ms < threshold:
            return force, name
    return 12, "Hurricane force"


def thsw_index_c(
    temp_c: float, humidity_pct: float, wind_speed_ms: float, solar_w_m2: float
) -> float:
    """Temperature–Humidity–Sun–Wind index (°C). ESTIMATE: the apparent
    temperature plus a conservative solar-load term (~+8°C at full sun). The
    only "feels like" that accounts for direct sunlight."""
    at = apparent_temperature_c(temp_c, humidity_pct, wind_speed_ms)
    solar_load = (max(0.0, solar_w_m2) / 1000.0) * 8.0
    return at + solar_load


def et0_hourly_mm(
    temp_c: float,
    humidity_pct: float,
    wind_speed_ms_10m: float,
    solar_w_m2: float,
    pressure_pa: float,
) -> float | None:
    """Reference evapotranspiration rate (mm/hour), FAO-56 hourly
    Penman-Monteith. Net radiation is approximated from measured shortwave
    (longwave neglected), so it's a solid estimate for irrigation guidance."""
    if humidity_pct < 0 or humidity_pct > 100:
        return None
    t = temp_c
    p_kpa = pressure_pa / 1000.0
    es = 0.6108 * math.exp(17.27 * t / (t + 237.3))
    ea = es * humidity_pct / 100.0
    delta = 4098.0 * es / (t + 237.3) ** 2
    gamma = 0.000665 * p_kpa
    u2 = wind_speed_ms_10m * 0.748  # 10 m → 2 m (FAO-56 log profile)
    rs_mj_hr = max(0.0, solar_w_m2) * 0.0036  # W/m² → MJ/m²/hr
    rn = 0.77 * rs_mj_hr  # net shortwave (albedo 0.23), longwave neglected
    if solar_w_m2 > 0:
        g, cd = 0.1 * rn, 0.34  # daytime
    else:
        g, cd = 0.0, 0.96  # nighttime
    cn = 37.0
    num = 0.408 * delta * (rn - g) + gamma * (cn / (t + 273.0)) * u2 * (es - ea)
    den = delta + gamma * (1.0 + cd * u2)
    return max(0.0, num / den)
