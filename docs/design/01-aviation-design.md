# Aviation Field-Weather Station — Design

**Status:** design captured (build pending)
**Date:** 2026-06-01
**One-liner:** A self-hosted, GPS-aware weather station any pilot can build and deploy at their own
airstrip, surfacing the pre-flight-relevant conditions a small-aircraft pilot cares about.
**Unofficial — never a substitute for an official preflight briefing.**

## What this is / who it's for

- A general-purpose aviation variant of a DIY home weather station. A pilot clones it, flashes their
  own ESP32 sensors, runs the server on a Pi / Linux box on their home LAN, and gets a cockpit-style
  dashboard for their own field.
- **Nothing about any specific airfield is hardcoded.** Location, runways, magnetic variation,
  timezone, and the nearest reporting station are all resolved from the outdoor unit's GPS at runtime.
- **Offline-first:** the station starts, serves, and logs with no internet. Internet adds regional /
  ceiling data but is never required.

## Relationship to the base project

- Built on `weather-station-public` (MIT, same author). **Seed the new repo from that codebase; do
  not greenfield.** The backend (FastAPI app, SQLite/WAL, TOML config, read-time derivations, the
  pluggable external-provider pattern, TTL cache, response composers, Pydantic schemas, the test
  suite) is ~80% reusable as-is.
- This document set captures the **aviation delta**. The base's `docs/design/02-api-design.md`
  (provenance taxonomy, bucketing, naming rules) and base ADR-0001 / ADR-0002 (optional external
  feed; offline-first dashboard) remain the substrate — extended, not replaced.
- The instrument-panel aesthetic carries forward as the design *language*; the "Jones" branding does
  **not**.

## Locked decisions (do not re-litigate; surface as a question if you disagree)

| # | Decision |
|---|----------|
| 1 | Seed from `weather-station-public`; aviation work is a series of small deltas, not a rewrite. |
| 2 | **Density altitude is the hero metric.** Already computed in the base (`derived.density_altitude_*`); surface it prominently. |
| 3 | **Add altimeter setting (QNH)** as a new `D-READING`: the ISA / standard-atmosphere reduction to field elevation, *temperature-independent*. Distinct from the base's true sea-level pressure (`pressure_sealevel_*`, which is temperature-dependent). Same BUG-21 lesson — one SI quantity, two definitions, two explicitly-named fields. |
| 4 | **Wind comes from a local anemometer → `RAW` / `CALIBRATED` / derived.** The fused comfort/agronomy indices (wind chill, apparent temp, Beaufort, THSW, ET₀) move from `external` into `derived`. Extends base ADR-0001. See ADR-0003. |
| 5 | **Location intelligence is GPS-driven and offline.** Nearest airport + runways from a bundled OurAirports CSV; magnetic variation from a bundled WMM2025 model. Config override (by ident) + manual entry for unmapped strips. Mirrors the `timezonefinder` precedent. See ADR-0004. |
| 6 | **Runway component calculator:** head / cross / tailwind per runway end, favored runway, crosswind-limit warning. Canonical internal frame is **true**; magnetic variation is exposed so the UI can present magnetic (what pilots actually fly). |
| 7 | **METAR external provider** (aviationweather.gov, keyless) for ceiling, visibility, and flight category, plus an altimeter cross-check. Offline-first: block is null when the feed is off / unreachable. Always attributed with station + distance + age. See ADR-0005. |
| 8 | **Flight category (VFR/MVFR/IFR/LIFR) comes only from a METAR** — never derived from our own sensors. We have no ceilometer. |
| 9 | **Units are user-configurable** (no baked-in US conventions). Aviation-universal units (knots, feet) stay fixed; inHg↔hPa, SM↔km, °F↔°C switch. |
| 10 | **Twilight data is universal; regulatory interpretation is an optional, region-labeled overlay, OFF by default.** (Position lights, night currency, etc. are jurisdiction-specific.) |
| 11 | **UNOFFICIAL / not-for-flight-planning is a persistent, non-removable element.** No go/no-go automation. |
| 12 | **Branding lives in `branding.toml`** as visible placeholder slots (name, tagline, footer, state copy). Do not invent branding. No "Jones" references anywhere. |
| 13 | **Dashboard reskinned to EFIS / instrument-panel**, vanilla JS/HTML/SVG (no framework — per base), self-hosted fonts. Color encodes provenance: local = cyan, internet-sourced = violet. Offline-first dimming (only the internet-sourced panels dim). Visual spec: `docs/design/dashboard-mockup.html`. |
| 14 | **Nothing location-specific is hardcoded** anywhere in code. |
| 15 | **No light sensor (TSL2591 removed).** Outdoor suite = BME280 + GPS + anemometer. Set `has_light = false`; the base then cascades `derived.sky`, THSW, hourly ET₀, UV, and DLI to absent on their own. Sky/cloud comes from METAR (ADR-0005), which is authoritative — the light-sensor estimate was redundant and less reliable for a pilot. |
| 16 | **Flexible anemometer topology** *(post-v1)*. Wind may come from the **outdoor** unit (all-in-one, or a remote anemometer on a cable to the same ESP32 — the **default**) or from an opt-in **separate wind station** (a dedicated ESP32 + anemometer, no GPS, logged). A config `wind_source` selects it; a read-time resolver feeds the wind derivations regardless of origin. A **freshness guard** nulls wind-derived fields (incl. the runway solution) when the latest wind exceeds a configurable max age. See ADR-0006. |
| 17 | **No indoor / basement sensors** *(post-v1)*. Removed as inherited home-weather scope with no aviation purpose; `/current` = outdoor + airport + astronomy + external. The multi-station *mechanism* is retained (ADR-0006 reuses it); only the instances go. No migration (they were never logged). See ADR-0007. |

