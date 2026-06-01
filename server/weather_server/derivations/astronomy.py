"""Sun and moon derivations + timezone resolution (D-TIME and D-LOCATION).

Algorithms are a Python port of SunCalc (Vladimir Agafonkin, BSD-2-Clause).
Approximate but well within the precision needed for a weather dashboard.

Timezone resolution uses `timezonefinder` which ships an offline IANA
database — no network required.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from functools import lru_cache
from typing import Any
from zoneinfo import ZoneInfo

from timezonefinder import TimezoneFinder

# ── trig and constants ──────────────────────────────────────────────────────

PI = math.pi
RAD = PI / 180.0
DAY_S = 86400.0
J1970 = 2440588
J2000 = 2451545

# Obliquity of the ecliptic.
E = RAD * 23.4397


def _to_julian(d: datetime) -> float:
    if d.tzinfo is None:
        d = d.replace(tzinfo=UTC)
    delta = d - datetime(1970, 1, 1, tzinfo=UTC)
    return delta.total_seconds() / DAY_S - 0.5 + J1970


def _from_julian(j: float) -> datetime:
    seconds = (j + 0.5 - J1970) * DAY_S
    return datetime(1970, 1, 1, tzinfo=UTC) + timedelta(seconds=seconds)


def _to_days(d: datetime) -> float:
    return _to_julian(d) - J2000


def _right_ascension(lon: float, b: float) -> float:
    return math.atan2(math.sin(lon) * math.cos(E) - math.tan(b) * math.sin(E), math.cos(lon))


def _declination(lon: float, b: float) -> float:
    return math.asin(math.sin(b) * math.cos(E) + math.cos(b) * math.sin(E) * math.sin(lon))


def _azimuth(h: float, phi: float, dec: float) -> float:
    return math.atan2(math.sin(h), math.cos(h) * math.sin(phi) - math.tan(dec) * math.cos(phi))


def _altitude(h: float, phi: float, dec: float) -> float:
    return math.asin(math.sin(phi) * math.sin(dec) + math.cos(phi) * math.cos(dec) * math.cos(h))


def _sidereal_time(d: float, lw: float) -> float:
    return RAD * (280.16 + 360.9856235 * d) - lw


def _solar_mean_anomaly(d: float) -> float:
    return RAD * (357.5291 + 0.98560028 * d)


def _ecliptic_longitude(m: float) -> float:
    c = RAD * (1.9148 * math.sin(m) + 0.02 * math.sin(2 * m) + 0.0003 * math.sin(3 * m))
    p = RAD * 102.9372
    return m + c + p + PI


def _sun_coords(d: float) -> tuple[float, float]:
    m = _solar_mean_anomaly(d)
    ec_lon = _ecliptic_longitude(m)
    return _declination(ec_lon, 0), _right_ascension(ec_lon, 0)


def _moon_coords(d: float) -> tuple[float, float, float]:
    l_ = RAD * (218.316 + 13.176396 * d)
    m = RAD * (134.963 + 13.064993 * d)
    f = RAD * (93.272 + 13.22935 * d)
    lng = l_ + RAD * 6.289 * math.sin(m)
    lat = RAD * 5.128 * math.sin(f)
    dist = 385001 - 20905 * math.cos(m)
    return _right_ascension(lng, lat), _declination(lng, lat), dist


# ── public sun functions ────────────────────────────────────────────────────


@dataclass(frozen=True)
class SunPosition:
    azimuth_deg: float
    altitude_deg: float


def sun_position(d: datetime, lat: float, lon: float) -> SunPosition:
    lw = RAD * -lon
    phi = RAD * lat
    days = _to_days(d)
    dec, ra = _sun_coords(days)
    h = _sidereal_time(days, lw) - ra
    return SunPosition(
        azimuth_deg=math.degrees(_azimuth(h, phi, dec)) + 180.0,  # north = 0°
        altitude_deg=math.degrees(_altitude(h, phi, dec)),
    )


# (angle in degrees, rise name, set name)
_SUN_EVENTS: list[tuple[float, str, str]] = [
    (-0.833, "sunrise", "sunset"),
    (-6, "dawn", "dusk"),
]

_J0 = 0.0009


def _julian_cycle(d: float, lw: float) -> float:
    return round(d - _J0 - lw / (2 * PI))


def _approx_transit(ht: float, lw: float, n: float) -> float:
    return _J0 + (ht + lw) / (2 * PI) + n


def _solar_transit_j(ds: float, m: float, ec_lon: float) -> float:
    return J2000 + ds + 0.0053 * math.sin(m) - 0.0069 * math.sin(2 * ec_lon)


def _hour_angle(h: float, phi: float, dec: float) -> float:
    return math.acos(
        (math.sin(h) - math.sin(phi) * math.sin(dec)) / (math.cos(phi) * math.cos(dec))
    )


def _get_set_j(
    h: float, lw: float, phi: float, dec: float, n: float, m: float, ec_lon: float
) -> float:
    w = _hour_angle(h, phi, dec)
    a = _approx_transit(w, lw, n)
    return _solar_transit_j(a, m, ec_lon)


@dataclass(frozen=True)
class SunTimes:
    sunrise: datetime | None
    sunset: datetime | None
    dawn: datetime | None
    dusk: datetime | None
    solar_noon: datetime


def sun_times(d: datetime, lat: float, lon: float) -> SunTimes:
    """Sun events for the local day containing `d`. Returns None for any
    event that doesn't happen (high-latitude polar day/night)."""
    lw = RAD * -lon
    phi = RAD * lat
    days = _to_days(d)
    n = _julian_cycle(days, lw)
    ds = _approx_transit(0, lw, n)
    m = _solar_mean_anomaly(ds)
    l_ = _ecliptic_longitude(m)
    dec = _declination(l_, 0)
    j_noon = _solar_transit_j(ds, m, l_)

    results: dict[str, datetime | None] = {"solar_noon": _from_julian(j_noon)}
    for angle, rise_name, set_name in _SUN_EVENTS:
        try:
            j_set = _get_set_j(angle * RAD, lw, phi, dec, n, m, l_)
            j_rise = j_noon - (j_set - j_noon)
            results[rise_name] = _from_julian(j_rise)
            results[set_name] = _from_julian(j_set)
        except ValueError:
            results[rise_name] = None
            results[set_name] = None

    return SunTimes(
        sunrise=results.get("sunrise"),
        sunset=results.get("sunset"),
        dawn=results.get("dawn"),
        dusk=results.get("dusk"),
        solar_noon=results["solar_noon"],  # type: ignore[arg-type]
    )


