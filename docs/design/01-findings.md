# Weather Station — Code Review Findings

Repository: `kbennett2000/weather-station-public`
Review date: 2026-05-22

Each finding has a stable ID (`CATEGORY-NN`) so it can be referenced individually in follow-up discussion. No remediation guidance is included; this document is descriptive only.

**Categories**

- `SEC` — Security
- `BUG` — Bugs and correctness defects
- `ARCH` — Architecture and design
- `PERF` — Performance
- `QUAL` — Code quality and maintainability
- `DOC` — Documentation accuracy
- `MIN` — Minor / cosmetic

---

## Decisions log

**2026-05-22 — Remove all forecasting features.** The "Forecast & Analysis" and "My Forecast" tabs, `weatherAnalysis.js`, the `predictConditions` function, the `SharedComponents` block, and any related UI will be deleted. Findings tied exclusively to forecasting code are marked **REMOVED** below and excluded from the active count.

Items closed by this decision: **BUG-09, BUG-10, BUG-11, BUG-17, DOC-01**.

**2026-05-22 — Adopt unified server-side API (see `weather-station-api-design.md`).** All derived weather/astronomy values move to a single read-only HTTP API on the server. Dashboard and tray become thin consumers. The proxy SSRF mechanism is removed.

**2026-05-22 — Storage & data scope:**
- Storage engine: **SQLite, WAL mode** (replaces MariaDB). See `server/weather_server/db.py`.
- Migration: **greenfield, no import** from existing tables.
- Logged data: **outdoor sensor only**. Indoor and basement sensors remain live-only (current readings via API poll, no history retained).

This reclassifies **BUG-12** from a defect to an intentional design choice — the basement (and indoor) sensors are live-only by design rather than incompletely implemented. The associated README inaccuracy still needs to be fixed; tracking that under the BUG-12 entry going forward.

**2026-05-22 — Server process model.** Single FastAPI process with two internal async tasks: a background outdoor-logging loop and on-demand polling of indoor/basement sensors from request handlers (with a short TTL cache to deduplicate concurrent fetches). One systemd unit, one log file. See `server/weather_server/main.py` for the implementation.

> **Update (post-external-feed):** the model now has **three** concurrency mechanisms — the outdoor logger task, the **optional** external-feed background task (spawned but exits immediately when `[external]` is disabled), and the on-demand indoor/basement polling. See ADR-0001.

**2026-05-22 — Dashboard and widget scope.** See `weather-station-clients-scope.md`. Key points:
- Dashboard moves to a configurable port, default **8005**. The iptables 80→8000 redirect is removed; port 80 is freed for a future menu/landing service.
- Dashboard visual direction: instrument-panel aesthetic (dark theme, big readouts, accent glow, sky polar plot for sun/moon).
- All fields currently in the tray widget are added to the dashboard.
- Title "Jones Big Ass Weather Dashboard" and subtitle "Collectin' Some Good Ass Weather Data!" are preserved as prominent elements. Specific layout slots are reserved for project-specific branding/jokes.
- Widget UI is preserved as-is; only its data plumbing changes (now reads `/api/v1/current` instead of polling the sensor directly).

---

## Security (SEC)

### SEC-01 — Server-Side Request Forgery in proxy

**Location:** `weatherProxy.py`, `do_GET`, the block guarded by `if parsed_url.query:` (~lines 71–87)

The proxy accepts a client-supplied URL via the `?url=` query parameter and fetches it server-side with no validation, no allowlist, and no scheme restriction:

```python
url = query.get('url', [None])[0]
if url:
    response = requests.get(url, timeout=5)
    ...
    self.wfile.write(response.content)
```

Anything that can reach the proxy can use it to issue arbitrary outbound HTTP requests from the host. This includes LAN scanning, fetching internal admin interfaces, hitting cloud metadata endpoints (e.g. `169.254.169.254`) if the host ever runs in a cloud VM, and using the host as an open relay if it is exposed beyond the LAN.

### SEC-02 — Path traversal in static JS handler

**Location:** `weatherProxy.py`, `do_GET`, the `.js` branch (~lines 42–48)

