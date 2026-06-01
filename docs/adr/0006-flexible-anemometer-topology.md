# 0006. Flexible anemometer topology (all-in-one or separate wind station)

**Date:** 2026-06-01
**Status:** Accepted
**Builds on** ADR-0003 (local anemometer makes wind first-class). Post-v1 change.

## Context

ADR-0003 established local wind as a first-class reading and Cycle 6 built it on the **outdoor** ESP32:
the anemometer's pulse/ADC reads ride on the outdoor payload, alongside the BME280 and GPS. That works,
but the anemometer and the temp/humidity/pressure sensor have **conflicting siting requirements**. Good
wind data needs the vane/cups mounted high and clear of building turbulence (the ADR-0003 siting
caveat); the BME280 wants shade and ventilation near the area of interest; the GPS wants sky view. At
many fields these can't all live in one spot. A single fixed topology forces a bad compromise at some
sites.

Three physical arrangements are reasonable, and which one fits is **site-dependent**:

1. **All-in-one** — anemometer, BME280, GPS on one ESP32 (what Cycle 6 built).
2. **Remote anemometer on a cable** — anemometer mounted high on a mast, its wires run down to the
   **same** ESP32 as the BME280/GPS. No second microcontroller. Electrically identical to (1) from the
   firmware's view; just a longer sensor lead.
3. **Separate wind station** — a dedicated ESP32 + anemometer at the good wind-mounting spot, reporting
   independently; the BME280/GPS outdoor unit lives elsewhere.

## Decision

Support all three, with **all-in-one (including the remote-cable variant) as the default** and the
**separate wind station as an opt-in**. The common case stays a single device; the second-device
complexity is only paid by sites that need distant wind mounting.

- **Config-declared wind source.** A `wind_source` setting selects where field wind comes from: the
  **outdoor** unit's payload (default — topologies 1 and 2, unchanged from Cycle 6) or a **dedicated
  wind-station ID** (topology 3).
- **Read-time wind resolver.** Consistent with the project's "all derivations computed at read time"
  rule, a small resolver returns the latest field wind from whichever source `wind_source` names. The
  wind-dependent derivations — `wind_direction_true_deg`, the runway solution, and the wind-fused
  comfort indices — consume the resolver's output and **do not care which device produced the wind**.
- **The separate wind station is minimal:** ESP32 + anemometer only. **No GPS** — location intelligence
  stays anchored to the outdoor unit's GPS (one field, one position). It is **logged** like the outdoor
  unit (its own readings table), so it gets wind history on the same footing.
- **The vane offset travels with the device that physically holds the anemometer** (it's that vane's
  north-alignment), whether that's the outdoor unit or the separate wind station.
- **Storage:** the separate wind station gets a new `wind_readings` table, added as an **additive
  v2→v3 schema migration** reusing the forward-migration framework built in Cycle 6b. Existing outdoor
  data (including outdoor-borne wind from topologies 1/2) is untouched.

## Wind freshness guard (part of this decision)

With wind on a **separate** device, that device can fail while the outdoor unit keeps reporting. Without
a guard, the dashboard would present minutes-old wind as current and the runway solution would recommend
a favored runway computed from **dead-sensor wind** — an actively misleading failure for a pilot-facing
tool.

- The resolver returns wind **with its observation age**. Wind-derived fields (true direction, runway
  solution, and the wind-fused comfort indices) are **nulled when the latest wind exceeds a configurable
  max age** — default a small multiple of the report interval (~60–120 s).
- It applies to **both** topologies for safety, but earns its keep in the separate-station case (in the
  all-in-one case, stale wind usually means the whole reading is stale anyway).
- Kept deliberately small: **one threshold**, no elaborate staleness model.

## Alternatives considered

- **Keep all-in-one only (status quo).** Rejected — forces a siting compromise that produces bad wind or
  bad thermo at sites where the two can't co-locate.
- **Make the separate station the default / only topology.** Rejected — it imposes a second wifi node,
  second power feed, and merge/freshness logic on every build, including the majority that don't need
  distant wind. Complexity should be opt-in.
- **Time-synchronize the two devices.** Rejected as unnecessary. Wind+thermo fusion tolerates a few
  seconds of skew, and the runway solution uses **only** wind, so cross-device clock skew is irrelevant.
  No time-sync machinery — just the single freshness check above.
- **Push wind merging to firmware (one device relays the other).** Rejected — couples the two devices
  and breaks the clean "each sensor reports independently, server composes at read time" model.

## Consequences

- A `wind_source` config knob and a new `wind_readings` table (additive v2→v3 migration; no risk to
  existing data).
- The wind-consuming derivations gain a single indirection (the resolver) but otherwise are unchanged —
  they already read a normalized wind input as of Cycle 6.
- A second sketch (wind-station firmware) for the opt-in topology; the outdoor sketch is unchanged.
- One new failure mode handled explicitly (stale separate-station wind) via the freshness guard;
  documented as a configurable threshold.
- The hardware guide must explain all three topologies and when to choose each; the install guide gains
  `wind_source` and the freshness threshold.

## Revisit if

- Multiple anemometers per field are ever wanted (e.g. opposite ends of a long strip) — today it's one
  wind source per station.
- The freshness threshold proves too blunt (e.g. a site wants "show stale wind, but visibly flagged"
  rather than nulled) — could become a presentation choice rather than a hard null.
