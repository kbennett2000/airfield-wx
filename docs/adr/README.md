# Architecture Decision Records

The *why* behind airfield-wx's load-bearing choices. Each ADR captures context, the decision, and its
consequences. ADRs 0001–0002 carry over from the base `weather-station-public`; 0003–0005 are the
aviation additions.

| ADR | Decision |
|---|---|
| [0001](0001-optional-internet-external-data-feed.md) | The internet feed is **optional** — regional conditions come from a pluggable external provider, and the server is fully functional offline. |
| [0002](0002-internet-optional-dashboard-ux.md) | The dashboard is **offline-first in the UX**: internet-sourced panels dim / show "no feed" when the feed is absent, while local panels stay live. |
| [0003](0003-local-anemometer-wind-first-class.md) | Wind is a **first-class local reading** from an on-site anemometer (not a model point); the wind-fused comfort indices move to `derived`. |
| [0004](0004-offline-gps-location-intelligence.md) | **Offline, GPS-driven location intelligence**: nearest airport + runways from bundled OurAirports, magnetic variation from bundled WMM2025. |
| [0005](0005-metar-data-and-unofficial-stance.md) | **METAR** supplies ceiling/visibility/flight-category (always attributed); the product is **unofficial — not for flight planning**, with no go/no-go automation. |

For the full design intent, see [`docs/design/`](../design/).
