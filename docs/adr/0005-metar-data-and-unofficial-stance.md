# 0005. METAR data for ceiling/visibility/flight-category, and the unofficial-only stance

**Date:** 2026-06-01
**Status:** Accepted

## Context

Pilots care about ceiling, visibility, and flight category (VFR/MVFR/IFR/LIFR) — the VFR go/no-go
inputs. **This station cannot measure ceiling** (no ceilometer), and its light-sensor sky estimate is a
flagged guess, not a height-of-layer measurement. The authoritative source is a nearby METAR. Separately,
a backyard sensor plus nearby reports is *not* a legal weather briefing, and presenting it as if it were
would be actively dangerous. Both concerns are about how we handle authoritative-looking aviation data we
do not own.

## Decision

**METAR provider.** Add a `metar` provider to the existing pluggable `external/providers.py` pattern,
returning a normalized aviation observation: ceiling (lowest BKN/OVC base), sky layers, visibility,
flight category, station altimeter (for cross-check), temp/dewpoint.

- Source: aviationweather.gov Data API — official FAA/NWS, free, keyless, JSON
  (`.../api/data/metar?ids=<ID>&format=json`). Nearest station auto-discovered from GPS (reuse the NWS
  provider's nearest-station logic). ≤100 req/min; custom User-Agent; schema changed Sept 2025.
- Offline-first: any failure → the `external` block is null. Unchanged invariant.
- `flight_category` comes from the METAR (or is computed from ceiling + visibility via standard
  thresholds) — **never** from our own sensors.
- The station's reported altimeter cross-checks our locally-derived altimeter setting (mirrors the base
  NWS `cross_check` / `confidence` pattern); a large divergence is a calibration/distance signal.

**Unofficial-only stance.** Non-removable:

- A persistent **UNOFFICIAL — NOT FOR FLIGHT PLANNING** banner; never a substitute for an official
  preflight briefing or current METAR/TAF.
- All internet/METAR data is **always attributed** with station + distance + age — it is conditions
  *at the reporting station*, not necessarily over the user's field.
- **No go/no-go automation.** We display data and components; the pilot decides.
- **No TAF / forecasting** (consistent with the base's removal of forecasting). METAR is observations
  only.

## Alternatives considered

- **Open-Meteo / model cloud cover for ceiling.** Rejected — it gives cloud-cover percentages and
  pressure-level cloud, not a measured ceiling; inferring a base is a fiddly model estimate.
- **A paid ceiling/cloud-base API (e.g. Meteomatics).** Rejected — breaks the keyless ethos.
- **Synthesize our own flight category from the light sensor.** Rejected — no ceilometer; would be
  misleading and unsafe.
- **Pull TAFs for a real forecast.** Rejected for v1 — reintroduces forecasting, a deliberate base
  exclusion. Could be a clearly-labeled opt-in later, but not a freebie.

## Consequences

- One more provider in the existing pattern; the offline-first guarantee is preserved and testable.
- Ceiling/flight-category quality depends on distance to the nearest reporting station — which is why
  attribution is mandatory, not cosmetic.
- The unofficial framing constrains the UI (persistent banner, mandatory provenance captions) but is
  the right liability and safety posture for a hobby device used near real flying.

## Revisit if

- The nearest reporting station is far enough that ceiling is unhelpful (attribution lets the user
  judge; consider surfacing "nearest station N nm" prominence).
- A jurisdiction's rules require different presentation of unofficial weather data.
