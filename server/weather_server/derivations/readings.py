"""Reading-bound derivations (the D-READING and CALIBRATED tags).

These functions are pure: given the same raw reading and calibration, they
always return the same numbers. That's the property that makes
server-side derivation the right call — bug fixes apply retroactively to
all history without backfill.
"""

from __future__ import annotations

import math
from typing import Any

from .._payload_keys import (
    K_FULL_SPECTRUM,
    K_HUMIDITY,
    K_IR,
    K_LUX,
    K_PRESSURE_PA,
    K_TEMP_C,
    K_VISIBLE,
)

HPA_PER_PA = 0.01
INHG_PER_HPA = 0.02953  # standard conversion factor

# Hypsometric (barometric) formula constants.
# P_sealevel = P_station * exp(g * h / (R_specific * T_kelvin))
G_M_S2 = 9.80665
R_SPECIFIC_DRY_AIR = 287.05  # J / (kg * K)
KELVIN_OFFSET = 273.15

# Magnus formula constants for dewpoint.
MAGNUS_B = 17.625
MAGNUS_C = 243.04


def c_to_f(celsius: float) -> float:
    return celsius * 9.0 / 5.0 + 32.0


def apply_calibration(raw_c: float, offset_c: float) -> float:
    return raw_c + offset_c


def dewpoint_c(temp_c: float, humidity_pct: float) -> float | None:
    """Magnus formula. Returns None for non-physical inputs."""
    if humidity_pct <= 0 or humidity_pct > 100:
        return None
    gamma = math.log(humidity_pct / 100.0) + (MAGNUS_B * temp_c) / (MAGNUS_C + temp_c)
    return (MAGNUS_C * gamma) / (MAGNUS_B - gamma)


def absolute_humidity_g_m3(temp_c: float, humidity_pct: float) -> float | None:
    """Absolute humidity (g/m^3) using the Clausius–Clapeyron approximation
    paired with the ideal gas law. Returns None for non-physical inputs."""
    if humidity_pct <= 0 or humidity_pct > 100:
        return None
    return (
        6.112
        * math.exp((17.67 * temp_c) / (temp_c + 243.5))
        * humidity_pct
        * 2.1674
    ) / (KELVIN_OFFSET + temp_c)


def heat_index_f(temp_f: float, humidity_pct: float) -> float:
    """NWS apparent-temperature ("feels like") on the warm side.

    Uses the simple-formula gate from NWS guidance: only escalate to the
    full Rothfusz regression once the simple value averaged with the air
    temperature reaches 80°F. Below that gate, or for non-physical
    humidity, returns the air temperature unchanged.

    Wind chill (cold side) is not included in this `derived.feels_like`
    value — there's no local anemometer. When the optional internet feed is
    enabled it IS available as `external.wind_chill_c/f` (see derivations/fused.py).
    """
    if humidity_pct <= 0 or humidity_pct > 100:
        return temp_f
    hi_simple = 0.5 * (
        temp_f + 61.0 + (temp_f - 68.0) * 1.2 + humidity_pct * 0.094
    )
    if (hi_simple + temp_f) / 2.0 < 80.0:
        return temp_f
    t = temp_f
    r = humidity_pct
    hi = (
        -42.379
        + 2.04901523 * t
        + 10.14333127 * r
        - 0.22475541 * t * r
        - 0.00683783 * t * t
        - 0.05481717 * r * r
        + 0.00122874 * t * t * r
        + 0.00085282 * t * r * r
        - 0.00000199 * t * t * r * r
    )
    if r < 13.0 and 80.0 <= t <= 112.0:
        hi -= ((13.0 - r) / 4.0) * math.sqrt((17.0 - abs(t - 95.0)) / 17.0)
    elif r > 85.0 and 80.0 <= t <= 87.0:
        hi += ((r - 85.0) / 10.0) * ((87.0 - t) / 5.0)
    return hi


def f_to_c(fahrenheit: float) -> float:
    return (fahrenheit - 32.0) * 5.0 / 9.0


def pressure_pa_to_hpa(pa: float) -> float:
    return pa * HPA_PER_PA


def pressure_hpa_to_inhg(hpa: float) -> float:
    return hpa * INHG_PER_HPA