# ── public moon functions ───────────────────────────────────────────────────


@dataclass(frozen=True)
class MoonPosition:
    azimuth_deg: float
    altitude_deg: float
    distance_km: float


def moon_position(d: datetime, lat: float, lon: float) -> MoonPosition:
    lw = RAD * -lon
    phi = RAD * lat
    days = _to_days(d)
    ra, dec, dist = _moon_coords(days)
    h = _sidereal_time(days, lw) - ra
    alt = _altitude(h, phi, dec)
    # Atmospheric refraction near the horizon.
    if alt > 0:
        alt = alt + 0.0002967 / math.tan(alt + 0.00312536 / (alt + 0.08901179))
    return MoonPosition(
        azimuth_deg=math.degrees(_azimuth(h, phi, dec)) + 180.0,
        altitude_deg=math.degrees(alt),
        distance_km=dist,
    )


@dataclass(frozen=True)
class MoonIllumination:
    fraction: float  # 0..1
    phase: float  # 0..1 (0 = new, 0.5 = full)


def moon_illumination(d: datetime) -> MoonIllumination:
    days = _to_days(d)
    s_dec, s_ra = _sun_coords(days)
    m_ra, m_dec, m_dist = _moon_coords(days)
    sdist = 149_598_000.0
    phi = math.acos(
        math.sin(s_dec) * math.sin(m_dec)
        + math.cos(s_dec) * math.cos(m_dec) * math.cos(s_ra - m_ra)
    )
    inc = math.atan2(sdist * math.sin(phi), m_dist - sdist * math.cos(phi))
    angle = math.atan2(
        math.cos(s_dec) * math.sin(s_ra - m_ra),
        math.sin(s_dec) * math.cos(m_dec)
        - math.cos(s_dec) * math.sin(m_dec) * math.cos(s_ra - m_ra),
    )
    fraction = (1 + math.cos(inc)) / 2.0
    sign = -1 if angle < 0 else 1
    phase = 0.5 + (0.5 * inc * sign) / PI
    return MoonIllumination(fraction=fraction, phase=phase)


def moon_phase_name(phase: float) -> str:
    """Eight standard phase names from a SunCalc-style phase value (0..1)."""
    if phase < 0.0625 or phase >= 0.9375:
        return "New Moon"
    if phase < 0.1875:
        return "Waxing Crescent"
    if phase < 0.3125:
        return "First Quarter"
    if phase < 0.4375:
        return "Waxing Gibbous"
    if phase < 0.5625:
        return "Full Moon"
    if phase < 0.6875:
        return "Waning Gibbous"
    if phase < 0.8125:
        return "Last Quarter"
    return "Waning Crescent"


