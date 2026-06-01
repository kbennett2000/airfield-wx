# 0002. Internet-optional dashboard UX: isolated regional panel, adaptive feels-like, reused offline visual language

Date: 2026-05-29
Status: Accepted

## Context

ADR-0001 established an optional internet feed (`external` block) and placed all
wind-dependent indices there. The implementation question left open was: how does the
dashboard surface that data without undermining the station's offline-first
guarantee, and without making the UI look broken when the feed is absent?

Three tensions needed resolving:

1. **Provenance legibility.** The outdoor panel shows local sensor readings.
   Internet-sourced wind, comfort indices, cloud cover, UV, and precip have
   fundamentally different provenance — a nearby NWP model point, not an onsite
   instrument. Mixing them into the outdoor panel obscures that distinction and makes
   gaps read as sensor failures.

2. **Offline state vs broken state.** The station runs on a LAN without a guaranteed
   WAN link. When `external` is null — either because the feed is disabled in config
   or because the internet is unreachable — every affected readout would show dashes.
   Without a deliberate offline affordance, those dashes look like a bug.

3. **Single best "feels like" in the hero.** Users expect one number to represent
   thermal comfort. Locally, the server computes a heat-index feels-like from
   temperature and humidity. When the feed is online, a wind-aware apparent
   temperature is available and is more informative. Showing both or picking one
   unconditionally each has tradeoffs.

A second, smaller problem appeared during development: after deploying a new server
build, some browsers kept executing a cached `app.js` against the new `index.html`.
New panels rendered but stayed blank because the old script did not know their IDs.
FastAPI's `StaticFiles` emits only `ETag`/`Last-Modified` with no `Cache-Control`,
which allows heuristic caching.

## Decision

**Separate regional panel.** Internet-sourced data lives exclusively in a dedicated
panel (`panel-regional`, equipment code `WX-NET · F1`). It contains the wind compass
(a small polar-SVG reusing the same idiom as the sky plot), wind speed/gust/Beaufort,
fused comfort indices (apparent temperature, wind chill, THSW, ET0), cloud cover, UV,
visibility, and a provenance caption (`via <source> · <distance> · <age>`). The
outdoor hero panel (`WX-OUT · A1`) contains only local sensor data, always.

**Treat the feed like an unpluggable sensor.** When `external` is null the regional
panel reuses the existing basement-offline visual language verbatim: `opacity: 0.62`,
a `NO FEED` badge replacing the basement panel's `OFFLINE` badge, and the `NET` status
LED set to off. This makes the offline-first invariant visually testable: disable
`[external]` in config and exactly one panel dims. Nothing else changes.

**Unified adaptive "feels like" in the outdoor hero.** The hero shows a single
"feels" value with a small source tag. When `external` is present and
`apparent_temperature_f` is non-null, the hero shows the wind-aware apparent
temperature tagged `apparent`. When the feed is absent it falls back to the local
heat-index, tagged `heat index`. The swap is explicit; the tag prevents it from
being mysterious.

**Surface everything.** Every derived field the server now emits gets a visible
readout. New panels were added: Derived Thermodynamics (`THERMO · A4`) for extended
local thermodynamics (wet bulb, humidex, VPD, mixing ratio, vapor pressure, air
density, density altitude, cloud base), and Today & Trends (`TODAY · D0`) for
history-derived aggregates (temp high/low, pressure tendency, degree days, DLI,
Hargreaves ET0). Sky estimates from the light sensor are displayed with `EST` chips
to distinguish them from direct measurements.

**Server-side `no-cache` for dashboard assets.** A `_NoCacheStaticFiles` subclass
overrides `StaticFiles.file_response()` to inject `Cache-Control: no-cache` on every
asset served from `/dashboard/`. `no-cache` means "revalidate before use" — browsers
still issue conditional requests (`If-None-Match`) and receive cheap `304 Not
Modified` responses when nothing changed. On a LAN the overhead is negligible, and a
stale-JS-after-deploy can never happen again without any per-deploy action required.

## Alternatives considered

- **Blend wind/feels-like into the outdoor panel** — this was the obvious "fewer
  panels" option. Rejected because it conflates local-sensor and internet/model
  provenance in a single display surface, and because when the feed is offline the
  outdoor panel would show dashes in exactly the fields users check most (comfort
  indices), making it look broken rather than intentionally offline.

- **Local heat-index in the hero always; wind-aware comfort only in the regional
  panel** — avoids the connectivity-dependent shift in the hero number. Rejected in
  favor of the unified adaptive value: a user with the feed enabled should see the
  best available single comfort number in the most prominent position. The source tag
  mitigates the shift.

- **Show only a curated subset of derived fields** — a smaller diff, less panel
  clutter. Rejected by user directive ("surface everything").

- **Version query string on assets (`app.js?v=abc123`)** for the cache bug — requires
  a per-deploy action to bump the version token, which is easy to forget. Rejected in
  favor of the server-side approach that is automatic.

- **`Cache-Control: no-store`** — stronger than needed. `no-store` prevents all
  caching and means every asset load is a full transfer. `no-cache` (revalidate +
  304) is sufficient and preserves bandwidth benefit on slow LAN links.

## Consequences

**What gets easier:**
- The offline-first guarantee is now visually testable: pull the WAN cable (or set
  `enabled = false` in `[external]`) and verify that exactly one panel dims.
- Every new derived field has a place to live with no reshuffling of existing panels.
- The wind compass reuses the polar-SVG idiom already present in the sky panel — no
  new dependencies, no new rendering approach.
- Dashboard assets can never silently run a stale build after a server deploy.

**What gets harder / what this commits us to:**
- Every consumer of `external` data — the dashboard, the GTK widget, any future
  client — must be null-safe against `external` being absent. There is no fallback
  value; the field is either populated or null.
- The hero "feels like" value changes with connectivity. This is intentional and
  tagged, but it means two users on the same station at different network states see
  a different number in the most prominent display position.
- New panels must remain null-safe; adding a field that crashes the render when
  `external` or `summary` is absent breaks the offline guarantee in the UI.
- The GTK widget does not yet display the regional data. That was deferred as a
  follow-up task. Until it is done, the widget shows only the local heat-index
  feels-like regardless of feed state.

## Revisit if

- The GTK widget is updated to consume `external`. At that point the adaptive
  feels-like logic should be extracted into a shared utility rather than duplicated
  between dashboard and widget.
- A local anemometer is added to the outdoor sensor. Wind-chill and apparent
  temperature would move from `external` to `derived`, the regional panel would lose
  its comfort-indices section, and the adaptive hero logic would simplify to
  "always use the local wind-aware value."
- Users find the hero number shifting between `apparent` and `heat index` confusing
  enough to file a bug. In that case the option rejected above (local heat-index
  always in the hero) should be reconsidered.
- `Cache-Control: no-cache` causes measurable page-load slowness on a genuinely
  bandwidth-constrained LAN link (e.g. a very slow Pi Zero WiFi connection under
  load). At that point switching to a versioned asset filename scheme is the right
  trade.