def pressure_station_to_sealevel_hpa(
    station_hpa: float,
    altitude_m: float,
    temp_c: float,
) -> float:
    """Hypsometric formula. Requires station temperature in Celsius and
    altitude in meters. Result is the equivalent sea-level pressure."""
    t_kelvin = temp_c + KELVIN_OFFSET
    return station_hpa * math.exp((G_M_S2 * altitude_m) / (R_SPECIFIC_DRY_AIR * t_kelvin))


def altimeter_setting_hpa(station_hpa: float, altitude_m: float) -> float | None:
    """ASOS/NWS altimeter setting (QNH): station pressure reduced to field
    elevation under the ISA standard atmosphere — the value a pilot dials into
    the Kollsman window.

    Temperature-INDEPENDENT, and therefore distinct from
    pressure_station_to_sealevel_hpa() (true sea-level pressure, which is
    temperature-dependent). Both are kept and disambiguated by field name.

    Pure; returns None for non-physical station pressure rather than raising.
    """
    base = station_hpa - ALTIMETER_OFFSET_HPA
    if base <= 0:
        return None
    k = (
        STANDARD_PRESSURE_HPA**ALTIMETER_EXPONENT
        * ISA_LAPSE_RATE_K_M
        / ISA_SEALEVEL_TEMP_K
    )
    return float(
        base * (1.0 + k * (altitude_m / base**ALTIMETER_EXPONENT)) ** (1.0 / ALTIMETER_EXPONENT)
    )


# ── extended thermodynamics (D-READING) ──────────────────────────────────────

FT_PER_M = 3.280839895
RV_WATER_VAPOR = 461.495  # J/(kg·K)
STANDARD_AIR_DENSITY = 1.225  # kg/m³ at 15°C, sea level
STANDARD_PRESSURE_HPA = 1013.25

# ISA constants for the altimeter-setting reduction (decision 3 / QNH).
ALTIMETER_EXPONENT = 0.190284  # same exponent as pressure_altitude_m (Rd·Γ/g)
ISA_LAPSE_RATE_K_M = 0.0065  # ISA temperature lapse rate (K/m)
ISA_SEALEVEL_TEMP_K = 288.15  # ISA sea-level standard temperature (15°C)
ALTIMETER_OFFSET_HPA = 0.3  # standard NWS/ASOS offset term


def saturation_vapor_pressure_hpa(temp_c: float) -> float:
    """Saturation vapor pressure over water (hPa), Magnus form — same
    constants as the dewpoint derivation for internal consistency."""
    return 6.112 * math.exp((MAGNUS_B * temp_c) / (MAGNUS_C + temp_c))


def vapor_pressure_hpa(temp_c: float, humidity_pct: float) -> float | None:
    """Actual vapor pressure (hPa) from temperature and relative humidity."""
    if humidity_pct < 0 or humidity_pct > 100:
        return None
    return saturation_vapor_pressure_hpa(temp_c) * humidity_pct / 100.0


def vapor_pressure_deficit_kpa(temp_c: float, humidity_pct: float) -> float | None:
    """VPD (kPa): how far the air is from saturation. Drives plant stress."""
    ea = vapor_pressure_hpa(temp_c, humidity_pct)
    if ea is None:
        return None
    es = saturation_vapor_pressure_hpa(temp_c)
    return (es - ea) / 10.0  # hPa → kPa


def wet_bulb_c(temp_c: float, humidity_pct: float) -> float | None:
    """Wet-bulb temperature (°C) via the Stull (2011) empirical fit. Valid
    for ~5–99% RH at sea level; accurate to a few tenths of a degree."""
    if humidity_pct <= 0 or humidity_pct > 100:
        return None
    rh = humidity_pct
    return float(
        temp_c * math.atan(0.151977 * math.sqrt(rh + 8.313659))
        + math.atan(temp_c + rh)
        - math.atan(rh - 1.676331)
        + 0.00391838 * rh**1.5 * math.atan(0.023101 * rh)
        - 4.686035
    )


def humidex_c(temp_c: float, dewpoint_c_value: float) -> float:
    """Canadian humidex (°C). Returns the air temperature unchanged when the
    humidex would fall below it (cool/dry conditions)."""
    dew_k = dewpoint_c_value + KELVIN_OFFSET
    e = 6.11 * math.exp(5417.7530 * (1.0 / 273.16 - 1.0 / dew_k))
    h = temp_c + 0.5555 * (e - 10.0)
    return h if h > temp_c else temp_c