def moon_phase_icon(phase: float) -> str:
    if phase < 0.0625 or phase >= 0.9375:
        return "🌑"
    if phase < 0.1875:
        return "🌒"
    if phase < 0.3125:
        return "🌓"
    if phase < 0.4375:
        return "🌔"
    if phase < 0.5625:
        return "🌕"
    if phase < 0.6875:
        return "🌖"
    if phase < 0.8125:
        return "🌗"
    return "🌘"


def moon_times(d: datetime, lat: float, lon: float) -> dict[str, Any]:
    """Moonrise and moonset for the local day containing `d`.

    Returns {"rise": dt|None, "set": dt|None, "always_up": bool, "always_down": bool}.
    Iterative quadratic search across 24 hours in 2-hour windows.
    """
    t = d.replace(hour=0, minute=0, second=0, microsecond=0)
    if t.tzinfo is None:
        t = t.replace(tzinfo=UTC)
    hc = 0.133 * RAD
    h0 = moon_position(t, lat, lon).altitude_deg * RAD - hc
    rise: float | None = None
    sett: float | None = None
    ye = 0.0

    for i in range(1, 25, 2):
        h1 = moon_position(t + timedelta(hours=i), lat, lon).altitude_deg * RAD - hc
        h2 = moon_position(t + timedelta(hours=i + 1), lat, lon).altitude_deg * RAD - hc
        a = (h0 + h2) / 2.0 - h1
        b = (h2 - h0) / 2.0
        xe = -b / (2 * a) if a != 0 else 0.0
        ye = (a * xe + b) * xe + h1
        d_disc = b * b - 4 * a * h1
        roots = 0
        x1 = x2 = 0.0
        if d_disc >= 0:
            dx = math.sqrt(d_disc) / (abs(a) * 2)
            x1 = xe - dx
            x2 = xe + dx
            if abs(x1) <= 1:
                roots += 1
            if abs(x2) <= 1:
                roots += 1
            if x1 < -1:
                x1 = x2
        if roots == 1:
            if h0 < 0:
                rise = i + x1
            else:
                sett = i + x1
        elif roots == 2:
            rise = i + (x2 if ye < 0 else x1)
            sett = i + (x1 if ye < 0 else x2)
        if rise is not None and sett is not None:
            break
        h0 = h2

    result: dict[str, Any] = {"rise": None, "set": None, "always_up": False, "always_down": False}
    if rise is not None:
        result["rise"] = t + timedelta(hours=rise)
    if sett is not None:
        result["set"] = t + timedelta(hours=sett)
    if rise is None and sett is None:
        if ye > 0:
            result["always_up"] = True
        else:
            result["always_down"] = True
    return result


# ── extra sun events / season ────────────────────────────────────────────────


def sun_event(
    d: datetime, lat: float, lon: float, angle_deg: float
) -> tuple[datetime | None, datetime | None]:
    """Morning and evening times the sun reaches `angle_deg` elevation on the
    local day containing `d`. Negative angles are below the horizon (twilight
    bands); positive angles are above (golden hour). (None, None) if the sun
    never reaches that angle that day."""
    lw = RAD * -lon
    phi = RAD * lat
    days = _to_days(d)
    n = _julian_cycle(days, lw)
    ds = _approx_transit(0, lw, n)
    m = _solar_mean_anomaly(ds)
    l_ = _ecliptic_longitude(m)
    dec = _declination(l_, 0)
    j_noon = _solar_transit_j(ds, m, l_)
    try:
        j_set = _get_set_j(angle_deg * RAD, lw, phi, dec, n, m, l_)
    except ValueError:
        return None, None
    j_rise = j_noon - (j_set - j_noon)
    return _from_julian(j_rise), _from_julian(j_set)


def shadow_multiplier(sun_altitude_deg: float) -> float | None:
    """Length of an object's shadow as a multiple of its height. None when the
    sun is at or below the horizon (shadow is effectively infinite)."""
    if sun_altitude_deg <= 0:
        return None
    return 1.0 / math.tan(math.radians(sun_altitude_deg))


