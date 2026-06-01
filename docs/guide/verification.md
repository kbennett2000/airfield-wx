# Verification Checklist

Here's how you know it's working — a friendly walk from "the server came up" to "the dashboard behaves
the way it should." Nothing here is a test you can fail; it's a tour of what *good* looks like, so you
can spot anything that isn't. Run through it once after install, and again after any config change. New
to a term? The [glossary](glossary.md) has you covered.

> **UNOFFICIAL — NOT FOR FLIGHT PLANNING.** See the [README](../../README.md).

## 0. Tests (developers)

```bash
make check              # ruff + mypy + the fast unit suite (must be green)
make test-integration   # the real-HTTP/timing integration tests (run explicitly)
```

`make check` is the gate for code changes; the integration suite spins up a fake ESP32 over real HTTP
and is kept separate so the default suite stays fast and deterministic.

## 1. Backend bring-up

- [ ] **Server starts.** systemd: `systemctl status weather-server.service` is *active*; or `make dev`
  prints `Application startup complete` with no traceback. Logs:
  `journalctl -u weather-server.service -f`.
- [ ] **DB migrates / opens.** The log shows the db opening at its schema version; no "schema version
  too new" error. (Upgrades migrate forward automatically.)
- [ ] **Health:** `curl -s http://<server>:8005/api/v1/health` → `"ok": true`, `db_reachable: true`.
- [ ] **Current populates:** `curl -s http://<server>:8005/api/v1/current` returns JSON with a
  `sensors.outdoor` block. In **demo mode** the outdoor temp is ≈ 64.2 °F; with **real sensors** it's
  your actual reading (and `sensors.outdoor.online` is `true`).
- [ ] **Airport resolves** (if GPS/fallback coords are set): `airport.ident` is non-null and
  `airport.runways` is populated (auto-resolved from the bundled OurAirports data, offline).

## 2. Dashboard — local panels (cyan, always live)

Open `http://<server>:8005/dashboard/`.

- [ ] The **UNOFFICIAL — NOT FOR FLIGHT PLANNING** banner is visible and cannot be dismissed.
- [ ] **Density altitude** hero shows a number (ft); pressure altitude populated.
- [ ] **Altimeter** QNH + station pressure populated.
- [ ] **Temp / Moisture**: OAT, dewpoint, spread, RH populated.
- [ ] **Header**: Zulu clock ticks; station id + field elevation shown; the Outdoor / GPS status LEDs
  reflect reality.
- [ ] **No console errors** (browser dev tools); narrow the window — the grid collapses responsively.

## 3. Wind & runway solution

- [ ] With an **anemometer present and wind blowing**: wind speed/gust in **knots**, direction in °T
  with the magnetic note; the compass draws runways at magnetic headings; the **favored runway** is
  highlighted with **headwind / crosswind** components; a crosswind over `crosswind_limit_kt` is
  flagged.
- [ ] With **no anemometer / no wind** (`has_wind = false` or calm with no reading): the wind values
  show `—` and there's no favored highlight, **but the runways still draw**. Nothing errors.

### Separate wind station (topology 3 — only if `[wind] source` names a station)

- [ ] **It logs.** With `wind_station.ino` flashed and `[wind] source = "<id>"`, the station's wind
  rows accumulate: `curl -s http://<server>:8005/api/v1/health` shows it (or check the DB:
  `sqlite3 weather.db 'SELECT COUNT(*) FROM wind_readings'` climbs).
- [ ] **Wind resolves from it.** `/api/v1/current` shows `sensors.outdoor.derived.wind_direction_true_deg`
  and `airport.runway_solution` populated **even though the outdoor unit has no anemometer**
  (`has_wind = false`). The wind station has no `/current/<id>` view — it's a data source, not a display
  sensor (a GET returns 404).
- [ ] **Stale ⇒ nulled.** If the wind station stops reporting for longer than `[wind] max_age_s`, the
  wind values and the favored runway null out (a dead anemometer never reads as current).

## 4. METAR feed (violet) — only if `[external]` is enabled

With `provider = "metar"` (and internet):

- [ ] The **Ceiling & Visibility** panel populates: flight-category badge (correctly colored),
  sky layers with the ceiling flagged, visibility, and the station altimeter.
- [ ] It is **attributed**: `via <STATION> · <distance> NM · obs <age> ago`, with the "conditions AT
  the reporting station" reminder.
- [ ] The **altimeter cross-check** (Δ vs your local setting) shows in the Altimeter panel.

## 5. The offline-first asymmetry (the key check)

- [ ] **Disable the feed** — either set `[external].enabled = false` (restart) **or** click the
  dashboard's **Net Feed** toggle to OFFLINE.
- [ ] The **METAR panel dims and shows "NO FEED"**, and the altimeter cross-check dims to "no feed".
- [ ] **At the same time, every cyan local panel stays bright and keeps updating** — density altitude,
  altimeter QNH + station pressure, wind, runway, temp. The Net-Feed LED goes off; the UNOFFICIAL
  banner stays.

If the local panels dim when the feed drops, something is wrong — local data must never depend on the
network.

## 6. Interaction

- [ ] **Units** toggle flips inHg↔hPa, SM↔km, °F↔°C (value **and** label); **knots and feet stay
  fixed** (wind, density altitude, elevations unchanged). The trend chart axes flip too.
- [ ] **Day/Night** theme + arc reflect actual daytime (the **Sky** toggle overrides); twilight times +
  moon phase populated.
- [ ] **Trend** chart draws (OAT + station pressure); with little history it shows an empty/partial
  chart rather than crashing.

---

If every box is checked, your airfield-wx deployment is working. Re-read the
**[dashboard tour](dashboard.md)** for how to interpret each readout — and remember it's
**unofficial**.