def frost_point_c(temp_c: float, humidity_pct: float) -> float | None:
    """Frost point (°C): the over-ice analogue of dewpoint. Inverts the
    Magnus-over-ice saturation curve against the actual vapor pressure."""
    e = vapor_pressure_hpa(temp_c, humidity_pct)
    if e is None or e <= 0:
        return None
    gamma = math.log(e / 6.1115)
    return (272.55 * gamma) / (22.452 - gamma)


def mixing_ratio_g_kg(temp_c: float, humidity_pct: float, pressure_pa: float) -> float | None:
    """Mass of water vapor per kg of dry air (g/kg)."""
    e = vapor_pressure_hpa(temp_c, humidity_pct)
    if e is None:
        return None
    p_hpa = pressure_pa * HPA_PER_PA
    if p_hpa - e <= 0:
        return None
    return 0.62197 * e / (p_hpa - e) * 1000.0


def specific_humidity_g_kg(temp_c: float, humidity_pct: float, pressure_pa: float) -> float | None:
    """Mass of water vapor per kg of moist air (g/kg)."""
    w = mixing_ratio_g_kg(temp_c, humidity_pct, pressure_pa)
    if w is None:
        return None
    w_kg = w / 1000.0
    return w_kg / (1.0 + w_kg) * 1000.0


def air_density_kg_m3(temp_c: float, humidity_pct: float, pressure_pa: float) -> float | None:
    """Density of the moist air (kg/m³) — dry + vapor partial pressures."""
    e = vapor_pressure_hpa(temp_c, humidity_pct)
    if e is None:
        return None
    t_k = temp_c + KELVIN_OFFSET
    pv = e * 100.0  # hPa → Pa
    pd = pressure_pa - pv
    return pd / (R_SPECIFIC_DRY_AIR * t_k) + pv / (RV_WATER_VAPOR * t_k)


def pressure_altitude_m(station_pressure_pa: float) -> float:
    """Altitude in the ISA where this station pressure occurs (meters)."""
    ratio = (station_pressure_pa * HPA_PER_PA) / STANDARD_PRESSURE_HPA
    return float((1.0 - ratio**0.190284) * 44307.694)


def density_altitude_m(density_kg_m3: float) -> float:
    """Altitude in the ISA with this air density (meters)."""
    return float((1.0 - (density_kg_m3 / STANDARD_AIR_DENSITY) ** 0.234969) * 44329.0)


def cloud_base_m(temp_c: float, dewpoint_c_value: float) -> float | None:
    """Estimated convective cloud base / lifting condensation level (meters)
    from the temperature–dewpoint spread (Espy's ~125 m per °C)."""
    spread = temp_c - dewpoint_c_value
    if spread < 0:
        return 0.0
    return 125.0 * spread


def _extended_thermo(
    temp_c: float | None,
    humidity_pct: float | None,
    pressure_pa: float | None,
    dewpoint_c_value: float | None,
) -> dict[str, Any]:
    """Every additional D-READING field. Each cascades to None on missing
    inputs; nothing raises."""
    out: dict[str, Any] = {}
    if temp_c is None or humidity_pct is None:
        return out

    es = saturation_vapor_pressure_hpa(temp_c)
    ea = vapor_pressure_hpa(temp_c, humidity_pct)
    out["saturation_vapor_pressure_hpa"] = es
    out["vapor_pressure_hpa"] = ea
    out["vapor_pressure_deficit_kpa"] = vapor_pressure_deficit_kpa(temp_c, humidity_pct)

    wb = wet_bulb_c(temp_c, humidity_pct)
    out["wet_bulb_c"] = wb
    out["wet_bulb_f"] = c_to_f(wb) if wb is not None else None

    fp = frost_point_c(temp_c, humidity_pct)
    out["frost_point_c"] = fp
    out["frost_point_f"] = c_to_f(fp) if fp is not None else None

    if dewpoint_c_value is not None:
        hx = humidex_c(temp_c, dewpoint_c_value)
        out["humidex_c"] = hx
        out["humidex_f"] = c_to_f(hx)
        cb = cloud_base_m(temp_c, dewpoint_c_value)
        out["cloud_base_m"] = cb
        out["cloud_base_ft"] = cb * FT_PER_M if cb is not None else None

    if pressure_pa is not None:
        out["mixing_ratio_g_kg"] = mixing_ratio_g_kg(temp_c, humidity_pct, pressure_pa)
        out["specific_humidity_g_kg"] = specific_humidity_g_kg(temp_c, humidity_pct, pressure_pa)
        density = air_density_kg_m3(temp_c, humidity_pct, pressure_pa)
        out["air_density_kg_m3"] = density
        pa_m = pressure_altitude_m(pressure_pa)
        out["pressure_altitude_m"] = pa_m
        out["pressure_altitude_ft"] = pa_m * FT_PER_M
        if density is not None:
            da_m = density_altitude_m(density)
            out["density_altitude_m"] = da_m
            out["density_altitude_ft"] = da_m * FT_PER_M
    return out


