# 0004. Offline, GPS-driven location intelligence (OurAirports + WMM)

**Date:** 2026-06-01
**Status:** Accepted

## Context

The aviation variant needs, at any field on Earth and with nothing hardcoded: the **nearest airport**,
its **runway headings**, **field elevation**, and the **magnetic variation** at that location (so true
model/METAR winds can be reconciled with the magnetic runway numbers a pilot flies). It must keep the
project's offline-first guarantee — the station runs with no internet — and its "deploy anywhere with no
configuration" goal. The base already sets the precedent: timezone is derived from GPS via a bundled
`timezonefinder` dataset, fully offline.

## Decision

Two bundled, public-domain datasets, both resolved from the outdoor GPS at runtime:

- **OurAirports CSVs** (`airports.csv` + `runways.csv`) for airport identity, runway headings, surface,
  and published field elevation. Nearest match via haversine (promote the existing `haversine_km` from
  `external/providers.py` to a shared util). Ship the full set (small) or a regional slice.
- **WMM2025 coefficients** (valid 2025–2030, ~336 coefficients) for magnetic declination, computed from
  GPS lat/lon/alt + date (signed, +east), via a small WMM library or port.

Overrides for the long tail, mirroring the base's `fallback_*` pattern:

- `[airport].ident` to override an ambiguous / wrong nearest match.
- Manual runway + field-elevation entry for strips not in OurAirports.
- Field elevation prefers the matched airport's **published** value over raw GPS vertical (GPS vertical
  is noisy, ±10–30 m).

Internal canonical frame for headings/wind is **true**; magnetic variation is exposed so the UI can
present magnetic. Prefer OurAirports' true runway heading; else derive true from the runway number
(magnetic ×10) + WMM variation.

## Alternatives considered

- **Online airport / declination API.** Rejected — breaks offline-first; a rural strip is exactly where
  the WAN is least reliable.
- **Hardcode the field / variation.** Rejected — breaks "anyone, anywhere." This is the explicit reason
  the variant exists.
- **FAA NASR data.** US-only and heavier; OurAirports is global, public-domain, and CSV-simple.
- **Auto-detect only, no override.** Rejected — private/grass strips and close-together fields need an
  override and a manual-entry escape hatch.

## Consequences

- Two datasets to bundle and refresh: WMM ~every 5 years (next 2030); OurAirports periodically.
- Disk cost is modest (CSV + a tiny coefficient file), in the spirit of the ~80 MB `timezonefinder`
  precedent.
- True↔magnetic conversion lives in one place, tested once.
- A strip not in OurAirports still works via manual config — no dead end.

## Revisit if

- A station is nowhere near any airport in the data (manual entry is the answer).
- WMM2025 expires (refresh the coefficient file) or a higher-resolution variation source is wanted.