```python
if self.path.endswith('.js'):
    ...
    with open(self.path.lstrip('/'), 'rb') as f:
        self.wfile.write(f.read())
```

`lstrip('/')` removes leading slashes but does not normalize `..` segments. A request for `/../../etc/something.js` becomes `../../etc/something.js`, which `open()` resolves relative to the process working directory. The `.js` suffix limits what files can be exfiltrated but does not eliminate the traversal.

### SEC-03 — Client-supplied URL validation is client-side only

**Location:** `dashboard.html`, `getData()` (~lines 1064–1070)

```javascript
if (!url.includes("192.168.1.")) { ... }
```

This check exists in the browser only. The proxy itself (see SEC-01) imposes no equivalent check. An attacker does not need to use the dashboard to invoke the proxy.

### SEC-04 — `/setOffset` endpoint on ESP32 has no authentication

**Location:** `sketches/jonesBigAssWeatherStation_FreeRTOS_outdoor.ino`, `handleSetOffset` (~lines 121–139)

Any client able to reach the ESP32's HTTP server on the LAN can rewrite `TEMP_OFFSET` via a single GET to `/setOffset?value=...`. There is no token, IP allowlist, or any other access control.

### SEC-05 — Hardcoded database password in source and install script

**Location:**
- `weatherProxy.py` (~line 22)
- `weatherLogger_Indoor.py` (~line 18)
- `weatherLogger_Outdoor.py` (~line 20)
- `installScriptUbuntu.sh` (~line 23)

The MariaDB user `weatheruser` is created in the install script with the literal password `password`, and the same string appears in three Python files. The install script does not generate or prompt for a credential.

### SEC-06 — Wide-open CORS header on proxy

**Location:** `weatherProxy.py`, `ProxyHandler.end_headers` (~lines 36–38)

```python
self.send_header('Access-Control-Allow-Origin', '*')
```

Every response, including the SSRF endpoint (SEC-01), is annotated with `Access-Control-Allow-Origin: *`. This removes the normal same-origin protection that would otherwise prevent third-party websites visited by a user's browser from calling the proxy.

### SEC-07 — Install script opens host ports through UFW

**Location:** `installScriptUbuntu.sh` (~lines 111–115)

```bash
sudo ufw allow 80
sudo ufw allow 8000
```

Both the iptables-redirected port 80 and the proxy's bound port 8000 are opened on all interfaces by default. Combined with SEC-01 and SEC-06, this widens the SSRF blast radius to anything that can route to the host.

### SEC-08 — Plaintext HTTP throughout

**Location:** All sensor endpoints, the proxy, the dashboard, and the tray fetcher.

There is no TLS anywhere. WiFi credentials, sensor data, and HTTP traffic are exposed to anyone on the LAN segment.

---

## Bugs and correctness (BUG)

### BUG-01 — `hadError` is undeclared

**Location:** `dashboard.html`, `updateData()` (~lines 1183, 1192, 1201)

```javascript
hadError = true;
```

The identifier `hadError` is assigned in three places, declared nowhere, and read nowhere. In non-strict mode it becomes an implicit global; in strict mode it would throw `ReferenceError`. It is effectively dead code, suggesting an incomplete error-summary feature.

### BUG-02 — Duplicate script tags in dashboard

**Location:** `dashboard.html` (~lines 14–24)

`react.development.js`, `react-dom.development.js`, `lodash.min.js`, `papaparse.min.js`, and `babel.min.js` are each listed twice in the `<head>`.

### BUG-03 — `tensorflow.min.js` is loaded but never used

**Location:** `dashboard.html` line 10; library file `js/tensorflow.min.js` (~1.5 MB)

A grep of `dashboard.html` and `weatherAnalysis.js` finds no reference to `tf`, `tensorflow`, or any TF API. The script tag is the only mention.

### BUG-04 — React development builds shipped in production

**Location:** `js/react.development.js`, `js/react-dom.development.js`; loaded by `dashboard.html`

The files are React 17.0.2 *development* builds (per the license header), which contain extensive warning paths and are not optimized. The production builds (`react.production.min.js`, `react-dom.production.min.js`) are absent from the `js/` directory.

### BUG-05 — `TEMP_OFFSET` read without its mutex

