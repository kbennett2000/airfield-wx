# Install & Configuration Guide

Run the server, point it at your sensors (or demo fixtures), and understand every `weather.toml`
setting.

> **UNOFFICIAL — NOT FOR FLIGHT PLANNING.** See the [README](../../README.md).

## Requirements

- A Linux host on your LAN — a **Raspberry Pi Zero 2 W** is plenty, as is any Ubuntu/Debian box.
- **Python 3.11+**.
- Outbound internet is **optional** (only the METAR/model `external` block needs it; everything else
  works fully offline).

## Two ways to run

### A. Permanent install (systemd service) — `./install.sh`

```bash
git clone https://github.com/kbennett2000/airfield-wx.git
cd airfield-wx
sudo ./install.sh
```

What it does (idempotent — safe to re-run):

- Installs apt packages (`python3`, `python3-venv`, `sqlite3`, `curl`, `ufw`).
- Creates the venv at `server/.venv` and installs the server.
- **Seeds `server/weather.toml`** from the example if absent (and `branding.toml`).
- Writes a systemd unit `weather-server.service` (runs at boot, restarts on failure, logs to
  journald), reading the port from `weather.toml`.
- Opens the configured TCP port in UFW (plus SSH), denies the rest.

Useful flags: `--port N` (also rewrites `[server].port` in `weather.toml`), `--with-widget` (GTK tray
deps), `--no-systemd`, `--no-firewall`, `--no-start`. Run `./install.sh --help` for the full list.

Manage the service:

```bash
sudo systemctl restart weather-server.service
journalctl -u weather-server.service -f
```

### B. Developer / no-hardware run — `make`

```bash
make install      # venv + editable install
make dev          # auto-seeds server/weather.toml, then uvicorn --reload on :8005
```

`make dev` (and `make serve`) call `make config` first, which copies `weather.toml.example` →
`server/weather.toml` **only if it doesn't already exist**. So a fresh clone boots straight into
**fixture/demo mode** (sample data, no hardware) at <http://localhost:8005/dashboard/>.

## First run: demo data → real sensors

`weather.toml.example` ships with a `[development]` block active:

```toml
[development]
fixture_dir = "fixtures"
```

In this mode the server reads canned readings from `server/fixtures/` instead of polling ESP32s — the
dashboard shows demo data (outdoor ≈ **64.2 °F**) so you can explore before any hardware exists.

**To switch to live polling:**

1. Edit `server/weather.toml`.
2. **Comment out (or delete) the entire `[development]` block.**
3. In each `[[sensors]]` entry, set `ip` to your sensor's LAN address (from flashing — see the
   [hardware guide](hardware.md)).
4. Restart (`sudo systemctl restart weather-server.service`, or Ctrl-C + `make dev`).

You'll know it worked when `/api/v1/current` shows your real outdoor temperature instead of the demo
value.

## The database & its migration

Storage is a single **SQLite** file (WAL mode) under `server/` — `outdoor_readings`, raw readings only.
Every derived value is computed at read time, so the DB never holds a conversion.

The schema is versioned (`PRAGMA user_version`). On open, the server **migrates forward
automatically**: an older database has the new columns added in place (existing rows preserved, new
columns `NULL`) and the version bumped. It **never downgrades** — a database newer than the code
refuses to open rather than risk data loss. You don't run anything; just start the server.

## Bundled offline data

Airport identity/runways and magnetic variation are resolved **offline** from data shipped in the repo:

- **OurAirports** CSVs (`server/weather_server/data/ourairports/`) — airports + runways.
- **WMM2025** magnetic model via the `pygeomag` dependency (no file to vendor).

Refresh the OurAirports CSVs periodically:

```bash
make data-refresh
```

(WMM2025 is valid through 2030; refresh `pygeomag` when a new model ships.)

## `weather.toml` walkthrough

Copy `server/weather.toml.example` → `server/weather.toml` (or let `install.sh`/`make dev` seed it).
The example is heavily commented; the sections:

### `[server]`
`host`, `port` (default 8005), `db_path`, `dashboard_dir`, `branding_path`. Paths are relative to the
directory the server launches from (`server/`).

### `[logger]` / `[cache]`
`interval_seconds` (how often the outdoor sensor is logged), `http_timeout_seconds`, and the
indoor-poll TTL `cache.ttl_seconds`.

### `[development]`
`fixture_dir` — demo mode (above). Remove the block for real polling.

### `[external]` — optional internet feed (offline-first)
Remove the block, or set `enabled = false`, and everything else keeps working; the API's `external`
block is simply `null`. Providers:

- `open-meteo` — keyless model point (wind/cloud/UV/precip/visibility).
- `nws` — US station observations.
- `wunderground` — a specific PWS (`station_id` + `api_key`).
- **`metar`** — keyless aviationweather.gov: ceiling, sky layers, visibility, flight category, and the
  station altimeter (cross-checks your local QNH). **Recommended for the aviation dashboard.** It
  auto-finds the nearest reporting station, or pin one with `station_id = "KXXX"`.

```toml
[external]
enabled = true
provider = "metar"
# station_id = "KSEA"   # optional: pin a reporting station
```

### `[airport]` — location intelligence (all optional)
With nothing set, the nearest airport is resolved from OurAirports using the outdoor GPS.

- `ident` — override the auto-detected field (e.g. a close-together or wrong nearest match).
- `field_elevation_ft` — manual elevation (else published, else GPS).
- `crosswind_limit_kt` — flags a runway end whose crosswind exceeds this.
- `[[airport.runways]]` — **manual runways for a strip not in OurAirports.** Headings are **magnetic**
  (what's painted); the engine derives true via the WMM variation. *Hypothetical:*

  ```toml
  [airport]
  field_elevation_ft = 6900
  crosswind_limit_kt = 15
  [[airport.runways]]
    ident = "09/27"
    le_heading_magnetic_deg = 90
    length_ft = 4000
    surface = "turf"
  ```

### `[[sensors]]`
One block per sensor. The outdoor one carries the aviation fields:

- `ip` — the ESP32's LAN address.
- `has_gps` — enables GPS-driven location/airport resolution.
- `has_wind` — `true` if an anemometer is fitted (a station without one is valid; wind + runway
  solution then read `—`).
- `wind_vane_offset_deg` — the vane north-alignment offset (see the
  [hardware guide](hardware.md#vane-north-alignment-calibration-the-one-field-step)).
- `has_light = false` — there is no light sensor.
- `temp_offset_c` — calibration offset.
- `fallback_altitude_m` / `fallback_lat` / `fallback_lon` — used when GPS has no fix.

## Branding

`branding.toml` (seeded from `branding.toml.example`) fills the dashboard's `[BRANDING]` text slots —
station name, tagline, footer, offline-state copy. All optional placeholders; edit and restart. The
live `branding.toml` is gitignored.

## Widget (optional)

A GTK tray widget (`widget/`) reads the same API. `sudo ./install.sh --with-widget` installs its deps;
run `make widget` (uses system Python for `gi`). Point `widget/config.toml` at the server URL.

---

Next: the **[dashboard tour](dashboard.md)** and the **[verification checklist](verification.md)**.
