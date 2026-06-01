# 0007. Remove indoor and basement sensors

**Date:** 2026-06-01
**Status:** Accepted
**Post-v1 change.**

## Context

The base `weather-station-public` is a *home* weather station: it supports an **outdoor** unit plus
**indoor** and **basement** sensors. In that architecture, outdoor is continuously logged to
`outdoor_readings`, while indoor/basement are polled **on demand behind a TTL cache and never logged**
(which is why history and the daily summary have always been outdoor-only).

airfield-wx is a **pilot's field weather station**. The flight-relevant conditions — density altitude,
altimeter setting, wind, runway components, ceiling/visibility — are all outdoor/field quantities. A
basement or living-room temperature has **no aviation purpose**. Carrying indoor/basement forward only
adds noise: extra config blocks, extra schema sections on the public `/current` response, two extra
sketches, fixtures, and surface area for an "anyone can deploy" product to explain and maintain.

## Decision

Remove the **indoor and basement sensor instances** from airfield-wx.

- Drop their sketches, fixtures, config entries, and the indoor/basement sections of the `/current`
  schema and response. `/current` becomes **outdoor + airport + astronomy + external**.
- **Remove the instances, not the mechanism.** The multi-sensor-station capability itself is retained,
  because ADR-0006 (separate wind station) reuses it. If the on-demand-poll / TTL-cache path exists
  *solely* to serve indoor/basement, it can be removed too; if it's shared machinery ADR-0006 will use,
  keep it.
- **No migration needed.** Indoor/basement were never logged, so there are no database tables for them —
  this is pure subtraction, with no schema-version change and no risk to stored data.

## Alternatives considered

- **Keep them, just hidden/optional.** Rejected — they still bloat config, schema, and docs for zero
  aviation value; an anyone-can-deploy product is better without the dead weight.
- **Repurpose indoor as a hangar/cabin temp.** Rejected for v1 — speculative, not flight-relevant, and
  re-addable later if a real need appears (the base mechanism still exists in git history).

## Consequences

- Simpler config, a smaller `/current` payload, fewer sketches and fixtures, less to document.
- The EFIS dashboard already renders only aviation panels, so it should reference nothing
  indoor/basement — verify during removal.
- The retained multi-station mechanism is exactly what ADR-0006 builds the separate wind station on, so
  this removal and that addition are complementary: remove the inherited instances first, then extend a
  clarified single-station base.

## Revisit if

- A concrete aviation use for an additional indoor-style sensor emerges (e.g. logged hangar temperature)
  — it would come back as its own decision, not as inherited home-weather scope.