**Location:** `sketches/jonesBigAssWeatherStation_FreeRTOS_outdoor.ino`, `handleData()` (~line 322)

```cpp
json += "\"tempOffset\":" + String(TEMP_OFFSET) + ",";
```

Every other access to `TEMP_OFFSET` (read in `sensorTask`, write in `handleSetOffset`) is guarded by `tempOffsetMutex`. This read is not. On 32-bit ESP32 a float load is typically atomic, so a torn value is unlikely, but it is a formal data race.

### BUG-06 — `TEMP_OFFSET` is not persisted

**Location:** `sketches/jonesBigAssWeatherStation_FreeRTOS_outdoor.ino` (~line 50)

```cpp
float TEMP_OFFSET = 0;
```

`TEMP_OFFSET` is a plain RAM variable. It is reset to `0` on every boot. The sketch contains multiple paths that reboot the device (watchdog reset, `ESP.restart()` on WiFi failure), so user calibration is lost on each such event.

### BUG-07 — Partial-success state in sensor task

**Location:** `sketches/jonesBigAssWeatherStation_FreeRTOS_outdoor.ino`, `sensorTask` (~lines 369–419)

A single `bool success` flag tracks both the BME280 and TSL2591 reads. If BME succeeds but TSL fails:

- `sensorData.temperatureC`, `humidity`, and `pressure` are written with fresh values.
- `sensorData.validData` is set to `false`.

The next `handleData()` call returns the error JSON, hiding the valid BME data behind a TSL failure.

### BUG-08 — `nan` text emitted by ESP32 then patched downstream

**Location:**
- Source: `sketches/jonesBigAssWeatherStation_FreeRTOS_outdoor.ino`, `handleData()` (~lines 306–325)
- Workaround 1: `weatherLogger_Outdoor.py`, `get_weather_data` (~lines 40–61)
- Workaround 2: `dashboard.html`, `getData` (~lines 1093–1096)

`String(float)` on an Arduino prints `nan` for NaN values, producing invalid JSON. Two separate consumers (the Python logger and the browser) carry regex-based workarounds to convert `nan` (and `undefined`) tokens to `null` before parsing. The root cause is not addressed at the source.

### ~~BUG-09 — Forecast is a seasonal naive baseline, mislabeled as a forecast engine~~ **[REMOVED — forecasting feature deleted]**

**Location:** `dashboard.html`, `predictConditions` (~lines 514–550); README "Features" section

The function takes readings within ±7 minutes of the target time-of-day from the prior N days and returns a weighted average:

```javascript
return Math.abs(readingMinutes - targetHourMinute) <= 7;
...
const weight = readingDate > recentCutoff ? 1 + extraWeight / 100 : 1;
```

It does not use pressure trend, dew point, current momentum, or the outputs of `weatherAnalysis.js`. The README markets this as a "custom forecast engine that predicts the next 12 hours" — the implementation is a textbook persistence/seasonal baseline.

### ~~BUG-10 — Insufficient-data threshold is too low for analysis~~ **[REMOVED — forecasting feature deleted]**

**Location:** `weatherAnalysis.js`, `loadAndAnalyzeData` (~line 116)

```javascript
if (validData.length < 5) { ... }
```

Five samples is below the slice sizes used downstream:

- `pressureTrends.longTerm` slices `[-60]`
- `analyzeTempHumidity` slices `[-30]`

Any reading count between 5 and the slice size will compute and display metrics derived from too few data points without warning.

### ~~BUG-11 — Correlation coefficient rendered as a percentage~~ **[REMOVED — forecasting feature deleted]**

**Location:** `weatherAnalysis.js`, `getStabilityIndexPercentage` (~lines 231–233)

```javascript
return (-index * 100).toFixed(2) + "%";
```

`index` is a Pearson correlation coefficient (range −1 to 1). The function multiplies by 100 and appends a `%` sign, presenting a unitless coefficient as a percentage. A correlation of `-0.4` is rendered as `"40.00%"`.

### BUG-12 — Basement sensor is half-integrated

**Location:**
- Read path: `dashboard.html` (~line 1197)
- Missing: logger script, MySQL table, schema entry in install script, history chart

