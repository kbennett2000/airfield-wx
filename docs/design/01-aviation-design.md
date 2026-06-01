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
sensor — the TSL2591 is removed (decision 15).

- **Primary recommendation: Davis 6410** (wind speed + direction, continuous-pot direction,
  hurricane-rated). **Budget: SparkFun Weather Meter Kit** (reed-switch + resistor-network, 8-point
  direction, includes a rain gauge).
- ESP32 wiring: interrupt-driven pulse counter for speed; ADC read for direction.
- The vane's north-alignment is a **per-install calibration offset in config** (like the existing
  `temp_offset_c`), not a constant in code. Raw direction = vane reading; corrected (true) direction =
  raw + offset.
- Siting matters: keep it clear of house / hangar roof turbulence (a building-eddy reading is the
  failure mode).

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

## Safety stance (summary)

Unofficial, always. Persistent banner. Internet / METAR data always carries source + distance + age.
Flight category only ever from a METAR. No go/no-go automation. See ADR-0005.