def derive_reading(
    payload: dict[str, Any],
    *,
    temp_offset_c: float = 0.0,
    fallback_altitude_m: float | None = None,
) -> dict[str, Any]:
    """Compute every D-READING / CALIBRATED field from a SensorPayload.

    Missing inputs cascade to None outputs; nothing raises. Sea-level
    pressure prefers the live GPS altitude in the payload, then falls
    back to the configured altitude, then to None.
    """
    out: dict[str, Any] = {}

    raw_temp_c = payload.get(K_TEMP_C)
    raw_humidity = payload.get(K_HUMIDITY)
    raw_pressure_pa = payload.get(K_PRESSURE_PA)

    cal_temp_c: float | None = None
    if raw_temp_c is not None:
        cal_temp_c = apply_calibration(raw_temp_c, temp_offset_c)
        out["temperature_c"] = cal_temp_c
        out["temperature_f"] = c_to_f(cal_temp_c)

    dp_c: float | None = None
    if cal_temp_c is not None and raw_humidity is not None:
        dp_c = dewpoint_c(cal_temp_c, raw_humidity)
        out["dewpoint_c"] = dp_c
        out["dewpoint_f"] = c_to_f(dp_c) if dp_c is not None else None
        out["absolute_humidity_g_m3"] = absolute_humidity_g_m3(cal_temp_c, raw_humidity)
        feels_f = heat_index_f(c_to_f(cal_temp_c), raw_humidity)
        out["feels_like_f"] = feels_f
        out["feels_like_c"] = f_to_c(feels_f)

    out.update(_extended_thermo(cal_temp_c, raw_humidity, raw_pressure_pa, dp_c))

    if raw_pressure_pa is not None:
        station_hpa = pressure_pa_to_hpa(raw_pressure_pa)
        out["pressure_station_hpa"] = station_hpa
        out["pressure_station_inhg"] = pressure_hpa_to_inhg(station_hpa)

        altitude_m = payload.get("altitude_m")
        if altitude_m is None:
            altitude_m = fallback_altitude_m
        if altitude_m is not None and cal_temp_c is not None:
            sealevel_hpa = pressure_station_to_sealevel_hpa(
                station_hpa, altitude_m, cal_temp_c
            )
            out["pressure_sealevel_hpa"] = sealevel_hpa
            out["pressure_sealevel_inhg"] = pressure_hpa_to_inhg(sealevel_hpa)
        else:
            out["pressure_sealevel_hpa"] = None
            out["pressure_sealevel_inhg"] = None

        # Altimeter setting (QNH) needs only station pressure + elevation; it is
        # temperature-independent, so it is computed even when temp is missing.
        if altitude_m is not None:
            alt_hpa = altimeter_setting_hpa(station_hpa, altitude_m)
            out["altimeter_setting_hpa"] = alt_hpa
            out["altimeter_setting_inhg"] = (
                pressure_hpa_to_inhg(alt_hpa) if alt_hpa is not None else None
            )
        else:
            out["altimeter_setting_hpa"] = None
            out["altimeter_setting_inhg"] = None

    return out


def map_raw(payload: dict[str, Any]) -> dict[str, Any]:
    """Map SensorPayload field names to the API's `raw.*` block.

    The DB column is full_spectrum (avoiding the SQL reserved-word risk);
    the API field is `full`.
    """
    out: dict[str, Any] = {}
    if K_TEMP_C in payload:
        out["temperature_c"] = payload[K_TEMP_C]
    if K_HUMIDITY in payload:
        out["humidity_pct"] = payload[K_HUMIDITY]
    if K_PRESSURE_PA in payload:
        out["pressure_pa"] = payload[K_PRESSURE_PA]
    if K_LUX in payload:
        out["lux"] = payload[K_LUX]
    if K_IR in payload:
        out["ir"] = payload[K_IR]
    if K_VISIBLE in payload:
        out["visible"] = payload[K_VISIBLE]
    if K_FULL_SPECTRUM in payload:
        out["full"] = payload[K_FULL_SPECTRUM]
    return out