The dashboard fetches `http://192.168.1.63/data` for live display. There is no `weatherLogger_Basement.py`, no `basement_weather` table in the install script's schema DDL, and no historical chart for it. The README claims basement sensors are supported "out of the box."

### BUG-13 — Proxy startup message contradicts the bound port

**Location:** `weatherProxy.py`, `__main__` block (~lines 213–216)

```python
server = HTTPServer(('0.0.0.0', 8000), ProxyHandler)
print(f"Server running on http://0.0.0.0:80")
```

Server binds to `8000`; the startup message prints `80`.

### BUG-14 — JS static-file handler can crash mid-response

**Location:** `weatherProxy.py`, `do_GET`, `.js` branch (~lines 42–48)

The code calls `send_response(200)` and `end_headers()` before opening the file. If `open()` raises (file missing, permission denied, etc.), headers have already been sent and the exception propagates uncaught, terminating the request handler with a half-sent response.

### BUG-15 — Single-threaded HTTPServer

**Location:** `weatherProxy.py`, `__main__` block (~line 215)

`HTTPServer` processes requests sequentially. A slow CSV query (multi-thousand-row outdoor data) blocks all other handlers, including static file requests and the live sensor proxy.

### BUG-16 — Bare `except:` clauses

**Location:**
- `weather_tray.py`, `fetch_data` (~line 692)
- `weather_tray.py`, `get_timezone` (~line 710)
- `weatherLogger_Outdoor.py`, inner JSON parse fallback (~line 59)
- `weatherAnalysis.js` (line 952 in tray)

Bare `except:` catches `KeyboardInterrupt` and `SystemExit` alongside ordinary exceptions, complicating graceful shutdown and masking unexpected errors.

### ~~BUG-17 — Tailwind classes used without Tailwind loaded~~ **[REMOVED — forecasting feature deleted; affected code lives in the deleted tabs]**

**Location:** `weatherAnalysis.js`, `getPatternColor`, `SharedComponents` in `dashboard.html` (~line 409)

```javascript
case "rapid-rise": return "text-red-600";
Card: ... `bg-white p-4 rounded-lg shadow ${className}`
```

These are Tailwind utility class names. No Tailwind stylesheet is loaded by `dashboard.html`. The classes are inert.

### BUG-18 — Static IP configured after `WiFi.begin()`

**Location:** `sketches/jonesBigAssWeatherStation_FreeRTOS_outdoor.ino`, `setup()` (~lines 659–660) and `checkWiFiConnection()` (~lines 98–99)

```cpp
WiFi.begin(ssid, password);
WiFi.config(ip, gateway, subnet, dns);
```

`WiFi.config()` is called after `WiFi.begin()`. The conventional order is the reverse so that DHCP is never initiated.

### BUG-19 — `pause` is not a bash builtin

**Location:** `installScriptUbuntu.sh` (~lines 31, 36)

```bash
echo "********** IT MIGHT BREAK HERE **********"
pause
```

`pause` is a DOS/CMD command. Bash will report "command not found" and continue executing.

### BUG-21 — Dashboard and widget show different inHg values for the same hPa reading

**Location:**
- Dashboard: `dashboard.html`, `formatPressure()` (~lines 764–768), invoked at ~lines 1240, 1268, 1281
- Widget: `weather_tray.py`, `pressure_inhg` calculation (~line 765), displayed at ~line 873
- Dashboard helpers: `hPaToSeaLevel`, `hPaToInHg` (~line 761 area)

For the same input reading, the two clients display different inHg values:

- Widget: 804.43 hPa → **23.75 inHg**
- Dashboard: 804.4 hPa → **30.21 inHg**

Root cause is that the two consumers compute *different physical quantities* under the same UI label "Pressure (inHg)":

