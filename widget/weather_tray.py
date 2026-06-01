#!/usr/bin/env python3
"""
airfield-wx Weather Tray
========================

Linux system-tray widget that displays current outdoor weather. All data
comes from the weather server's HTTP API; no sensor polling, no astronomy
math, no timezone lookup happens here.

Config: TOML file at $WEATHER_TRAY_CONFIG, then ~/.config/weather-tray/
config.toml, then ./config.toml next to this script. See config.toml.example.

Runtime deps: GTK 3 + AppIndicator3 (system packages), and `requests`.
"""

from __future__ import annotations

import os
import sys
import threading
import tomllib
import webbrowser
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import gi
import requests

gi.require_version("Gtk", "3.0")
gi.require_version("AppIndicator3", "0.1")
from gi.repository import AppIndicator3, GLib, Gtk  # noqa: E402

# ─────────────────────────────────────────────────────────────────
# Config
# ─────────────────────────────────────────────────────────────────

DEFAULTS = {
    "server_url": "http://localhost:8005",
    "refresh_seconds": 30,
}


def _config_search_paths() -> list[Path]:
    """Where the widget looks for its TOML config, in priority order."""
    paths: list[Path] = []
    if env := os.environ.get("WEATHER_TRAY_CONFIG"):
        paths.append(Path(env).expanduser())
    paths.append(Path.home() / ".config" / "weather-tray" / "config.toml")
    paths.append(Path(__file__).resolve().parent / "config.toml")
    return paths


def load_config() -> dict[str, Any]:
    """Merge the first config file found onto the defaults. Missing file is
    not fatal — useful for first-run when the user just wants to see the
    icon appear before editing the file."""
    cfg = dict(DEFAULTS)
    for path in _config_search_paths():
        if path.is_file():
            with path.open("rb") as fh:
                data = tomllib.load(fh)
            cfg.update({k: v for k, v in data.items() if k in DEFAULTS})
            print(f"[weather-tray] loaded config from {path}", file=sys.stderr)
            return cfg
    print(
        "[weather-tray] no config file found, using defaults "
        f"(server_url={cfg['server_url']})",
        file=sys.stderr,
    )
    return cfg


# ─────────────────────────────────────────────────────────────────
# Formatting helpers — all stdlib, no astral / timezonefinder / pytz
# ─────────────────────────────────────────────────────────────────


def fmt_seconds_dhms(seconds: float | int | None) -> str:
    """Format a duration in seconds as 'Xd HHhrs MMmin SSsec'. Matches the
    legacy widget's display format. Returns '—' on bad input."""
    if seconds is None:
        return "—"
    try:
        total = int(seconds)
    except (TypeError, ValueError):
        return "—"
    days, rem = divmod(total, 86_400)
    hours, rem = divmod(rem, 3600)
    minutes, secs = divmod(rem, 60)
    return f"{days}d {hours:02d}hrs {minutes:02d}min {secs:02d}sec"


def fmt_hhmm(iso_str: str | None) -> str:
    """Format an ISO 8601 timestamp as HH:MM using its embedded offset.
    The server emits sun/moon events in the resolved station timezone
    (post-Phase-4.5), so a bare strftime on the parsed datetime gives the
    station's wall clock without any additional conversion."""
    if not iso_str:
        return "—"
    try:
        return datetime.fromisoformat(iso_str).strftime("%H:%M")
    except ValueError:
        return "—"


def tz_abbrev(iana_name: str | None, at: datetime | None = None) -> str:
    """Return the timezone abbreviation (e.g. 'MDT') at the given moment.
    Falls back to the offset string if the IANA database doesn't ship the
    short name."""
    if not iana_name:
        return ""
    try:
        zi = ZoneInfo(iana_name)
    except ZoneInfoNotFoundError:
        return ""
    moment = at or datetime.now(tz=timezone.utc)
    abbrev = moment.astimezone(zi).strftime("%Z")
    return abbrev


