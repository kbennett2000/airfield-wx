# 0003. Local anemometer makes wind first-class (RAW → derived)

**Date:** 2026-06-01
**Status:** Accepted
**Continues** the base ADR log (base ADR-0001 / ADR-0002 carry over from `weather-station-public`).

## Context

The base station has no wind sensor. Base ADR-0001 deliberately sourced wind from an internet model
point and placed all wind-dependent indices (wind chill, apparent temperature, Beaufort, THSW, ET₀) in
the optional `external` block — and explicitly named "a local anemometer is added" as the trigger to
revisit. For a pilot, **the wind at their own field is the entire point**, and a model point miles away
is not good enough for a takeoff/landing decision. So this variant adds a real anemometer.

## Decision

- Wind becomes a first-class local reading. `raw.wind_speed_ms / wind_gust_ms / wind_direction_deg`
  come off the outdoor ESP32. The vane's north alignment is a per-install `calibration.wind_vane_offset_deg`
  (mirrors `temp_offset_c`); corrected `derived.wind_direction_true_deg` = raw + offset.
- The fused indices **move from `external` into `derived`**, now computed from local wind. The existing
  `fused.py` functions are reused — only their wind input changes.
- Wind is **logged** (it is an outdoor RAW reading), which adds wind history. (Full FAO-56
  Penman-Monteith ET₀ also needs solar radiation; the default no-light build — decision 15 — lacks it,
  so the summary ET₀ stays Hargreaves / temperature-only.)
- The optional external feed is **demoted, not removed**: it still supplies cloud / ceiling / visibility
  (things the local sensors can't measure — see ADR-0005). Offline-first is unchanged.
- Recommended hardware: Davis 6410 (continuous-pot direction, rugged) as primary; SparkFun Weather Meter
  Kit as the budget option. ESP32: interrupt pulse counter for speed, ADC for direction.
- Outdoor sensor suite is BME280 + GPS + anemometer; the TSL2591 light sensor is removed (decision 15),
  so light-derived sky / THSW / UV / DLI and hourly ET₀ are absent by default.

## Alternatives considered

- **Keep model wind (status quo).** Rejected: field wind is the headline aviation requirement; a model
  point doesn't capture the strip's actual conditions.
- **Sonic anemometer.** Better in theory (no moving parts) but pricier and less proven in cheap DIY
  builds; cup + vane is robust and well-documented for ESP32.
- **Store wind as a derived/non-logged value.** Rejected: a genuine sensor reading belongs in `raw` and
  in the log, consistent with the base's "DB stores raw readings only."

## Consequences

- DB schema bump (v1 → v2) with a migration (see data-model deltas) — the base raises on mismatch, so a
  migration path is required.
- All consumers must stay null-safe when `has_wind` is false / the sensor is offline (no anemometer is a
  valid config).
- Siting caveat is now ours to document: a vane in roof turbulence reads wrong.
- The `external` block keeps its offline-first null behavior; only its *role* shrinks.

## Revisit if

- A station has no anemometer — wind/fused indices simply fall back to the external feed (if enabled) or
  go null. The code must support both.
- ET₀ accuracy matters enough to add a solar measurement back (Penman-Monteith needs solar radiation,
  not just wind) — i.e. reintroduce a light/pyranometer sensor.
