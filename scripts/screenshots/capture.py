#!/usr/bin/env python3
"""Regenerate the dashboard screenshots in docs/assets/screenshots/.

This is TOOLING, not a test — it is never imported by the server or the fast
`make check` suite. Run it via `make screenshots` (which installs the
`tooling` extra + a headless Chromium first), or directly:

    server/.venv/bin/python scripts/screenshots/capture.py

What it does, with NO network:

1. Builds a throwaway, fully-populated demo: a wind-bearing outdoor reading
   sited at KSEA (so the airport + runways resolve from the bundled
   OurAirports data) and the real Cycle-5 KSEA METAR fixture injected into the
   external store. Wind is ~aligned with a runway so the favored-runway
   headwind reads cleanly and the crosswind stays under the limit.
2. Runs the actual FastAPI app under uvicorn in a background thread.
3. Drives a headless Chromium (Playwright) at a 1440-wide retina viewport to
   capture: the populated ONLINE dashboard, the OFFLINE state (Net-Feed off →
   the violet METAR panel dims while the cyan local panels stay bright), and
   tight crops of the runway compass and the density-altitude hero.

Refresh these whenever the dashboard UI changes.
"""

from __future__ import annotations

import dataclasses
import json
import os
import tempfile
import threading
import time
import urllib.request
from datetime import UTC, datetime
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SERVER = REPO / "server"
OUT = REPO / "docs" / "assets" / "screenshots"
METAR_FIXTURE = SERVER / "tests" / "fixtures" / "metar_ksea.json"
PORT = 8077  # off the default 8005 so it won't collide with a running server

# KSEA — a real field with multiple runways, so the runway solution is rich.
LAT, LON, ALT = 47.45, -122.31, 132.0

# A wind-bearing outdoor reading. ~340° roughly aligns with KSEA's 16/34
# runways → a clear favored-runway headwind, minimal crosswind (a clean shot).
OUTDOOR = {
    "temperature_c": 16.0,
    "humidity_pct": 55.0,
    "pressure_pa": 100900,
    "latitude": LAT,
    "longitude": LON,
    "altitude_m": ALT,
    "satellites": 9,
    "rssi_dbm": -58,
    "uptime_s": 84320,
    "free_heap_bytes": 178000,
    "wind_speed_ms": 6.0,
    "wind_gust_ms": 9.0,
    "wind_direction_deg": 340.0,
}

# Friendly demo branding so the header reads cleanly (not [BRANDING — …] slots).
BRANDING_TOML = """
[header]
title = "AIRFIELD-WX"
subtitle = "DEMO FIELD"
system_mark = "EFIS"
tagline = "A weather station for your own airstrip"
[footer]
text = "Unofficial — not for flight planning"
system_line = "airfield-wx"
[browser_title]
text = "airfield-wx"
[states]
outdoor_offline = "Outdoor sensor offline"
loading = "Loading…"
[error]
generic = "Can't reach the station."
[taglines]
rotating = []
"""

CONFIG_TOML = """
[server]
host = "127.0.0.1"
port = {port}
db_path = "{db_path}"
branding_path = "{branding_path}"
dashboard_dir = "{dashboard_dir}"

[logger]
interval_seconds = 60
http_timeout_seconds = 10

[cache]
ttl_seconds = 5

[development]
fixture_dir = "{fixture_dir}"

[airport]
ident = "KSEA"
crosswind_limit_kt = 15

[[sensors]]
id = "outdoor"
role = "outdoor"
ip = "192.168.1.60"
has_gps = true
has_wind = true
wind_vane_offset_deg = 0.0
online_threshold_seconds = 120
"""