def parse_iso(iso_str: str | None) -> datetime | None:
    """ISO 8601 → datetime, or None on bad input."""
    if not iso_str:
        return None
    try:
        return datetime.fromisoformat(iso_str)
    except ValueError:
        return None


# ─────────────────────────────────────────────────────────────────
# Popup formatter — turns a /current response into the menu string
# ─────────────────────────────────────────────────────────────────

HEADER_MARKUP = (
    '<span size="large" weight="bold">🌦️ Weather Station ⛱️</span>'
)


def format_offline(reason: str = "DEVICE OFFLINE") -> str:
    return f"{HEADER_MARKUP}\n\n❌ ❌ ❌ {reason} ❌ ❌ ❌"


def _sun_timing_lines(sun_block: dict[str, Any], local_now: datetime | None) -> list[str]:
    """Return the two lines covering 'Day length' / 'Time to (or since) sunset'
    / 'Time to sunrise'. Mirrors the legacy widget's branching: before
    sunset shows time-to-sunset only; after sunset shows time-since-sunset
    plus time-to-sunrise. The 'since' value is computed locally from the
    sunset timestamp + local_time, since the API returns null for
    seconds_to_sunset past sunset."""
    lines: list[str] = []
    day_length_s = sun_block.get("day_length_seconds")
    if day_length_s is not None:
        lines.append(f"📅 Day length: {day_length_s / 3600:.1f} hrs")

    seconds_to_sunset = sun_block.get("seconds_to_sunset")
    seconds_to_sunrise = sun_block.get("seconds_to_sunrise")

    if seconds_to_sunset is not None:
        lines.append(f"⏳ Time to sunset: {seconds_to_sunset / 3600:.1f} hrs")
    else:
        sunset_dt = parse_iso(sun_block.get("sunset"))
        if sunset_dt and local_now:
            since_h = (local_now - sunset_dt).total_seconds() / 3600
            lines.append(f"⏳ Time since sunset: {abs(since_h):.1f} hrs")
        if seconds_to_sunrise is not None:
            lines.append(f"🌄 Time to sunrise: {seconds_to_sunrise / 3600:.1f} hrs")

    return lines


def _moon_event_strs(moon_block: dict[str, Any]) -> tuple[str, str]:
    """Format moonrise/moonset, honoring the always_up / always_down flags."""
    if moon_block.get("always_up"):
        return "↑ Always up", "↑ Always up"
    if moon_block.get("always_down"):
        return "↓ Never rises", "↓ Never sets"
    return (
        fmt_hhmm(moon_block.get("moonrise")),
        fmt_hhmm(moon_block.get("moonset")),
    )