## New components

- **Altimeter derivation** — `derivations/readings.py`: add `altimeter_setting_inhg/_hpa`. Pure;
  cascades to null without field elevation.
- **Location intelligence** — bundled datasets + a new derivation module (e.g. `derivations/airport.py`):
  nearest-airport match (haversine), magnetic variation (WMM), field elevation. Promote `haversine_km`
  out of `external/providers.py` into a shared util.
- **Runway calculator** — head / cross / tailwind, favored end, crosswind-limit flag (depends on the
  current *corrected* wind direction).
- **METAR provider** — new provider in `external/providers.py` returning a normalized aviation
  observation (ceiling, layers, visibility, flight category, altimeter); nearest station auto-discovered
  from GPS.
- **Dashboard** — the EFIS reskin implemented against the live API.
- **Anemometer ingestion** — outdoor sketch + `wire_format.py` + DB schema.

## Bundled offline datasets (new)

- **OurAirports** `airports.csv` + `runways.csv` (public domain). Full set is small; a regional slice
  is tiny. Refresh periodically.
- **WMM2025** coefficient file (public domain; valid 2025–2030; ~336 coefficients). Refresh ~every 5
  years. Declination computed from GPS lat/lon/alt + date (signed, +east).

## Hardware

Outdoor sensor suite: **BME280** (temp / humidity / pressure) + **GPS** + **anemometer**. No light
sensor — the TSL2591 is removed (decision 15). Indoor/basement sensors are not used (decision 17).

- **Primary recommendation: Davis 6410** (wind speed + direction, continuous-pot direction,
  hurricane-rated). **Budget: SparkFun Weather Meter Kit** (reed-switch + resistor-network, 8-point
  direction, includes a rain gauge).
- ESP32 wiring: interrupt-driven pulse counter for speed; ADC read for direction.
- The vane's north-alignment is a **per-install calibration offset in config** (like the existing
  `temp_offset_c`), not a constant in code. Raw direction = vane reading; corrected (true) direction =
  raw + offset. The offset travels with whichever device physically holds the anemometer.
- Siting matters: keep it clear of house / hangar roof turbulence (a building-eddy reading is the
  failure mode).

**Anemometer mounting topology (decision 16 / ADR-0006).** The anemometer and the BME280 have
conflicting siting needs (wind wants a high, clear mast; the BME280 wants shaded ventilation), so three
arrangements are supported and the right one is site-dependent:

