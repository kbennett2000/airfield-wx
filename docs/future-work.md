# Future work

Things deliberately deferred from v1 and the post-v1 topology work. None are commitments — they're a
backlog of "revisit if" items so the reasoning isn't lost. Each notes *why* it was deferred and *when*
it would be worth doing.

## Sensing & derivations

- **Penman-Monteith ET₀ (needs a solar sensor).** The daily summary ET₀ is currently Hargreaves
  (temperature-only). The TSL2591 light sensor was removed (decision 15) because its sky/cloud estimate
  was redundant with METAR and irrelevant to flying — but that also removed the solar-irradiance input
  that full FAO-56 Penman-Monteith ET₀ requires. *Revisit if* a solar/pyranometer sensor is ever added
  back for agronomy reasons; the `light.py` derivation code is still in place, dormant. (ADR-0003.)

- **History / summary wind enrichment.** When logging is enabled, wind is recorded — both outdoor-borne
  wind and the separate wind station's `wind_readings` — but `/api/v1/history` and `/api/v1/summary`
  don't yet surface it. The daily summary could gain wind stats (average, peak gust, prevailing
  direction). Noted out of scope in Cycles 6b and 10b. **Requires `[logging] enabled = true`** (history
  logging is off by default — Cycle 13). *Revisit if* there's appetite for wind history/trends beyond
  the live dashboard.

## Location & data

- **`?lat=&lon=` overrides on `/api/v1/airport`** (and astronomy). Sketched in the data-model doc, never
  built — the endpoints resolve from the station's GPS only. *Revisit if* anyone wants to query the
  airport/runway solution for a point other than the installed station (e.g. a planning aid). Low effort,
  was simply not needed for the core "my field" use case.

- **Regional OurAirports slicing.** The bundled CSVs are the full global set (~16 MB). A build/config
  option to ship only a region would shrink the install footprint for constrained hosts. *Revisit if*
  disk/footprint becomes a concern (e.g. very small SD cards); the full set is fine on a Pi today.
  (ADR-0004.)

## Wind topology (ADR-0006 "revisit if")

- **Multiple anemometers per field.** Today it's one wind source per station (`wind.source`). A long
  strip might want wind at both ends. *Revisit if* that need appears — would mean more than one wind
  source and a rule for combining/selecting them.

- **"Show stale wind, flagged" instead of nulling.** The freshness guard currently *nulls* wind-derived
  fields (and the runway solution) past `wind.max_age_s`. An alternative is to keep showing the last
  wind but visibly mark it stale. *Revisit if* a site prefers "old data, clearly labeled" over "no data"
  — this would become a presentation choice rather than a hard null.

## Documentation & presentation

- **Real hardware photos.** The hardware guide is diagrams + text because there is no physical build to
  photograph. Real photos of an assembled unit (the ESP32, the Davis 6410 on its mast, the wiring, the
  enclosure) would be the single biggest warmth upgrade to the build guide. *Revisit if/when* someone
  builds the unit and can contribute photos.

- **A short build-walkthrough video or annotated photo series.** Same spirit — the calibration steps
  (especially vane north-alignment) are much easier to follow visually. Future, contributor-friendly.

## Possible larger directions (not planned, just noted)

- **TAF / forecast** — deliberately excluded (METAR is observations only; ADR-0005). Could be a clearly
  labeled, opt-in addition someday, but it reintroduces forecasting, which v1 intentionally avoids.
- **Multi-field / "nearby strips"** — v1 is one station, one field by design. A planning-oriented variant
  could list nearby fields, but that's a different product from "my airstrip's conditions."