def format_popup(payload: dict[str, Any]) -> str:
    """Build the full Pango-markup string shown in the popup menu. Pulls
    every value straight from /api/v1/current — no derivations here.

    The outdoor sensor is the only one displayed (matches the legacy
    widget). If outdoor is missing or has no data, the caller should use
    format_offline() instead of calling this."""
    sensors = payload.get("sensors", {}) or {}
    outdoor = sensors.get("outdoor") or {}
    astro = payload.get("astronomy", {}) or {}
    sun_b = astro.get("sun") or {}
    moon_b = astro.get("moon") or {}

    raw = outdoor.get("raw") or {}
    derived = outdoor.get("derived") or {}
    loc = outdoor.get("location") or {}
    dev = outdoor.get("device") or {}

    local_now = parse_iso(astro.get("local_time"))
    tz_name = astro.get("timezone") or ""
    tz_short = tz_abbrev(tz_name, local_now)

    # Numeric extractions with safe defaults for the legacy display format.
    temp_f = derived.get("temperature_f")
    temp_c = derived.get("temperature_c")
    dew_f = derived.get("dewpoint_f")
    dew_c = derived.get("dewpoint_c")
    humidity = raw.get("humidity_pct")
    abs_hum = derived.get("absolute_humidity_g_m3")
    press_hpa = derived.get("pressure_sealevel_hpa")
    press_inhg = derived.get("pressure_sealevel_inhg")  # ← BUG-21 fix
    alt_m = loc.get("altitude_m")
    alt_ft = loc.get("altitude_ft")
    lat = loc.get("lat")
    lon = loc.get("lon")
    dms = loc.get("dms")
    grid = loc.get("maidenhead")
    sats = loc.get("satellites")
    lux = raw.get("lux")
    rssi = dev.get("rssi_dbm")
    uptime_s = dev.get("uptime_s")

    # Sun + moon position numbers.
    sun_az = sun_b.get("azimuth_deg")
    sun_alt = sun_b.get("altitude_deg")
    moon_az = moon_b.get("azimuth_deg")
    moon_alt = moon_b.get("altitude_deg")
    moon_dist = moon_b.get("distance_km")
    moon_icon = moon_b.get("phase_icon") or "🌑"
    moon_phase = moon_b.get("phase_name") or "—"
    moon_illum = moon_b.get("illumination_pct")

    moon_rise_s, moon_set_s = _moon_event_strs(moon_b)
    updated_s = fmt_hhmm(astro.get("local_time"))

    lines = [
        HEADER_MARKUP,
        "",
        f"🌡️ Temp: {temp_f:.1f}°F / {temp_c:.1f}°C"
        if temp_f is not None and temp_c is not None else "🌡️ Temp: —",
    ]
    if dew_c is not None and dew_f is not None:
        lines.append(f"🌧️ Dew Point: {dew_f:.1f}°F / {dew_c:.1f}°C")
    if humidity is not None:
        ah = f" ({abs_hum:.1f} g/m³)" if abs_hum is not None else ""
        lines.append(f"💧 Humidity: {humidity:.1f}%{ah}")
    if press_hpa is not None and press_inhg is not None:
        lines.append(f"🌀 Pressure: {press_hpa:.1f} hPa ({press_inhg:.2f} inHg)")
    if alt_m is not None and alt_ft is not None:
        lines.append(f"🏔️ Altitude: {alt_ft:.1f} ft / {alt_m:.1f} m")
    lines.append("")

    if sun_b:
        sr = fmt_hhmm(sun_b.get("sunrise"))
        dawn = fmt_hhmm(sun_b.get("dawn"))
        noon = fmt_hhmm(sun_b.get("solar_noon"))
        ss = fmt_hhmm(sun_b.get("sunset"))
        dusk = fmt_hhmm(sun_b.get("dusk"))
        lines.append(f"🌅 Sunrise (Dawn): {sr} ({dawn})")
        lines.append(f"☀️ Solar Noon: {noon}")
        lines.append(f"🌇 Sunset (Dusk): {ss} ({dusk})")
        lines.extend(_sun_timing_lines(sun_b, local_now))
        if sun_az is not None and sun_alt is not None:
            lines.append(f"☀️ Sun now: Az {sun_az:.0f}°  Alt {sun_alt:.1f}°")
        if moon_az is not None and moon_alt is not None:
            dist_part = f"  ({moon_dist:.0f} km)" if moon_dist is not None else ""
            lines.append(f"🌕 Moon now: Az {moon_az:.0f}°  Alt {moon_alt:.1f}°{dist_part}")
        lines.append("")

    if moon_b:
        lines.append(f"🌝 Moonrise: {moon_rise_s}")
        lines.append(f"🌚 Moonset: {moon_set_s}")
        if moon_illum is not None:
            lines.append(f"{moon_icon} {moon_phase}: {moon_illum:.1f}%")
        lines.append("")

    if lux is not None:
        lines.append(f"💡 Light: {lux:.2f} lux")
    if rssi is not None:
        lines.append(f"📶 WiFi: {rssi} dBm")
    if lat is not None and lon is not None:
        lines.append(f"🌏 GPS: {lat:.5f}, {lon:.5f}")
    if dms:
        lines.append(f"🌍 GPS DMS: {dms}")
    if grid:
        lines.append(f"📡 Grid: {grid}")
    if sats is not None:
        lines.append(f"📡 Satellites: {sats}")
    if tz_name:
        zone_suffix = f" ({tz_short})" if tz_short else ""
        lines.append(f"🕓 Timezone: {tz_name}{zone_suffix}")
    if uptime_s is not None:
        lines.append(f"🤖 Uptime: {fmt_seconds_dhms(uptime_s)}")
    lines.append(f"✅ Updated: {updated_s}")

    return "\n".join(lines)


