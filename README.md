# airfield-wx

A self-hosted, GPS-aware weather station any pilot can build and run at their own airstrip. ESP32
sensors feed a FastAPI server on your home LAN, which serves a cockpit-style (EFIS) dashboard and a
read-only HTTP API.

> ## ⚠ UNOFFICIAL — NOT FOR FLIGHT PLANNING
> airfield-wx is backyard sensor data plus nearby public reports. It is **not** a substitute for an
> official preflight briefing or current METAR/TAF, and it performs **no go/no-go automation**. You
> are the pilot in command. Observations only — no forecasting.

## What it is / who it's for

A general-purpose aviation variant of a DIY home weather station. Clone it, flash your own ESP32
sensors, run the server on a Raspberry Pi (or any Ubuntu/Debian box) on your LAN, and get a
cockpit-style readout for **your** field:

- **Density altitude** (the hero metric), pressure altitude, and **altimeter setting (QNH)**.
- **Local wind** from your own anemometer → a **runway solution**: favored end, head/crosswind
  components, magnetic runway headings.
- **Temp / dewpoint / spread / humidity** from a local sensor.
- The **nearest airport + runways + magnetic variation**, auto-resolved from GPS, fully offline.
- Optional **METAR** panel (ceiling, visibility, flight category, station altimeter cross-check) when
  the internet is available — always attributed with station, distance, and age.

**Nothing about any specific airfield is hardcoded.** Location, runways, magnetic variation, timezone,
and the nearest reporting station are all resolved from the outdoor unit's GPS at runtime.

## Hardware (overview)

Outdoor suite = **BME280** (temp/humidity/pressure) + **GPS** + **anemometer**. No light sensor.

- **Anemometer — primary:** Davis 6410 (continuous-pot direction, rugged).
- **Anemometer — budget:** SparkFun Weather Meter Kit (reed-switch + resistor-network, 8-point).

The vane's north alignment is a one-time per-install **config calibration** (`wind_vane_offset_deg`),
not a code constant. Full wiring, pinout, flashing, and the calibration procedure are in the
**[hardware guide](docs/guide/hardware.md)**.

**Anemometer can mount three ways** (ADR-0006): all-in-one (default), a remote anemometer cabled to the
same ESP32, or — where wind and thermo can't co-locate — an opt-in **separate wind station** (a second
ESP32 + anemometer running `wind_station.ino`, no GPS, logged). A `[wind] source` config knob selects
it; a freshness guard nulls stale wind (and the runway solution) so a dead sensor never reads as current.

## Quick start (demo data, no hardware)

```bash
git clone https://github.com/kbennett2000/airfield-wx.git
cd airfield-wx
make install        # creates server/.venv and installs the server
make dev            # auto-seeds server/weather.toml (fixture/demo mode) and runs on :8005
```

Open **http://localhost:8005/dashboard/**. The dashboard renders with sample data (outdoor ≈ 64.2°F)
so you can explore it before any sensor exists. To go live, edit `server/weather.toml` — comment out
the `[development]` block and set your sensor IPs. See the **[install &
configuration guide](docs/guide/install.md)**.

(For a permanent install with a systemd service + firewall: `sudo ./install.sh` — see the install
guide.)

## Architecture (in brief)

- **One source of truth:** a read-only, versioned HTTP API under `/api/v1/`. Every client (dashboard,
  tray widget) polls the same endpoints — no per-client math.
- **The DB stores RAW sensor readings only;** every conversion / derived value (density altitude,
  altimeter setting, vane-corrected wind, runway components) is computed at **read time**. Bug fixes
  apply retroactively to all history — no backfill.
- **Offline-first:** the server starts, serves, and logs with no internet. The only thing that differs
  online vs offline is the optional `external` (METAR/model) block.
- **Provenance is visible on the dashboard:** **cyan** = your LOCAL sensors (always live); **violet** =
  internet-sourced (may be absent, and *dims* when the feed is gone, while the cyan panels stay
  bright). See the **[dashboard tour](docs/guide/dashboard.md)**.

## Documentation

- **[Hardware & build](docs/guide/hardware.md)** — sensors, ESP32 pinout, wiring, flashing, vane
  calibration, siting.
- **[Install & configuration](docs/guide/install.md)** — server install, the systemd service, the
  `weather.toml` walkthrough, the demo path, the DB migration, bundled data.
- **[Dashboard tour](docs/guide/dashboard.md)** — how to read the instrument.
- **[Verification checklist](docs/guide/verification.md)** — "did my deployment work?", end to end.
- **[Design docs](docs/design/)** and **[architecture decisions](docs/adr/)** — the *why* behind the
  build (offline-first, local-first wind, the unofficial stance).

## Licensing & attribution

- **airfield-wx is MIT-licensed.** It is built on a copy of `weather-station-public` (MIT, same
  author) — an aviation variant, not a fork.
- **Airport / runway data:** [OurAirports](https://ourairports.com/data/) — public domain.
- **Magnetic variation:** the WMM2025 model via [`pygeomag`](https://pypi.org/project/pygeomag/) (MIT);
  the WMM coefficients are public domain (NOAA/NCEI + BGS).
- **METAR:** [aviationweather.gov](https://aviationweather.gov/) (FAA/NWS), keyless.

Self-hosted, LAN-only, no accounts, no cloud, no ads.
