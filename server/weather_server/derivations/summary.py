"""History summaries (the D-HISTORY provenance).

Pure aggregations over a window of logged outdoor readings: extremes, observed
pressure tendency (a measurement, NOT a forecast), trends, degree days, daily
light integral, and a temperature-only (Hargreaves) reference ET₀. Wind is not
logged, so the wind-based Penman-Monteith ET₀ lives only in the live external
block; here we use the daily temperature-range method.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

# Degree-day bases (US convention, °F).
HDD_CDD_BASE_F = 65.0
GDD_BASE_F = 50.0
# Rough daylight conversion: lux → PAR photon flux density (µmol·m⁻²·s⁻¹).
_LUX_TO_PPFD = 1.0 / 54.0
_SOLAR_CONSTANT_MJ = 0.0820  # MJ·m⁻²·min⁻¹
_PRESSURE_TENDENCY_THRESHOLD_HPA = 0.5  # over 3 h


@dataclass(frozen=True)
class Stat:
    min: float
    max: float
    avg: float


def stat(values: list[float | None]) -> Stat | None:
    vals = [v for v in values if v is not None]
    if not vals:
        return None
    return Stat(min=min(vals), max=max(vals), avg=sum(vals) / len(vals))


def linear_trend_per_hour(times_s: list[float], values: list[float | None]) -> float | None:
    """Least-squares slope of `values` vs time, expressed per hour."""
    pairs = [(t, v) for t, v in zip(times_s, values, strict=True) if v is not None]
    if len(pairs) < 2:
        return None
    n = len(pairs)
    mean_t = sum(t for t, _ in pairs) / n
    mean_v = sum(v for _, v in pairs) / n
    num = sum((t - mean_t) * (v - mean_v) for t, v in pairs)
    den = sum((t - mean_t) ** 2 for t, _ in pairs)
    if den == 0:
        return None
    return (num / den) * 3600.0  # per second → per hour


def pressure_tendency(
    times_s: list[float], pressures_hpa: list[float | None], window_s: float = 10800.0
) -> tuple[float | None, str | None]:
    """Observed pressure change over the trailing ``window_s`` (default 3 h) and
    a rising/falling/steady label."""
    pairs = [(t, p) for t, p in zip(times_s, pressures_hpa, strict=True) if p is not None]
    if len(pairs) < 2:
        return None, None
    t_last, p_last = pairs[-1]
    cutoff = t_last - window_s
    ref = next(((t, p) for t, p in pairs if t >= cutoff), pairs[0])
    delta = p_last - ref[1]
    if delta > _PRESSURE_TENDENCY_THRESHOLD_HPA:
        trend = "rising"
    elif delta < -_PRESSURE_TENDENCY_THRESHOLD_HPA:
        trend = "falling"
    else:
        trend = "steady"
    return delta, trend


def light_integral_mol_m2(times_s: list[float], lux: list[float | None]) -> float | None:
    """Trapezoidal integral of PAR photon flux over the window (mol·m⁻²).
    Over a single day this is the Daily Light Integral (DLI)."""
    pairs = [(t, lv) for t, lv in zip(times_s, lux, strict=True) if lv is not None]
    if len(pairs) < 2:
        return None
    total_umol = 0.0
    for (t0, l0), (t1, l1) in zip(pairs, pairs[1:], strict=False):
        dt = t1 - t0
        if dt <= 0:
            continue
        ppfd0 = l0 * _LUX_TO_PPFD
        ppfd1 = l1 * _LUX_TO_PPFD
        total_umol += 0.5 * (ppfd0 + ppfd1) * dt  # µmol·m⁻²
    return total_umol / 1_000_000.0  # → mol·m⁻²


def extraterrestrial_radiation_mm(lat_deg: float, day_of_year: int) -> float:
    """FAO-56 extraterrestrial radiation Ra, expressed as mm/day equivalent."""
    phi = math.radians(lat_deg)
    dr = 1.0 + 0.033 * math.cos(2.0 * math.pi / 365.0 * day_of_year)
    decl = 0.409 * math.sin(2.0 * math.pi / 365.0 * day_of_year - 1.39)
    x = -math.tan(phi) * math.tan(decl)
    x = max(-1.0, min(1.0, x))  # clamp for polar day/night
    ws = math.acos(x)
    ra_mj = (
        (24.0 * 60.0 / math.pi)
        * _SOLAR_CONSTANT_MJ
        * dr
        * (ws * math.sin(phi) * math.sin(decl) + math.cos(phi) * math.cos(decl) * math.sin(ws))
    )
    return 0.408 * ra_mj  # MJ·m⁻²·day⁻¹ → mm/day


def hargreaves_et0_mm(tmin_c: float, tmax_c: float, tmean_c: float, ra_mm: float) -> float:
    """Hargreaves reference ET₀ (mm/day) — temperature-only, for when wind is
    unavailable (history has no wind)."""
    spread = max(0.0, tmax_c - tmin_c)
    return max(0.0, 0.0023 * ra_mm * (tmean_c + 17.8) * math.sqrt(spread))


def degree_day_contributions(tmin_f: float, tmax_f: float) -> tuple[float, float, float]:
    """One day's (heating, cooling, growing) degree-day contributions (°F-days)."""
    tmean = (tmin_f + tmax_f) / 2.0
    hdd = max(0.0, HDD_CDD_BASE_F - tmean)
    cdd = max(0.0, tmean - HDD_CDD_BASE_F)
    gdd = max(0.0, tmean - GDD_BASE_F)
    return hdd, cdd, gdd