def format_tray_label(payload: dict[str, Any]) -> str:
    """Short string shown next to the tray icon. Always temperature, since
    that's what was on the icon in the legacy widget."""
    outdoor = (payload.get("sensors") or {}).get("outdoor") or {}
    derived = outdoor.get("derived") or {}
    tf = derived.get("temperature_f")
    tc = derived.get("temperature_c")
    if tf is None or tc is None:
        return "🌡️ -- °F"
    return f"🌡️ {tf:.1f}°F / {tc:.1f}°C"


# ─────────────────────────────────────────────────────────────────
# GTK widget — fetch, render, click
# ─────────────────────────────────────────────────────────────────


class WeatherTray:
    def __init__(self, config: dict[str, Any]):
        self.server_url = config["server_url"].rstrip("/")
        self.refresh_seconds = int(config["refresh_seconds"])
        self.api_url = f"{self.server_url}/api/v1/current"

        self.indicator = AppIndicator3.Indicator.new(
            "home-weather", "", AppIndicator3.IndicatorCategory.APPLICATION_STATUS
        )
        self.indicator.set_status(AppIndicator3.IndicatorStatus.ACTIVE)
        self.indicator.set_label("🌡️ -- °F", "home-weather-label")

        self.menu = Gtk.Menu()
        self.details_label = Gtk.Label()
        self.details_item = Gtk.MenuItem()
        self.details_item.add(self.details_label)
        self.details_item.connect("activate", self._on_open_dashboard)
        self.menu.append(self.details_item)
        self.menu.append(Gtk.SeparatorMenuItem())
        quit_item = Gtk.MenuItem(label="Quit")
        quit_item.connect("activate", lambda _: Gtk.main_quit())
        self.menu.append(quit_item)
        self.menu.show_all()
        self.indicator.set_menu(self.menu)

        GLib.timeout_add_seconds(self.refresh_seconds, self._tick)
        self._tick()

    def _tick(self) -> bool:
        """Kick off a background fetch. Returning True keeps the GLib
        timeout firing."""
        threading.Thread(target=self._fetch_and_render, daemon=True).start()
        return True

    def _fetch_and_render(self) -> None:
        try:
            r = requests.get(self.api_url, timeout=8)
            r.raise_for_status()
            payload = r.json()
        except Exception as exc:
            GLib.idle_add(self._render_offline, f"DEVICE OFFLINE ({exc.__class__.__name__})")
            return

        outdoor = (payload.get("sensors") or {}).get("outdoor")
        if not outdoor or outdoor.get("derived", {}).get("temperature_c") is None:
            GLib.idle_add(self._render_offline, "OUTDOOR SENSOR OFFLINE")
            return

        GLib.idle_add(self._render, payload)

    def _render(self, payload: dict[str, Any]) -> bool:
        self.indicator.set_label(format_tray_label(payload), "home-weather-label")
        self.details_label.set_markup(format_popup(payload))
        return False

    def _render_offline(self, reason: str) -> bool:
        self.indicator.set_label("🌡️ -- °F", "home-weather-label")
        self.details_label.set_markup(format_offline(reason))
        return False

    def _on_open_dashboard(self, _item) -> None:
        webbrowser.open(self.server_url)


def main() -> None:
    config = load_config()
    WeatherTray(config)
    Gtk.main()


if __name__ == "__main__":
    main()