def _wait_health(base: str, timeout: float = 20.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(f"{base}/api/v1/health", timeout=1) as r:
                if r.status == 200:
                    return
        except Exception:
            pass
        time.sleep(0.1)
    raise RuntimeError("server did not become healthy in time")


def _inject_metar(app: object) -> None:
    """Parse the real KSEA METAR fixture and drop it into the external store,
    stamped 'now' so it reads fresh regardless of the wall clock."""
    from weather_server.external import providers

    payload = json.loads(METAR_FIXTURE.read_text())
    obs = providers.normalize_metar(payload, LAT, LON)
    assert obs is not None, "KSEA METAR fixture failed to normalize"
    obs = dataclasses.replace(obs, observed_at=datetime.now(UTC))
    app.state.external_store.set(obs, datetime.now(UTC))  # type: ignore[attr-defined]


def _launch_system_chrome(pw: object):  # type: ignore[no-untyped-def]
    """Fall back to an installed Chrome/Chromium when Playwright has no bundled
    browser for this OS."""
    # The `chrome` / `chromium` channels use a system install.
    for channel in ("chrome", "chromium"):
        try:
            return pw.chromium.launch(channel=channel)  # type: ignore[attr-defined]
        except Exception:
            continue
    # Last resort: a known executable path.
    for path in ("/usr/bin/google-chrome", "/usr/bin/chromium", "/usr/bin/chromium-browser"):
        if Path(path).exists():
            return pw.chromium.launch(executable_path=path)  # type: ignore[attr-defined]
    raise RuntimeError("no Chromium/Chrome available for Playwright")


# A phone descriptor (iPhone-14-class) defined inline so it's version-proof —
# no dependency on the playwright.devices registry. is_mobile/has_touch are
# Chromium-only context options (work with the bundled build or system Chrome).
IPHONE = {
    "viewport": {"width": 390, "height": 844},
    "device_scale_factor": 3,
    "is_mobile": True,
    "has_touch": True,
    "user_agent": (
        "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) "
        "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1"
    ),
}


def _wait_populated(page: object) -> None:
    """Block until the dashboard shows real data, not a wall of "—"."""
    page.wait_for_function("document.querySelector('#da')?.textContent.trim() !== '—'")
    page.locator("#rwy-fav").wait_for(state="visible")  # favored runway lit
    page.wait_for_function(
        "/cat-(vfr|mvfr|ifr|lifr)/.test(document.querySelector('#flight-cat')?.className||'')"
    )
    page.wait_for_timeout(400)  # let the compass SVG + animations settle


def _shoot_online_offline(page: object, base: str, online: Path, offline: Path) -> None:
    page.goto(f"{base}/dashboard/", wait_until="networkidle")
    _wait_populated(page)
    page.screenshot(path=str(online), full_page=True)
    # OFFLINE: Net-Feed off → the violet METAR panel dims; cyan stays bright.
    page.locator("#sw-feed-btn").click()
    page.locator("#metar-panel.offline").wait_for()
    page.wait_for_timeout(200)
    page.screenshot(path=str(offline), full_page=True)


def _capture(base: str) -> None:
    from playwright.sync_api import sync_playwright

    OUT.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as pw:
        # Prefer Playwright's bundled Chromium; fall back to a system Chrome/
        # Chromium when no bundled build exists for this OS (e.g. very new
        # Ubuntu releases Playwright doesn't ship a binary for).
        try:
            browser = pw.chromium.launch()
        except Exception:
            browser = _launch_system_chrome(pw)

        # ── Desktop (1440-wide retina) ──────────────────────────────────────
        page = browser.new_page(viewport={"width": 1440, "height": 900}, device_scale_factor=2)
        _shoot_online_offline(
            page, base, OUT / "dashboard-online.png", OUT / "dashboard-offline.png"
        )
        # Tight crops for inline use (desktop layout).
        page.goto(f"{base}/dashboard/", wait_until="networkidle")
        _wait_populated(page)
        page.locator("section.panel:has(#compass)").screenshot(
            path=str(OUT / "runway-compass.png")
        )
        page.locator("section.panel.hero").screenshot(path=str(OUT / "density-altitude.png"))
        page.close()

        # ── Mobile (iPhone-class, the primary at-the-hangar context) ────────
        mobile = browser.new_context(**IPHONE)
        mpage = mobile.new_page()
        _shoot_online_offline(
            mpage, base, OUT / "mobile-online.png", OUT / "mobile-offline.png"
        )
        mobile.close()

        browser.close()


def main() -> None:
    import uvicorn

    tmp = Path(tempfile.mkdtemp(prefix="awx-shots-"))
    fixtures = tmp / "fixtures"
    fixtures.mkdir()
    (fixtures / "outdoor.json").write_text(json.dumps([OUTDOOR]))
    (tmp / "branding.toml").write_text(BRANDING_TOML)
    (tmp / "weather.toml").write_text(
        CONFIG_TOML.format(
            port=PORT,
            db_path=str(tmp / "weather.db"),
            branding_path=str(tmp / "branding.toml"),
            dashboard_dir=str(REPO / "dashboard"),
            fixture_dir=str(fixtures),
        )
    )
    os.environ["WEATHER_CONFIG"] = str(tmp / "weather.toml")

    from weather_server.main import create_app

    app = create_app()
    config = uvicorn.Config(app, host="127.0.0.1", port=PORT, log_level="warning")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    try:
        base = f"http://127.0.0.1:{PORT}"
        _wait_health(base)
        _inject_metar(app)
        _capture(base)
    finally:
        server.should_exit = True
        thread.join(timeout=5)

    print(f"wrote screenshots to {OUT}:")
    for p in sorted(OUT.glob("*.png")):
        print(f"  {p.relative_to(REPO)}  ({p.stat().st_size // 1024} KB)")


if __name__ == "__main__":
    main()
