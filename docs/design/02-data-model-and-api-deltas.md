# Data Model & API — Aviation Deltas

**Status:** design captured (build pending)
**Date:** 2026-06-01
**Reads against:** the base `docs/design/02-api-design.md`. This file lists only what *changes* or is
*added*. Everything not mentioned here is unchanged. The Pydantic models in `schemas.py` remain the
runtime contract; this is the why-it's-shaped-this-way for the aviation additions.

## Provenance

Taxonomy is unchanged (`RAW`, `CALIBRATED`, `D-READING`, `D-LOCATION`, `D-TIME`, `META`, `EXTERNAL`,
`D-HISTORY`). Placement notes for the new data:

- Local wind → `RAW` (sensor reading) / `CALIBRATED` (vane-offset applied) / `D-READING` (fused indices).
- Airport identity + runway headings + field elevation + magnetic variation → `D-LOCATION`
  (functions of position; effectively static for a fixed station).
- Runway solution (head/cross/tailwind, favored end) → derived from current corrected wind →
  recompute per request (treat like `D-TIME`).
- Ceiling / visibility / flight category / METAR altimeter → `EXTERNAL` (internet-sourced; null offline).

## Wind: now local

Base sourced wind from the optional model feed and placed fused indices in `external`. With a local
anemometer:

**`raw`** (new): `wind_speed_ms`, `wind_gust_ms`, `wind_direction_deg` (raw vane frame).
**`calibration`** (new): `wind_vane_offset_deg` (per-install north alignment; mirrors `temp_offset_c`).
**`derived`** (corrected + fused, all move here from `external`):
`wind_direction_true_deg` (= raw + offset), `wind_chill_c/f`, `apparent_temperature_c/f`,
`beaufort_force`, `beaufort_description`, `thsw_index_c/f`, `et0_mm_hour`.

**No light sensor (default — design decision 15):** `thsw_index_*` and `et0_mm_hour` require solar
irradiance and therefore cascade to null. Wind chill, apparent temperature, and Beaufort run on wind
alone and remain. (`derived.sky`, UV, and DLI are likewise absent.)

Wind is logged (it's an outdoor RAW reading) → new wind history. (Full FAO-56 Penman-Monteith ET₀ also
needs solar radiation, which the default no-light build lacks — so the daily summary ET₀ stays
Hargreaves / temperature-only, as in the base. See decision 15 and ADR-0003.)

## Pressure: add altimeter setting

**`derived`** (new): `altimeter_setting_inhg`, `altimeter_setting_hpa`.

- Definition: station pressure reduced to field elevation under the **standard atmosphere (ISA)** —
  the QNH a pilot dials in. **Temperature-independent**, unlike the base's `pressure_sealevel_*`
  (true SLP, temperature-dependent). Both are kept; they answer different questions.
- Inputs: station pressure + field elevation. Cascades to null if elevation is unknown.
- Test it against a known field's published METAR altimeter (same spirit as the base's Denver
  pressure-quadruple test).

`pressure_altitude_*` and `density_altitude_*` already exist in base `derived` and are correct for
aviation — surface them; no recompute needed.

## New: `airport` block

Present on `GET /api/v1/current` (single round trip) and standalone at `GET /api/v1/airport`
(mirrors `/astronomy`, `/external`).

```json
"airport": {
  "ident": "—", "name": "—", "type": "small_airport",
  "field_elevation_ft": 6912,
  "distance_nm": 0.2,
  "source": "gps_nearest",            // gps_nearest | config_override | manual
  "magnetic_variation_deg": 9.2,      // signed, +E; from WMM
  "magnetic_model": "WMM2025",
  "runways": [
    { "ident": "12/30", "le_ident": "12", "he_ident": "30",
      "le_heading_true_deg": 129.2, "he_heading_true_deg": 309.2,
      "length_ft": 1700, "surface": "gravel" }
  ],
  "runway_solution": {                // depends on current corrected wind; recomputed per request
    "favored": "30",
    "ends": [
      { "ident": "30", "headwind_kt": 11, "crosswind_kt": 4, "crosswind_side": "L",
        "tailwind": false, "crosswind_exceeds_limit": false },
      { "ident": "12", "headwind_kt": -11, "crosswind_kt": 4, "crosswind_side": "R",
        "tailwind": true,  "crosswind_exceeds_limit": false }
    ]
  }
}
```

Notes:
- `field_elevation_ft` prefers the matched airport's **published** elevation over raw GPS vertical
  (GPS vertical is noisy, ±10–30 m).
- Runway true headings prefer OurAirports' values; if only the runway *number* is known, derive true
  from the magnetic number (×10) + WMM variation.
- `runway_solution` is null when there is no current wind reading. `crosswind_exceeds_limit` compares
  against `[airport].crosswind_limit_kt` in config.

## EXTERNAL: aviation additions (METAR provider)

The base `ExternalBlock` already carries model wind/cloud/uv/precip/visibility + fused indices. With
the new `metar` provider selected (or running alongside), add an aviation observation. Keep it in
`external` (offline-first invariant unchanged — whole block null when off).

New fields: `metar_raw`, `station_id`, `distance_km`, `observed_at`, `flight_category`
(`VFR`/`MVFR`/`IFR`/`LIFR`), `ceiling_ft_agl` (lowest BKN/OVC base; null if none),
`sky_layers` (`[{cover, base_ft_agl}]`), `visibility_sm`, `altimeter_inhg` (station's reported QNH),
`temp_c`, `dewpoint_c`.

Provider mechanics (see ADR-0005): aviationweather.gov Data API, keyless, JSON, endpoint
`https://aviationweather.gov/api/data/metar?ids=<ID>&format=json`; nearest station auto-discovered from
GPS (reuse the NWS provider's nearest-station pattern); ≤100 req/min; set a custom User-Agent; schema
changed Sept 2025. Fail-soft (any error → block null). `flight_category` taken from the METAR or computed
from ceiling + visibility using standard thresholds; never from our own sensors.

## New / changed endpoints

- `GET /api/v1/airport` — the `airport` block alone. Optional `?lat=&lon=` override (like `/astronomy`).
- `GET /api/v1/current` — now includes `airport`; `external` may include the aviation fields.
- METAR data folds into `/external` (no separate `/metar` endpoint in v1).

## Config additions (`weather.toml`)

```toml
[units]
system = "imperial"          # imperial | metric (knots & feet stay fixed regardless)

[airport]
# all optional; default is GPS-nearest from OurAirports
# ident = "____"             # override the auto-detected field
crosswind_limit_kt = 15
# [[airport.runways]]        # manual entry for strips not in OurAirports
#   ident = "12/30"
#   le_heading_magnetic_deg = 120
#   length_ft = 1700
#   surface = "gravel"

[overlay]
regulatory = false           # position-light / night-currency interpretation; off by default

# per-sensor additions on the outdoor [[sensors]] entry:
#   has_wind = true
#   vane_offset_deg = 0.0     # align the vane's reading to true north

# [external] gains provider = "metar" alongside open-meteo | nws | wunderground
```

## DB schema delta

`outdoor_readings` gains `wind_speed_ms`, `wind_gust_ms`, `wind_direction_deg` (raw). Bump
`SCHEMA_VERSION` 1 → 2. **The base raises on a version mismatch with no migration** — add a real
migration path (ALTER TABLE ADD COLUMN is sufficient; columns are nullable) so existing deployments
don't lose history. Wind is stored raw; all corrections/derivations stay at read time.
