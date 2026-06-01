# Verification Checklist

End-to-end "did my deployment work?" — from server bring-up to the dashboard's offline-first behavior.
Run it once after install and after any config change.

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
