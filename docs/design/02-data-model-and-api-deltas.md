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

## Post-v1 deltas (Cycles 9–10)

### Remove indoor/basement (Cycle 9, ADR-0007)

Pure subtraction. `/api/v1/current` drops its `indoor` and `basement` sections and becomes
**outdoor + airport + astronomy + external**. The indoor/basement config blocks, sketches, and fixtures
are removed. **No schema-version change and no migration** — indoor/basement were polled on demand and
never logged, so there are no tables to migrate. The multi-station *mechanism* is retained for Cycle 10;
only the instances are removed.

### Flexible wind source + freshness (Cycle 10, ADR-0006)

**Config: `wind_source`.** Selects where field wind is read from:
- `"outdoor"` *(default)* — wind arrives on the outdoor payload (all-in-one or remote-cable; unchanged
  from Cycle 6).
- a **wind-station sensor ID** — wind comes from a dedicated, logged wind station.

**Read-time wind resolver.** A small resolver returns the latest field wind **and its observation age**
from the configured source. The wind-dependent derivations — `wind_direction_true_deg`, the runway
solution, and the wind-fused comfort indices — consume the resolver's output and are agnostic to origin.
This preserves the "all derivations at read time" rule and means switching topology is a config change,
not a code path.

**Freshness guard.** A `wind_max_age_s` config value (default ~60–120 s). When the latest wind is older,
the wind-derived fields **and the runway solution** are nulled — never present stale wind as current, and
never compute a favored runway from a dead sensor. Applies to both topologies; matters most for the
separate station. One threshold; no broader staleness model. (Cross-device time-sync is explicitly *not*
needed — see ADR-0006.)

**DB: `wind_readings` table.** The separate wind station logs to its own table, added as an **additive
v2 → v3 migration** reusing the Cycle 6b forward-migration framework. `SCHEMA_VERSION` 2 → 3. Existing
`outdoor_readings` data (including outdoor-borne wind) is untouched. The migration test must prove a
populated v2 DB upgrades to v3 with no data loss, exactly as the v1→v2 test did.

**Wire format.** `wire_format.py` learns to parse the wind station's payload (wind speed/gust/direction,
same raw fields as outdoor wind). The wind station's sketch (`wind_station.ino`) emits anemometer reads
only — no BME280, no GPS.

**Vane offset.** Travels with the device holding the anemometer — on the outdoor sensor's calibration in
topologies 1/2, on the wind station's in topology 3.
