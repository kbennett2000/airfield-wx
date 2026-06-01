#!/usr/bin/env python3
"""Generate the frozen, SYNTHETIC API snapshots that drive the public demo.

This is TOOLING — never imported by the server or `make check`. It runs the
REAL app (so the JSON shape is faithful) against a FICTIONAL field with
SYNTHETIC weather, captures the three endpoints the dashboard fetches, and
freezes them to demo/snapshots/. No network is ever touched:

- The field is a MANUAL airport (`ident="DEMO"`, no OurAirports lookup, so no
  real airport identity is ever presented — its block is `source="manual"`).
- The METAR is a hand-built Observation injected directly into the external
  store — `fetch_external` / aviationweather.gov is never called.

Three scenarios are frozen: a clear `vfr` day (lit runway solution + green
VFR), a marginal `mvfr` case, and an `offline` case (no external → the METAR
panel dims while the local panels stay live). Run via `make demo-snapshots`.
"""

from __future__ import annotations

import json
import math
import os
import tempfile
import threading
import time
import urllib.request
from datetime import UTC, datetime, timedelta
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SERVER = REPO / "server"
SNAPSHOTS = REPO / "demo" / "snapshots"
PORT = 8078  # off the default 8005 and the screenshot script's 8077

# Neutral round coordinates — open high country, no airport there. They feed
# magnetic variation only; identity stays the fictional "DEMO" field.
LAT, LON = 39.0000, -105.0000
FIELD_ELEV_FT = 6500
ALT_M = round(FIELD_ELEV_FT / 3.28084, 1)  # ~1981 m, consistent with the field elevation

SCENARIOS = ("vfr", "mvfr", "offline")

BRANDING_TOML = """
[header]
title = "AIRFIELD-WX"
subtitle = "DEMO — Example Field"
system_mark = "DEMO"
tagline = "Live demo — simulated data for a fictional field"
[footer]
text = "Demo — simulated data. Not a real station. Not for flight planning."
system_line = "airfield-wx demo"
[browser_title]
text = "airfield-wx — demo"
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
interval_seconds = 120
http_timeout_seconds = 10

[cache]
ttl_seconds = 5

[development]
fixture_dir = "{fixture_dir}"

[airport]
ident = "DEMO"
field_elevation_ft = 6500
crosswind_limit_kt = 15

[[airport.runways]]
ident = "16/34"
le_heading_magnetic_deg = 160
length_ft = 4200
surface = "turf"

[[sensors]]
id = "outdoor"
role = "outdoor"
ip = "192.168.1.60"
has_gps = true
has_wind = true
wind_vane_offset_deg = 0.0
online_threshold_seconds = 600
"""


def _outdoor_rows() -> list[dict]:
    """A gentle 24-point history so the trend chart has a curve; the LAST row
    (≈now) carries the wind that lights the runway solution."""
    rows = []
    for i in range(24):
        # Mild diurnal wobble in temperature and pressure.
        phase = (i / 24.0) * 2 * math.pi
        rows.append(
            {
                "temperature_c": round(13.0 + 3.0 * math.sin(phase), 2),
                "humidity_pct": 48.0,
                "pressure_pa": round(80050.0 + 60.0 * math.cos(phase), 1),
                "latitude": LAT,
                "longitude": LON,
                "altitude_m": ALT_M,
                "satellites": 9,
                "rssi_dbm": -57,
                "uptime_s": 84320,
                "free_heap_bytes": 178000,
                "wind_speed_ms": 6.0,
                "wind_gust_ms": 8.7,
                "wind_direction_deg": 340.0,
            }
        )
    return rows


def _make_obs(scenario: str, now: datetime):  # type: ignore[no-untyped-def]
    from weather_server.external.providers import MetarSkyLayer, Observation

    common = dict(
        provider="metar",
        source="metar:DEMO (simulated)",
        station_id="DEMO",
        distance_km=2.4,
        observed_at=now - timedelta(seconds=180),  # fresh: ≈3 min old, never stale
        temp_c=14.0,
        dewpoint_c=3.0,
    )
    if scenario == "vfr":
        return Observation(
            **common,
            flight_category="VFR",
            visibility_sm=10.0,
            visibility_m=16090.0,
            sky_layers=(MetarSkyLayer(cover="FEW", base_ft_agl=12000),),
            ceiling_ft_agl=None,
            altimeter_inhg=30.12,
            altimeter_hpa=1020.0,
            metar_raw="DEMO 011853Z 34008KT 10SM FEW120 14/03 A3012 RMK SIMULATED DATA",
        )
    if scenario == "mvfr":
        return Observation(
            **common,
            flight_category="MVFR",
            visibility_sm=4.0,
            visibility_m=6437.0,
            sky_layers=(
                MetarSkyLayer(cover="SCT", base_ft_agl=1200),
                MetarSkyLayer(cover="BKN", base_ft_agl=1800),
            ),
            ceiling_ft_agl=1800,
            altimeter_inhg=29.95,
            altimeter_hpa=1014.0,
            metar_raw="DEMO 011853Z 34010KT 4SM BR BKN018 12/08 A2995 RMK SIMULATED DATA",
        )
    raise ValueError(scenario)


def _get(base: str, path: str) -> dict:
    with urllib.request.urlopen(f"{base}{path}", timeout=5) as r:
        return json.loads(r.read())


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


# Real airport idents must never appear as the demo's live identity.
_KNOWN_REAL = {"KSEA", "KBJC", "KDEN", "KJFK", "KLAX", "KORD", "EGLL"}


def _safety_check(current: dict) -> None:
    airport = current.get("airport") or {}
    assert airport.get("source") == "manual", f"airport source must be manual, got {airport.get('source')!r}"
    assert airport.get("ident") == "DEMO", f"airport ident must be DEMO, got {airport.get('ident')!r}"
    blob = json.dumps(current)
    assert "aviationweather" not in blob, "snapshot must not reference aviationweather.gov"
    for ident in _KNOWN_REAL:
        assert ident not in blob, f"snapshot leaked a real airport ident: {ident}"


def main() -> None:
    import uvicorn

    from weather_server.external.store import ExternalStore

    tmp = Path(tempfile.mkdtemp(prefix="awx-demo-"))
    fixtures = tmp / "fixtures"
    fixtures.mkdir()
    (fixtures / "outdoor.json").write_text(json.dumps(_outdoor_rows()))
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

        # Branding + history are shared across scenarios.
        SNAPSHOTS.mkdir(parents=True, exist_ok=True)
        (SNAPSHOTS / "branding.json").write_text(json.dumps(_get(base, "/api/v1/branding"), indent=2))
        (SNAPSHOTS / "history.json").write_text(
            json.dumps(_get(base, "/api/v1/history/outdoor?hours=24&include=weather"), indent=2)
        )

        for scenario in SCENARIOS:
            now = datetime.now(UTC)
            # Swap the external store per scenario (offline ⇒ empty ⇒ external null).
            store = ExternalStore()
            if scenario != "offline":
                store.set(_make_obs(scenario, now), now)
            app.state.external_store = store

            current = _get(base, "/api/v1/current")
            _safety_check(current)
            out = SNAPSHOTS / scenario
            out.mkdir(parents=True, exist_ok=True)
            (out / "current.json").write_text(json.dumps(current, indent=2))
    finally:
        server.should_exit = True
        thread.join(timeout=5)

    print(f"wrote snapshots to {SNAPSHOTS}:")
    for p in sorted(SNAPSHOTS.rglob("*.json")):
        print(f"  {p.relative_to(REPO)}")


if __name__ == "__main__":
    main()