- **Widget** converts the raw reading (station pressure, at the sensor's altitude) directly to inHg via `hPa × 0.02953`. Mathematically correct unit conversion of station pressure.
- **Dashboard** first applies a sea-level adjustment using the barometric formula (`hPaToSeaLevel`), then converts the *adjusted* value to inHg. This matches what NWS reports because barometric trends are only comparable across altitudes when normalized to sea level.

At Denver-area elevation (~1600 m), the difference is approximately 6.5 inHg — large enough that a user comparing against weather.gov would conclude their sensor is broken.

**Secondary defect:** `formatPressure` (dashboard, line 765) passes a hardcoded elevation of `1972` to `hPaToSeaLevel`, ignoring the GPS-reported altitude in `outdoor.altitude`. For installations at any other elevation, the sea-level adjustment is wrong.

**Tertiary defect:** the dashboard's pressure display string is `${pressure} hPa (${inHg} inHg)` where the hPa value is station pressure but the inHg value is sea-level pressure. Two different physical quantities under the same "Pressure" label, side-by-side in the same string.

### BUG-20 — Install script lacks `set -e`

**Location:** `installScriptUbuntu.sh` (whole file)

The script does not enable error exit. Failures in the `apt`, `nmcli`, `mysql`, `pip`, or `systemctl` steps do not halt execution; subsequent steps run regardless.

---

## Architecture and design (ARCH)

### ARCH-01 — Single wide schema mixes weather, GPS, and device telemetry

**Location:** `installScriptUbuntu.sh`, DDL for `outdoor_weather` (~line 23)

The table has 18 non-key columns:

- Weather: `temperatureC`, `temperatureF`, `humidity`, `pressure`, `lux`, `ir`, `visible`, `full`
- GPS (effectively static): `latitude`, `longitude`, `altitude`, `speed`, `course`, `satellites`
- Device telemetry: `tempOffset`, `rssi`, `uptime`, `freeHeap`

Every poll writes a full row including columns that change rarely or never.

### ARCH-02 — No connection pooling

**Location:** `weatherProxy.py`, `connect_to_database` (~line 26)

Every CSV request opens a new MariaDB connection and closes it in `finally`. There is no `mysql.connector.pooling.MySQLConnectionPool`.

### ARCH-03 — Unbounded result sets sent to the browser

**Location:** `weatherProxy.py`, `send_indoor_data` and `send_outdoor_data` (~lines 92, 142)

```sql
SELECT * FROM outdoor_weather WHERE timestamp >= %s ORDER BY timestamp
```

The query has no `LIMIT`, no aggregation, no time-bucketing. A 7-day window at one row per minute returns ~10,000 rows, all of which are CSV-encoded and sent to the browser to be parsed by Papa.parse and plotted by Chart.js.

### ARCH-04 — Proxy handler mixes three responsibilities

**Location:** `weatherProxy.py`, `do_GET` (~lines 40–90)

One method handles:
- Static JS file serving with a custom code path
- Root-path serving of `dashboard.html`
- CSV endpoints backed by MySQL
- Arbitrary URL proxying

The branching is positional and not clearly delineated.

### ARCH-05 — Live data and historical data fetched on independent paths with no freshness reconciliation

**Location:** `dashboard.html` overall structure

Current readings come from `getData("http://192.168.1.<n>/data")` (live sensor poll, via SSRF endpoint).
Historical readings come from `fetch("/weather_data_outdoor.csv?hours=...")` (MySQL).

If the logger process crashes, live values continue to display from the sensor while the database stops receiving new rows. There is no UI signal that historical data has gone stale.

### ARCH-06 — Reverse-proxy roundtrip from browser to sensor via host

**Location:** `dashboard.html` `getData()` (~lines 1064–1112) and `weatherProxy.py` (~lines 71–87)

The browser, which is already on the LAN, fetches `/?url=http://192.168.1.60/data` instead of contacting the sensor directly. The proxy hop exists to make the dashboard a single-origin app, but is the same mechanism flagged in SEC-01.

### ARCH-07 — WiFi credentials hardcoded in sketch source

**Location:** `sketches/jonesBigAssWeatherStation_FreeRTOS_outdoor.ino` (~lines 32–33) and the indoor sketches

```cpp
const char *ssid = "NetworkName";
const char *password = "NetworkPassword";
```

Credentials live in source. There is no captive portal, NVS provisioning, or WiFiManager fallback. Reflashing is required to change network.

### ARCH-08 — No retention or archival policy

The MySQL tables grow without bound. There is no rollup, no partitioning, no `DELETE` schedule, no documentation of expected disk-use over time.

---

## Performance (PERF)

### PERF-01 — 1.5 MB of unused JS on every page load

**Location:** `dashboard.html` line 10 (`tensorflow.min.js`)

See BUG-03; the file is fetched, parsed, and held in memory with no consumer.

### PERF-02 — In-browser Babel transpilation

**Location:** `dashboard.html`, multiple `<script type="text/babel">` blocks; `js/babel.min.js` (~2.9 MB)

JSX in `weatherAnalysis.js` and inline blocks is transpiled by Babel on every page load.

### PERF-03 — Five script tags loaded twice

See BUG-02. Browser cache mitigates network impact, but the parse work is still performed.

### PERF-04 — Chart.js plots full-resolution time series

The dashboard renders every row returned by the CSV endpoints (see ARCH-03) without downsampling, even for views as long as 7 days.

---

## Code quality (QUAL)

### QUAL-01 — `weather_tray.py` is over-commented

**Location:** `weather_tray.py` throughout

Most lines carry an inline comment restating the line in English, e.g.:

```python
import math
# Imports the math module because we need sin(), cos(), tan(), asin(), etc. for the SunCalc astronomy formulas.
```

The comment density obscures the code rather than clarifying it. The file is 961 lines for what is structurally a small GTK application plus a SunCalc port.

### QUAL-02 — Install script is environment-specific

**Location:** `installScriptUbuntu.sh`

Hardcoded values that will not match an arbitrary user's machine:
- Network interface `netplan-enp0s3`
- Static IP `192.168.1.62`
- Linux username `kb` in three systemd unit files (`User=kb`, `ExecStart=/home/kb/...`)
- `pause` command (BUG-19)
- Final `sudo reboot` with no warning prompt

### QUAL-03 — Inconsistent error handling across modules

- `weatherProxy.py` returns 500 on DB errors with minimal logging.
- `weather_tray.py` uses bare `except:` and collapses all failure modes into "DEVICE OFFLINE."
- Outdoor logger has structured retry logic; indoor logger does not (no `Retry` adapter, no session).

### QUAL-04 — Two indoor sketches plus two legacy sketches, no documentation

**Location:** `sketches/` directory

```
jonesBigAssWeatherStation_FreeRTOS_indoor_jr.ino
jonesBigAssWeatherStation_FreeRTOS_indoor_main.ino
jonesBigAssWeatherStation_FreeRTOS_outdoor.ino
jonesBigAssWeatherStation_indoor.ino           ← non-FreeRTOS
jonesBigAssWeatherStation_outdoor.ino          ← non-FreeRTOS
```

The README does not explain the relationship, deprecation status, or which to use.

### QUAL-05 — Inline HTML and CSS embedded in ESP32 sketch

**Location:** `sketches/jonesBigAssWeatherStation_FreeRTOS_outdoor.ino`, `handleRoot()` (~lines 141–296)

A 150-line raw-string HTML page lives in the sketch. Edits require a recompile and reflash.

### QUAL-06 — Mixed JavaScript style

**Location:** `dashboard.html`

Mix of `var`, `let`, and `const`; mix of named functions and arrow functions for similar roles; mix of `==` and `===`.

### QUAL-07 — Commented-out code left in dashboard

**Location:** `dashboard.html` (~lines 985–988, 1205–1207)

Several blocks of dead code commented out, with no marker for whether they are draft, deprecated, or intentional alternates.

### QUAL-08 — Unstable JSON construction via string concatenation

**Location:** `sketches/jonesBigAssWeatherStation_FreeRTOS_outdoor.ino`, `handleData` (~lines 306–331)

JSON is built by `String` concatenation of float-to-string conversions, which is the source of BUG-08 and is fragile to any future numeric edge case.

### QUAL-09 — No dependency pinning

**Location:** `installScriptUbuntu.sh` (~line 33)

```bash
pip install mysql-connector-python requests
```

No version pins, no `requirements.txt`.

### QUAL-10 — `sudo apt update` called twice

**Location:** `installScriptUbuntu.sh` lines 1 and 4

---

## Documentation (DOC)

### ~~DOC-01 — README overstates the forecast engine~~ **[REMOVED — forecasting feature deleted; README section will be rewritten]**

**Location:** `README.md`, Features section, "Custom forecast engine that predicts the next 12 hours…"

The implementation (see BUG-09) is a same-time-of-day weighted average, not a predictive model.

### DOC-02 — README overstates basement sensor support

**Location:** `README.md`, Features section, "Multi-sensor capable — supports indoor, outdoor, and additional indoor (e.g. basement) sensors out of the box"

The basement sensor is only displayed live (see BUG-12).

### DOC-03 — BME280 pin table omits I²C address rationale

**Location:** `README.md`, "Pin connections" → BME280

The table specifies `SDO → ESP32 GND`. This selects I²C address `0x76`, which is what the sketch calls `bme.begin(0x76)`. The relationship is not explained.

### DOC-04 — No mention of which sketch is current

**Location:** `README.md`, "What's in this repo" section

The table lists `sketches/` as "ESP32 Arduino sketches (one per sensor type)" but does not enumerate the files or indicate which are current vs legacy (see QUAL-04).

### DOC-05 — No data-retention or disk-use guidance

The README does not state the polling frequency, expected row growth, or disk-use over time. A user deploying on a Pi Zero 2 W has no guidance on when storage will fill (see ARCH-08).

---

## Minor / cosmetic (MIN)

### MIN-01 — Tray opens dashboard host, not the sensor it reads

**Location:** `weather_tray.py`, `on_details_clicked` (~line 929)

The tray fetches data from `192.168.1.60` (sensor) but the "details" menu item opens `http://192.168.1.62` (dashboard host). Both IPs are hardcoded in different places in the same file.

### MIN-02 — GMT offset hardcoded in sketches despite GPS being present

**Location:** `sketches/jonesBigAssWeatherStation_FreeRTOS_outdoor.ino` (~line 35)

```cpp
const long gmtOffset_sec = -25200;
```

Mountain Time is hardcoded. The outdoor unit has a GPS providing live latitude/longitude; the tray already derives timezone from coordinates using `timezonefinder`. The ESP32 does not.

### MIN-03 — iptables PREROUTING redirect for a server that could just bind 80

**Location:** `installScriptUbuntu.sh` (~line 40)

```bash
sudo iptables -t nat -A PREROUTING -i eth0 -p tcp --dport 80 -j REDIRECT --to-port 8000
```

The proxy binds 8000 because non-root processes cannot bind 80; an iptables rule then redirects 80 → 8000. The combination of `setcap` or a reverse proxy would avoid the indirection. No comment in the file explains the choice.

### MIN-04 — `installScriptUbuntu.sh` has no shebang

**Location:** `installScriptUbuntu.sh` line 1

The file begins directly with `sudo apt update`. There is no `#!/usr/bin/env bash` (or similar).

### MIN-05 — README references an unstated location for the dashboard URL

**Location:** `README.md`, Quick start step 4: "Point your browser at the collector machine and the dashboard will load."

No port or path is given. A new user does not know whether to visit `http://<host>/`, `http://<host>:8000/`, or `http://<host>/dashboard.html`.

### MIN-06 — Whitespace and CRLF inconsistencies

Files in the repo use mixed CRLF (Windows) and LF (Unix) line endings (visible in `dashboard.html`, `weatherAnalysis.js`).

### MIN-07 — Service unit `User=kb` will break for any other user

**Location:** `installScriptUbuntu.sh` (~lines 60–110)

Three systemd unit files are written with `User=kb` and `ExecStart=/home/kb/...`. This is also recorded under QUAL-02 but is significant enough operationally to flag separately.

---

## Summary count

| Category | Original | Added | Removed | Active |
|---|---|---|---|---|
| SEC  | 8  | 0 | 0 | 8  |
| BUG  | 20 | 1 | 4 | 17 |
| ARCH | 8  | 0 | 0 | 8  |
| PERF | 4  | 0 | 0 | 4  |
| QUAL | 10 | 0 | 0 | 10 |
| DOC  | 5  | 0 | 1 | 4  |
| MIN  | 7  | 0 | 0 | 7  |
| **Total** | **62** | **1** | **5** | **58** |
