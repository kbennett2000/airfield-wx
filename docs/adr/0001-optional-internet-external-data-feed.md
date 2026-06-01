# 0001. Add optional internet-sourced external data feed with provenance-based API placement

Date: 2026-05-29
Status: Accepted

## Context

The station has no local wind sensor. Wind is the most siting-sensitive measurement
in amateur meteorology, and the wrong mounting location or a nearby obstruction can
make a co-located anemometer less useful than a nearby model analysis point. Several
derived indices that users expect from a weather station — wind chill, apparent
temperature, Beaufort force, THSW index, and reference evapotranspiration (ET0) —
are meaningless without wind speed.

The project has a hard constraint: **offline-first**. The station runs on a home LAN
with no guarantee of internet access. The server must start, serve, and log without
any external dependency. Real deployments include Pi Zero 2 W units on rural properties
where the WAN link drops routinely. This means "add wind support" could not mean
"block `/current` until a fetch succeeds" or "return errors when the upstream is down."

There was also a related ask to add a windowed history summary endpoint
(`/api/v1/summary`) — pressure tendency, degree days, DLI, Hargreaves ET0 — derived
from the logged outdoor time series. That introduced a new data category (aggregated
history) that the existing provenance taxonomy did not cover.

The existing provenance tags from `docs/design/02-api-design.md` are:
`RAW`, `CALIBRATED`, `D-READING`, `D-LOCATION`, `D-TIME`, `META`.

## Decision

Add an optional internet-sourced data feed, implemented as a background timer task
that never runs in a request handler, with results held in an in-memory store. The
feed is **disabled by default**; enabling it requires an explicit `[external]` section
in `weather.toml`. When disabled — or when the internet is unreachable — the
`external` block in `/api/v1/current` is `null` and the server behaves byte-identically
to a pre-feature deployment.

API placement follows **provenance, not optionality**:

- Wind-dependent fused indices (wind chill, apparent temperature, THSW, ET0) live in
  `external`, not in `derived`. This makes the offline invariant provable: with no
  internet, `derived` is identical to what an offline-only server would produce.
  `external` is the only field that differs.
- The default provider is Open-Meteo `best_match` (keyless, global, data-assimilating
  NWP model). Two opt-in override providers — NWS nearest station and Weather
  Underground PWS — are also implemented.
- The summary endpoint (`/api/v1/summary/{sensor_id}`, outdoor only) introduces a
  `D-HISTORY` provenance tag for values derived from the logged time series. ET0 in
  the summary uses the Hargreaves temperature-only method rather than the FAO-56
  Penman-Monteith method, because the latter requires wind and humidity data not
  available without the external feed.

Two new provenance tags are introduced in code and schemas: `EXTERNAL` and `D-HISTORY`.
These extend the taxonomy but are **not yet reflected in `docs/design/02-api-design.md`**
because that file is a read-only design input. The human should fold these tags into
the design doc.

## Alternatives considered

- **Weather Underground PWS as the primary default** — PWS wind data varies enormously
  by site quality. For an arbitrary deployment the maintainer cannot vet the nearest
  PWS's mounting height or obstruction exposure. A data-assimilating NWP model (HRRR
  in the US via Open-Meteo `best_match`) fuses all nearby observations at the exact
  coordinate with no single-sensor failure mode and requires no API key. WU is kept as
  an opt-in override for operators who have a specific station they trust.

- **NWS nearest official station as the primary default** — Real meteorological
  observations with good instrument siting, but NWS stations are sparse: the nearest
  may be 25+ km away over different terrain. Open-Meteo at the exact GPS coordinate is
  more representative of local conditions. NWS is kept as an opt-in override and is
  also used as an optional cross-check reference to flag low-confidence wind readings.

- **A generic `/api/v1/optional` or `/api/v1/internet` endpoint** — Rejected because
  "optional" and "internet" are not coherent data categories; they describe the
  transport mechanism rather than what the data is. Consumers would have no principled
  way to understand the provenance of individual fields. Provenance-based placement
  (everything EXTERNAL in `external`, everything D-HISTORY in `summary`) is consistent
  with how the existing API already uses field blocks.

- **Storing wind as a logged column in the outdoor_readings table** — Rejected.
  The existing decision that "the DB stores raw sensor readings only" is load-bearing:
  it keeps the schema stable, lets formula bugs be fixed without backfill, and keeps
  the data source clear. External/model wind is not a sensor reading. The daily
  Hargreaves ET0 in the summary endpoint uses temperature min/max (which are logged)
  rather than the full Penman-Monteith equation (which would require wind) to respect
  this constraint.

- **Polling the external feed in the request handler with a TTL cache** — The existing
  pattern for indoor/basement sensors (poll on demand, 5 s TTL cache) works because
  those are LAN devices with sub-100 ms response times. An internet fetch can take
  several seconds or fail after a multi-second timeout. Putting that in a request
  handler would introduce latency spikes and timeout failures directly visible to
  dashboard users. A background timer task with a last-known-value store eliminates
  this entirely.

## Consequences

**What gets easier:**
- Wind-dependent indices are available with zero client-side logic once the feed is
  enabled. Dashboard and widget can render wind chill, apparent temperature, and
  Beaufort force by reading `external`.
- The offline-first guarantee is testable: disable `[external]`, compare `/current`
  output against an online deployment — only the `external` key differs.
- A keyless default (Open-Meteo) means zero-config internet deployment: uncomment
  `[external]` with `enabled = true` and the server works.
- The NWS cross-check (`cross_check = true` in config) provides a `confidence` flag
  without requiring the operator to do anything beyond enabling it.

**What gets harder / what this commits us to:**
- The `external` block and `summary` endpoint need to be kept null-safe everywhere in
  consumers (dashboard, widget). They cannot assume `external` is present.
- The design doc taxonomy (`docs/design/02-api-design.md`) is out of sync with the
  implementation until the human updates it. Two tags (`EXTERNAL`, `D-HISTORY`) exist
  in code but not in the doc.
- The Wunderground provider requires a free API key and a specific `station_id`. This
  is documented in `weather.toml.example` but is an operational burden compared to the
  keyless default.
- ET0 in `/summary` uses Hargreaves (temperature-only). This underestimates ET0 in
  windy, dry conditions. Upgrading to Penman-Monteith would require either logging
  wind from the external feed (which violates the raw-only DB constraint) or computing
  it on the fly from a rolling buffer. That trade-off was consciously deferred.

## Revisit if

- A local anemometer is added to the outdoor sensor. At that point wind becomes `RAW`,
  moves into `derived`, and the `external` block loses its primary rationale (though
  it could still carry cloud cover, UV, and precip).
- A consumer (dashboard or widget) needs to display wind-chill without enabling the
  full internet feed. That would require either a lightweight local wind source or
  splitting fused indices out of `external` into a separate block.
- Open-Meteo introduces rate limits or authentication for the `best_match` model. The
  current default is keyless and has no stated rate cap for residential use; if that
  changes the default provider decision needs to be revisited.
- The taxonomy in `docs/design/02-api-design.md` is updated to include `EXTERNAL` and
  `D-HISTORY`. At that point this ADR's note about the out-of-sync state can be
  removed.