1. **All-in-one** *(default)* — anemometer + BME280 + GPS on one ESP32.
2. **Remote anemometer on a cable** *(default-equivalent)* — anemometer high on a mast, wires run down
   to the **same** ESP32. Identical to the firmware; just a longer sensor lead.
3. **Separate wind station** *(opt-in)* — a dedicated ESP32 + anemometer (no GPS) at the good wind spot,
   reporting independently and logged like the outdoor unit. Selected via `wind_source` in config.

A read-time wind resolver feeds the wind derivations from whichever source is configured; a freshness
guard nulls wind-derived fields past a configurable max age (default ~60–120 s).

## Out of scope for v1

- **No forecasting / TAF.** METAR is observations only. (Base already excludes forecasting; reaffirmed.)
- **No ceiling measurement.** No ceilometer; ceiling is METAR-sourced and clearly attributed.
- **No multi-airport.** One station, one field (nearest / override).
- **No auth / remote access.** LAN-only, per base. Remote = a reverse-proxy concern, not ours.
- **No write-back to sensors / calibration endpoints.** Config + restart, per base.
- **No copy localization.** Units are configurable; UI copy stays English in v1.
- **Regulatory overlay ships off.** Twilight data present; legal interpretation not enabled by default.
- **Rain-gauge logging optional**, not required for v1 (the SparkFun kit includes one; nice-to-have).

## Deltas from the base codebase (files that change)

- `derivations/readings.py` — + altimeter setting.
- `derivations/airport.py` (new) — nearest airport, magnetic variation, runway components.
- `external/providers.py` — + `metar` provider; promote `haversine_km` to a shared util.
- `schemas.py` — + raw wind, + `airport` block, + aviation external fields; fused indices move to `derived`.
- `db.py` — + wind columns; `SCHEMA_VERSION` 1→2 + a migration path (base raises on mismatch).
- `wire_format.py` — + wind fields.
- `routes/` — + `/api/v1/airport`; aviation fields fold into `/current` + `/external`.
- `config.py` + `weather.toml.example` — + `[airport]`, `[units]`, crosswind limit, per-sensor
  `has_wind` / `vane_offset_deg`, METAR provider opts.
- `dashboard/*` — EFIS reskin; `branding.toml` slots.
- `sketches/outdoor.ino` — + anemometer; − TSL2591 (light) read.
- `derivations/light.py` + `_build_sky_block` — left dormant (gated off by `has_light = false`); no removal needed.
- New data dir for the bundled CSVs + WMM coefficients.
- `tests/` — coverage for every new derivation / provider / endpoint.

### Post-v1 changes (Cycles 9–10)

**Cycle 9 — remove indoor/basement (ADR-0007), pure subtraction, no migration:**
- `sketches/indoor.ino`, `sketches/basement.ino` — removed.
- `server/fixtures/indoor.json`, `basement.json`, and indoor/basement wire samples — removed.
- `schemas.py` — drop the indoor/basement sections from `/current`.
- `config.py` + `weather.toml.example` — drop indoor/basement sensor entries.
- on-demand-poll / TTL-cache path — removed **only if** it served indoor/basement alone; kept if shared
  machinery ADR-0006 reuses.
- `tests/` — drop/adjust indoor/basement coverage.

**Cycle 10 — flexible anemometer topology (ADR-0006), additive on the simplified base:**
- `config.py` + `weather.toml.example` — + `wind_source`, + wind-freshness max-age.
- new read-time **wind resolver** — returns latest field wind + its age from the configured source; the
  true-direction / runway-solution / fused-index derivations consume it.
- `db.py` — + `wind_readings` table as an **additive v2→v3 migration** (reuses the 6b framework).
- `wire_format.py` — + parse the separate wind station's payload.
- `sketches/wind_station.ino` (new) — ESP32 + anemometer only, no GPS, logged.
- freshness guard — nulls wind-derived fields past max age.
- `tests/` — resolver source-selection, freshness nulling, the v2→v3 migration (lossless), wind-station
  ingestion.

## Safety stance (summary)

Unofficial, always. Persistent banner. Internet / METAR data always carries source + distance + age.
Flight category only ever from a METAR. No go/no-go automation. See ADR-0005.
