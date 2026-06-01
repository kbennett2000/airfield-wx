# Dashboard Tour

How to read your instrument. Open `http://<server>:8005/dashboard/`.

> **UNOFFICIAL — NOT FOR FLIGHT PLANNING.** The amber banner at the top of the dashboard says so, on
> purpose, and never goes away. This is backyard sensor data plus nearby public reports — not a
> substitute for an official preflight briefing or current METAR/TAF, and there is **no go/no-go
> automation**. See the [README](../../README.md).

## The one thing to learn first: the provenance colors

Every readout is color-coded by **where the number comes from**:

- **Cyan = LOCAL.** Your own sensors and the values derived from them — density altitude, altimeter
  setting, temperature/dewpoint, wind, the runway solution. These are measured at *your field* and are
  **always live** (they work with no internet).
- **Violet = INTERNET-sourced.** The METAR panel (ceiling, visibility, flight category, the reporting
  station's altimeter). This is conditions *at a nearby reporting station*, not necessarily over your
  strip — so it's **always attributed** with station, distance, and observation age, and it can be
  absent.

**Why it matters — the offline-first behavior:** if the internet feed is gone or stale, **only the
violet elements dim** and show "NO FEED" — the **cyan local panels stay bright and keep updating.**
That asymmetry is deliberate: your field's own density altitude, altimeter, and wind never depend on a
network. (See [ADR-0002](../adr/0002-internet-optional-dashboard-ux.md) /
[ADR-0005](../adr/0005-metar-data-and-unofficial-stance.md).) The top-right **Net Feed** indicator
shows ONLINE / STALE / OFFLINE; the UNOFFICIAL banner is present regardless.

## The panels

**Density Altitude (hero).** The headline performance number, in feet. Below it: pressure altitude,
ISA deviation, and DA above field elevation, with a bar showing where DA sits between field elevation
and 10,000 ft.

**Altimeter.** Altimeter setting (QNH — what you'd dial into the Kollsman window) and station
pressure. The **"vs nearest METAR"** row is the one violet element here: it shows the difference
between your derived setting and the reporting station's altimeter when the feed is up, and dims to
"no feed" when it's not — while QNH and station pressure (local) stay live.

**Wind & Runway.** Wind speed/gust in **knots**, direction in degrees **true** (with the magnetic
equivalent noted). The compass draws your runways at their **magnetic** headings (what's painted), and
the **runway solution** highlights the **favored end** (most headwind) with its **headwind** and
**crosswind** (and which side, L/R). A crosswind over your configured `crosswind_limit_kt` is flagged.
The "var X°E · WMM" note is the magnetic variation used for the true↔magnetic conversion.

> Reading it (hypothetical): suppose a single **09/27** strip, wind **290° true at 12 kt**. The favored
> end is the one you'd land into-wind; the panel shows its headwind and crosswind components so you can
> judge it yourself. **It does not tell you whether to fly** — you decide.

If there's **no anemometer** (`has_wind = false`) or no wind reading, the wind values show `—` but the
runways still draw. If no airport resolves, the panel shows a clean "no field" state.

**Temp / Moisture.** OAT, dewpoint, the spread (cloud-base proxy), and relative humidity.

**Ceiling & Visibility (METAR, violet).** Flight category badge (**VFR** green / **MVFR** blue /
**IFR** red / **LIFR** magenta), the reported sky layers with the ceiling flagged, visibility, and the
station altimeter — all attributed (`via <STATION> · <distance> NM · obs <age> ago`) with the standing
reminder that these are conditions at the reporting station. **Flight category comes only from the
METAR**, never from your own sensors (you have no ceilometer).

**Day / Night.** A sun/moon arc driven by live astronomy (sunrise/sunset, the current sun position),
twilight times (civil / nautical / astronomical), and moon phase. The theme follows actual daytime,
not your clock. Twilight times are universal; **regulatory interpretation (position-light / night
currency) is a regional overlay and is off.**

**Today & Trend.** A recent-history chart of OAT and station pressure from the logged time series. With
little or no history yet, it shows an empty/partial chart — it fills in as the logger accumulates
readings.

## The toggles (top right)

- **Units (IMP/MET).** Flips inHg↔hPa, statute-miles↔km, °F↔°C live (display only — no re-fetch, not
  persisted). **Knots and feet never change** — wind is always in knots and altitudes/elevations
  always in feet, because those are aviation-universal.
- **Net Feed.** A manual override to *simulate* the offline state, so you can see the
  dim-violet-only behavior without unplugging anything.
- **Sky (AUTO/DAY/NIGHT).** Overrides the day/night theme; AUTO follows the astronomy.

---

Next: the **[verification checklist](verification.md)** to confirm your deployment end to end.