# Approximate UTC dates of the equinoxes/solstices — within a day or two, which
# is plenty for a season label and a countdown.
def _solar_events(year: int) -> list[tuple[datetime, str]]:
    return [
        (datetime(year, 3, 20, tzinfo=UTC), "march_equinox"),
        (datetime(year, 6, 21, tzinfo=UTC), "june_solstice"),
        (datetime(year, 9, 22, tzinfo=UTC), "september_equinox"),
        (datetime(year, 12, 21, tzinfo=UTC), "december_solstice"),
    ]


_SEASON_BY_START = {
    "march_equinox": ("Spring", "Autumn"),
    "june_solstice": ("Summer", "Winter"),
    "september_equinox": ("Autumn", "Spring"),
    "december_solstice": ("Winter", "Summer"),
}


@dataclass(frozen=True)
class SeasonInfo:
    season: str
    next_event: str
    next_event_time: datetime
    seconds_to_next_event: float


def season_info(d: datetime, lat: float) -> SeasonInfo:
    """Current astronomical season (hemisphere-aware) and the next
    equinox/solstice."""
    if d.tzinfo is None:
        d = d.replace(tzinfo=UTC)
    events = sorted(
        _solar_events(d.year - 1) + _solar_events(d.year) + _solar_events(d.year + 1)
    )
    prev_name = events[0][1]
    next_time, next_name = events[-1]
    for t, name in events:
        if t > d:
            next_time, next_name = t, name
            break
        prev_name = name
    north, south = _SEASON_BY_START[prev_name]
    season = north if lat >= 0 else south
    return SeasonInfo(
        season=season,
        next_event=next_name,
        next_event_time=next_time,
        seconds_to_next_event=(next_time - d).total_seconds(),
    )


def _phase_distance(d: datetime, target: float) -> float:
    """Circular distance (0..0.5) between the moon phase and `target`. The
    moon-phase value is discontinuous at new moon, so we minimize this distance
    rather than hunt for a sign change."""
    p = moon_illumination(d).phase
    raw = abs(p - target) % 1.0
    return min(raw, 1.0 - raw)


def next_moon_phase(d: datetime, target: float) -> datetime | None:
    """Next time after `d` the moon reaches `target` phase (0.0 new, 0.5 full).
    Coarse 2-hour scan to bracket the minimum-distance point, then a 1-minute
    refine within the bracket."""
    if d.tzinfo is None:
        d = d.replace(tzinfo=UTC)
    coarse = timedelta(hours=2)
    prev_dist = _phase_distance(d, target)
    for i in range(1, int(35 * 24 / 2)):
        t = d + coarse * i
        dist = _phase_distance(t, target)
        if dist > prev_dist and prev_dist < 0.05:
            return _refine_min(t - coarse * 2, t, target)
        prev_dist = dist
    return None


def _refine_min(lo: datetime, hi: datetime, target: float) -> datetime:
    best, best_dist = lo, _phase_distance(lo, target)
    minutes = int((hi - lo).total_seconds() / 60)
    for k in range(1, minutes + 1):
        t = lo + timedelta(minutes=k)
        dist = _phase_distance(t, target)
        if dist < best_dist:
            best, best_dist = t, dist
    return best


@lru_cache(maxsize=4)
def _moon_phases_at_hour(
    stamp: tuple[int, int, int, int],
) -> tuple[datetime | None, datetime | None]:
    y, mo, da, hr = stamp
    base = datetime(y, mo, da, hr, tzinfo=UTC)
    return next_moon_phase(base, 0.0), next_moon_phase(base, 0.5)


def upcoming_moon_phases(d: datetime) -> tuple[datetime | None, datetime | None]:
    """Next (new, full) moon after `d`. Moon phase is location-independent, so
    this is cached at UTC-hour granularity — the ~1000-iteration scan runs at
    most once per hour rather than twice per request (matters on a Pi Zero)."""
    b = (d if d.tzinfo else d.replace(tzinfo=UTC)).astimezone(UTC)
    return _moon_phases_at_hour((b.year, b.month, b.day, b.hour))


# ── timezone resolution ─────────────────────────────────────────────────────


@lru_cache(maxsize=1)
def _timezone_finder() -> TimezoneFinder:
    return TimezoneFinder()


def resolve_timezone(lat: float | None, lon: float | None) -> str:
    """Return an IANA timezone name, or 'UTC' if lat/lon are unavailable."""
    if lat is None or lon is None:
        return "UTC"
    name = _timezone_finder().timezone_at(lat=lat, lng=lon)
    return name or "UTC"


def to_local(d: datetime, tz_name: str) -> datetime:
    return d.astimezone(ZoneInfo(tz_name))
