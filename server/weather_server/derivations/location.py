"""GPS-bound derivations (D-LOCATION tag).

These derive from latitude/longitude/altitude and don't change between
readings for a fixed station.
"""

from __future__ import annotations

M_TO_FT = 3.280839895


def altitude_m_to_ft(meters: float) -> float:
    return meters * M_TO_FT


def decimal_to_dms(lat: float, lon: float) -> str:
    """Format coordinates as a degrees-minutes-seconds string.

    Example: 39.7392, -104.9903 -> '39°44\\'21.1"N  104°59\\'25.1"W'
    """
    lat_dir = "N" if lat >= 0 else "S"
    lon_dir = "E" if lon >= 0 else "W"
    return f"{_format_one(lat)}{lat_dir}  {_format_one(lon)}{lon_dir}"


def _format_one(value: float) -> str:
    value = abs(value)
    deg = int(value)
    minutes_full = (value - deg) * 60
    minutes = int(minutes_full)
    seconds = (minutes_full - minutes) * 60
    return f'{deg}°{minutes:02d}\'{seconds:04.1f}"'


def maidenhead(lat: float, lon: float, precision: int = 3) -> str:
    """6-character Maidenhead grid square (precision=3 → 6 chars).

    Field (2 chars, A-R, 20° wide × 10° tall)
    Square (2 chars, 0-9, 2° wide × 1° tall)
    Subsquare (2 chars, a-x, 5' wide × 2.5' tall)
    """
    if not -90 <= lat <= 90 or not -180 <= lon <= 180:
        raise ValueError("lat/lon out of range")
    lon_adj = lon + 180.0
    lat_adj = lat + 90.0

    field_lon = int(lon_adj / 20)
    field_lat = int(lat_adj / 10)

    sq_lon = int((lon_adj % 20) / 2)
    sq_lat = int((lat_adj % 10) / 1)

    sub_lon = int(((lon_adj % 2) * 60) / 5)
    sub_lat = int(((lat_adj % 1) * 60) / 2.5)

    out = chr(ord("A") + field_lon) + chr(ord("A") + field_lat)
    if precision >= 2:
        out += str(sq_lon) + str(sq_lat)
    if precision >= 3:
        out += chr(ord("a") + sub_lon) + chr(ord("a") + sub_lat)
    return out
