# Weather Station API — Proposed Surface

Status: implemented (amended 2026-05-30 to add the optional external feed, the summary endpoint, and the extended derived/astronomy fields — see the **Post-launch additions** sections marked below)
Date: 2026-05-22
Scope: HTTP API exposed by the (renamed) weather server to all consumers — dashboard, tray, and anything else that wants to consume weather data.

---

## Design principles

1. **One source of truth.** All consumers get identical numbers because they all call the same API. No duplicated SunCalc, no per-client dew point math.
2. **Read-only consumer API.** Calibration, sensor registration, and similar admin actions are out of scope here. They live in a separate config mechanism (file, CLI, or admin-only endpoint added later).
3. **Single-round-trip responses.** A consumer should never have to make two calls to render a view. `/api/current` returns everything needed for "right now."
4. **Provenance is explicit.** Every field in a response is one of six clearly defined types (see below). Knowing the provenance tells you how stale a value can be and what it depends on.
5. **Versioned from day one.** All paths under `/api/v1/`. Breaking changes go to `/api/v2/`.
6. **Plain JSON, no streaming, no auth.** This is a LAN device. If remote access is added later, that becomes a reverse-proxy concern, not an API concern.
7. **Disambiguate physical quantities by field name.** Where the same underlying SI quantity can be presented as different "kinds" (e.g. station pressure vs. sea-level pressure), each kind gets its own field with a self-describing name. Consumers pick which one to display; the API never offers a single ambiguous field. This rule is the direct response to BUG-21, where dashboard and tray showed wildly different inHg values under the same UI label.

---

## Field provenance taxonomy

Every field returned by the API falls into one of these eight categories. The categorization is documented per-field in the schema tables below.

| Tag | Meaning | Stale after |
|---|---|---|
| `RAW` | Direct sensor reading, unmodified | When sensor reports next reading |
| `CALIBRATED` | Raw value with calibration applied (e.g. temp offset) | Same as RAW |
| `D-READING` | Derived from a single reading (dew point, °F conversion, sea-level pressure) | Same as RAW — fixed for the lifetime of that reading |
| `D-LOCATION` | Derived from GPS coords (Maidenhead grid, DMS, altitude in ft) | When GPS changes (essentially never for a fixed station) |
| `D-TIME` | Derived from current clock time + location (sun position, time to sunset, moon phase) | Within seconds — must be computed fresh on every request |
| `META` | Server-generated bookkeeping (timestamps, IDs, freshness, online flag) | N/A |
| `EXTERNAL` *(post-launch)* | Internet-sourced regional data (wind, cloud, etc.) and anything fused from it (wind chill, ET₀). **OPTIONAL** — absent/`null` when the feed is disabled or offline | `refresh_interval_seconds` (default 300s); carries `age_seconds`/`stale` |
| `D-HISTORY` *(post-launch)* | Derived by aggregating the logged time series (today hi/lo, pressure tendency, degree days, DLI) | When the window advances / a new reading is logged |

The important distinction is `D-READING` vs `D-TIME`. Reading-bound derivations are snapshots: the dew point at 3:00 PM yesterday is fixed forever. Time-bound derivations are functions of *now*: "time to sunset" computed at sensor poll time is wrong 60 seconds later. The server treats them differently — reading-bound values can be computed once and cached; time-bound values are recomputed on every request.

---

## Endpoint summary

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/api/v1/current` | Latest reading from every sensor + astronomy block + optional `external` block |
| `GET` | `/api/v1/current/{sensor_id}` | Latest reading from one sensor + astronomy block + optional `external` block |
| `GET` | `/api/v1/history/{sensor_id}` | Time-bucketed history for one sensor |
| `GET` | `/api/v1/summary/{sensor_id}` *(post-launch)* | Windowed history summary (hi/lo/avg, tendency, degree days, DLI, ET₀); outdoor only |
| `GET` | `/api/v1/external` *(post-launch)* | The optional internet-sourced regional block alone (wind + conditions + fused indices) |
| `GET` | `/api/v1/sensors` | List of registered sensors with status |
| `GET` | `/api/v1/astronomy` | Astronomy block alone (no sensor data) |
| `GET` | `/api/v1/branding` *(post-launch)* | Parsed `branding.toml` used by the dashboard |
| `GET` | `/api/v1/health` | Health of server, DB, loggers, sensors |

All responses are `application/json`. All timestamps are ISO 8601 with timezone. Server time is UTC; user-facing times (sunrise, moonrise, local_time) are in the timezone derived from the outdoor sensor's GPS coordinates.

---

## Common objects

### `SensorReading`

Used in `/api/v1/current`, `/api/v1/current/{sensor_id}`, and as the building block for history rows (with some fields stripped — see history endpoint).

```json
{
  "sensor_id": "outdoor",
  "role": "outdoor",
  "online": true,
  "reading_timestamp": "2026-05-22T15:30:00Z",
  "age_seconds": 28,

  "raw": {
    "temperature_c": 18.9,
    "humidity_pct": 42.1,
    "pressure_pa": 84725,
    "lux": 12450.0,
    "ir": 230,
    "visible": 8200,
    "full": 8430
  },

  "calibration": {
    "temp_offset_c": -0.5
  },

  "derived": {
    "temperature_c": 18.4,
    "temperature_f": 65.1,
    "dewpoint_c": 5.1,
    "dewpoint_f": 41.2,
    "absolute_humidity_g_m3": 6.7,
    "pressure_station_hpa": 847.25,
    "pressure_station_inhg": 25.02,
    "pressure_sealevel_hpa": 1023.0,
    "pressure_sealevel_inhg": 30.21
  },

  "location": {
    "lat": 39.7392,
    "lon": -104.9903,
    "altitude_m": 1609.3,
    "altitude_ft": 5279.9,
    "satellites": 9,
    "speed_kmh": 0.0,
    "course_deg": 0.0,
    "dms": "39°44'21.1\"N  104°59'25.1\"W",
    "maidenhead": "DM79mr"
  },

  "device": {
    "rssi_dbm": -62,
    "uptime_s": 84320,
    "free_heap_bytes": 178432
  }
}
```

#### Field provenance for `SensorReading`

| Field | Tag | Notes |
|---|---|---|
| `sensor_id` | META | Stable identifier, used in paths |
| `role` | META | `outdoor`, `indoor`, `basement`, etc. |
| `online` | META | `age_seconds < threshold` (default 120s, configurable per sensor) |
| `reading_timestamp` | META | When the row was written to the DB |
| `age_seconds` | META | `server_time - reading_timestamp` |
| `raw.temperature_c` | RAW | Direct BME280 reading, before offset |
| `raw.humidity_pct` | RAW | |
| `raw.pressure_pa` | RAW | Stored in Pa (the SI unit). Conversion to hPa is in `derived` |
| `raw.lux` / `ir` / `visible` / `full` | RAW | Outdoor only; absent on indoor sensors |
| `calibration.temp_offset_c` | META | The offset applied to produce `derived.temperature_c` |
| `derived.temperature_c` | CALIBRATED | `raw.temperature_c + calibration.temp_offset_c` |
| `derived.temperature_f` | D-READING | Conversion from `derived.temperature_c` |
| `derived.dewpoint_c` | D-READING | Magnus formula on calibrated temp + humidity |
| `derived.dewpoint_f` | D-READING | |
| `derived.absolute_humidity_g_m3` | D-READING | Magnus-based |
| `derived.pressure_station_hpa` | D-READING | `raw.pressure_pa / 100`. Pressure at the sensor's altitude — what the BME280 actually measured |
| `derived.pressure_station_inhg` | D-READING | Station pressure converted to inHg |
| `derived.pressure_sealevel_hpa` | D-READING | Station pressure adjusted to sea level using `location.altitude_m` and the barometric formula. Requires GPS-reported altitude; falls back to a per-sensor configured altitude if no GPS fix |
| `derived.pressure_sealevel_inhg` | D-READING | Sea-level pressure converted to inHg. **This is the value that matches NWS reporting** |
| `location.lat` / `lon` | RAW | Outdoor only |
| `location.altitude_m` | RAW | Outdoor only |
| `location.altitude_ft` | D-LOCATION | |
| `location.satellites` / `speed_kmh` / `course_deg` | RAW | Outdoor only |
| `location.dms` | D-LOCATION | |
| `location.maidenhead` | D-LOCATION | |
| `device.rssi_dbm` / `uptime_s` / `free_heap_bytes` | RAW | Reported by ESP32 alongside weather data |

**Optional blocks.** `location` is absent for sensors without GPS (indoor, basement). `device` and `calibration` blocks are present for all sensors but can be empty objects if the sensor doesn't report telemetry.

#### Post-launch additions to `derived` (all `D-READING`)

The `derived` block grew well beyond the original nine fields. Source of truth is
`DerivedReading` in `server/weather_server/schemas.py`. Added fields:

- **Comfort:** `feels_like_c` / `feels_like_f` (NWS heat index, warm side only — wind chill lives
  in the `external` block).
- **Thermodynamics:** `wet_bulb_c/f`, `humidex_c/f`, `frost_point_c/f`,
  `saturation_vapor_pressure_hpa`, `vapor_pressure_hpa`, `vapor_pressure_deficit_kpa`,
  `mixing_ratio_g_kg`, `specific_humidity_g_kg`, `air_density_kg_m3`,
  `pressure_altitude_m/ft`, `density_altitude_m/ft`, `cloud_base_m/ft`.
- **`derived.sky` sub-block** (light sensor + sun altitude; ESTIMATES — carries `estimated: true`):
  `sun_altitude_deg`, `solar_irradiance_w_m2`, `cloud_cover_pct`, `uv_index_estimate`,
  `sky_condition`. Present only on light-equipped sensors with GPS; `cloud_cover_pct`/`uv` are
  `null` when the sun is too low to be reliable.

All cascade to `null` when their inputs are missing (same contract as `pressure_sealevel_*`).

---

### `Astronomy`

Used in `/api/v1/current`, `/api/v1/current/{sensor_id}`, and `/api/v1/astronomy`.

```json
{
  "server_time": "2026-05-22T15:30:28Z",
  "local_time": "2026-05-22T09:30:28-06:00",
  "timezone": "America/Denver",
  "reference_location": {
    "lat": 39.7392,
    "lon": -104.9903,
    "source": "outdoor_sensor"
  },

  "sun": {
    "altitude_deg": 42.3,
    "azimuth_deg": 156.7,
    "is_daytime": true,
    "sunrise": "2026-05-22T05:38:00-06:00",
    "sunset": "2026-05-22T20:15:00-06:00",
    "solar_noon": "2026-05-22T12:56:00-06:00",
    "dawn": "2026-05-22T05:08:00-06:00",
    "dusk": "2026-05-22T20:45:00-06:00",
    "day_length_seconds": 52620,
    "seconds_to_sunset": 38612,
    "seconds_to_sunrise": null
  },

  "moon": {
    "altitude_deg": -23.1,
    "azimuth_deg": 87.0,
    "distance_km": 384720,
    "illumination_pct": 73.4,
    "phase_name": "Waxing Gibbous",
    "phase_icon": "🌔",
    "moonrise": "2026-05-22T14:23:00-06:00",
    "moonset": "2026-05-23T03:45:00-06:00",
    "always_up": false,
    "always_down": false
  }
}
```

#### Field provenance for `Astronomy`

| Field | Tag | Notes |
|---|---|---|
| `server_time` | META | UTC, time of the request |
| `local_time` | D-TIME | `server_time` projected into the reference location's timezone |
| `timezone` | D-LOCATION | IANA name, derived from `reference_location` via `timezonefinder` (or similar) |
| `reference_location.{lat,lon}` | RAW | Pulled from the outdoor sensor's most recent reading by default |
| `reference_location.source` | META | `outdoor_sensor`, `config_default`, or `query_override` |
| `sun.altitude_deg` / `azimuth_deg` | D-TIME | Computed at `server_time` |
| `sun.is_daytime` | D-TIME | `sun.altitude_deg > 0` |
| `sun.sunrise` / `sunset` / `solar_noon` / `dawn` / `dusk` | D-TIME | For today in the reference timezone |
| `sun.day_length_seconds` | D-TIME | `sunset - sunrise` |
| `sun.seconds_to_sunset` | D-TIME | `null` after sunset |
| `sun.seconds_to_sunrise` | D-TIME | `null` before sunset; counts to tomorrow's sunrise after |
| `moon.altitude_deg` / `azimuth_deg` / `distance_km` | D-TIME | |
| `moon.illumination_pct` | D-TIME | |
| `moon.phase_name` | D-TIME | One of: New, Waxing Crescent, First Quarter, Waxing Gibbous, Full, Waning Gibbous, Last Quarter, Waning Crescent |
| `moon.phase_icon` | D-TIME | Emoji glyph; consumers free to ignore |
| `moon.moonrise` / `moonset` | D-TIME | May be `null` if event doesn't occur in the 24h window |
| `moon.always_up` / `always_down` | D-TIME | High-latitude edge cases |

**Reference location.** By default, astronomy is computed at the outdoor sensor's most recent GPS coordinates. If the outdoor sensor has no fix yet, the server falls back to a configured default lat/lon. The `reference_location.source` field tells the consumer which path was taken. Callers of `/api/v1/astronomy` can override via `?lat=...&lon=...` if needed.

#### Post-launch additions to `Astronomy` (all `D-TIME`/`D-LOCATION`)

- **`sun`:** `nautical_dawn/dusk` (−12°), `astronomical_dawn/dusk` (−18°),
  `golden_hour_dawn/dusk` (+6°), `blue_hour_dawn/dusk` (−4°), `sunrise_azimuth_deg`,
  `sunset_azimuth_deg`, `shadow_multiplier`, `day_length_change_seconds` (vs. yesterday),
  `season`, `next_solar_event`, `next_solar_event_time`, `seconds_to_next_solar_event`.
- **`moon`:** `next_new_moon`, `next_full_moon`.

---

## Endpoint specifications

### `GET /api/v1/current`

Latest reading from every registered sensor, plus a single astronomy block.

**Query parameters:** none.

**Response 200:**

```json
{
  "server_time": "2026-05-22T15:30:28Z",
  "sensors": {
    "outdoor": { /* SensorReading */ },
    "indoor":  { /* SensorReading */ },
    "basement": { /* SensorReading */ }
  },
  "astronomy": { /* Astronomy */ },
  "external": { /* ExternalBlock | null — post-launch; null when the feed is off/offline */ }
}
```

The `sensors` map is keyed by `sensor_id`. Sensors that have never reported are absent from the map. Sensors that have reported but are currently offline are present with `online: false` and the last-known reading (with `age_seconds` showing how stale it is).

**Caching:** server may cache the response body for up to 1 second to deduplicate near-simultaneous polls from multiple clients. The astronomy block alone may be cached for up to 5 seconds.

---

### `GET /api/v1/current/{sensor_id}`

Latest reading from one named sensor, plus the astronomy block.

**Path parameters:**
- `sensor_id` — one of the IDs returned by `/api/v1/sensors`.

**Response 200:**

```json
{
  "server_time": "2026-05-22T15:30:28Z",
  "sensor": { /* SensorReading */ },
  "astronomy": { /* Astronomy */ },
  "external": { /* ExternalBlock | null — post-launch; null when the feed is off/offline */ }
}
```

**Response 404:** unknown `sensor_id`.

**Response 503:** the sensor is registered but has never reported and is currently unreachable, so there is no last-known reading to return. `code: "sensor_no_data"`. Once at least one successful poll has been recorded, subsequent upstream failures return `200` with the last-known `SensorReading` and `online: false`.

---

### `GET /api/v1/history/{sensor_id}`

Time-bucketed history for one sensor.

Only `outdoor` is currently logged (see `server/weather_server/db.py`). Calls with any other `sensor_id` return `404` with `code: "history_not_available"`.

**Path parameters:**
- `sensor_id` — must be `outdoor`.

**Query parameters:**

| Param | Type | Default | Notes |
|---|---|---|---|
| `hours` | int | `24` | Time window (mutually exclusive with `from`/`to`) |
| `from` | ISO 8601 | — | Start of window |
| `to` | ISO 8601 | `now` | End of window |
| `bucket` | enum | `auto` | `raw`, `60`, `300`, `900`, `3600`, `auto` (server picks based on range) |
| `include` | csv | `weather` | `weather`, `light`, `location`, `device` — selects which field groups appear in rows |

**`bucket=auto` heuristic:**

| Range | Bucket |
|---|---|
| ≤ 1h | `raw` (no aggregation) |
| ≤ 6h | `60` (1 min) |
| ≤ 24h | `300` (5 min) |
| ≤ 7d | `1800` (30 min) |
| > 7d | `3600` (1 hour) |

> **Note:** the explicit `bucket` enum and the `auto` heuristic aren't identical. `900` (15 min)
> is accepted only as an explicit `?bucket=900` — `auto` never selects it. Conversely the 30-min
> bucket `auto` picks for the ≤7d band is internal: it appears in the response `bucket_seconds`
> but isn't an explicitly requestable enum value.

**Window selection:** pass either `?hours=` (default 24) **or** an explicit `?from=&to=`
(ISO 8601; `to` defaults to now). When `from`/`to` are supplied they define the window and the
`bucket=auto` heuristic uses that span.

**Response 200:**

```json
{
  "sensor_id": "outdoor",
  "from": "2026-05-21T15:30:00Z",
  "to":   "2026-05-22T15:30:00Z",
  "bucket_seconds": 300,
  "row_count": 288,
  "rows": [
    {
      "timestamp": "2026-05-21T15:30:00Z",
      "temperature_c": 18.4,
      "temperature_f": 65.1,
      "humidity_pct": 42.1,
      "pressure_sealevel_hpa": 1023.0,
      "pressure_station_hpa": 847.25,
      "dewpoint_c": 5.1
    }
  ]
}
```

History rows are stripped down: no astronomy, no light data by default (use `include=light`), no device telemetry by default (use `include=device`), no Maidenhead grid (use `include=location`). Each row carries pre-computed derived values for the bucketed reading, so the client never needs to compute °F or dew point itself.

The field groups are:

| Group | Fields |
|---|---|
| `weather` | `temperature_c`, `temperature_f`, `humidity_pct`, `pressure_station_hpa`, `pressure_sealevel_hpa`, `dewpoint_c` |
| `light` | `lux`, `ir`, `visible`, `full` |
| `location` | `lat`, `lon`, `altitude_m`, `satellites`, `maidenhead` |
| `device` | `rssi_dbm`, `uptime_s`, `free_heap_bytes` |

Aggregation within a bucket is mean for continuous quantities (temperature, humidity, pressure, lux) and most-recent for discrete/state values (satellites, RSSI, etc.).

---

### `GET /api/v1/sensors`

Lists all sensors known to the server with their current status.

**Response 200:**

```json
{
  "server_time": "2026-05-22T15:30:28Z",
  "sensors": [
    {
      "sensor_id": "outdoor",
      "role": "outdoor",
      "ip": "192.168.1.60",
      "has_gps": true,
      "has_light": true,
      "logged": true,
      "last_seen": "2026-05-22T15:30:00Z",
      "age_seconds": 28,
      "online": true
    },
    {
      "sensor_id": "indoor",
      "role": "indoor",
      "ip": "192.168.1.61",
      "has_gps": false,
      "has_light": false,
      "logged": false,
      "last_seen": "2026-05-22T15:30:16Z",
      "age_seconds": 12,
      "online": true
    },
    {
      "sensor_id": "basement",
      "role": "indoor",
      "ip": "192.168.1.63",
      "has_gps": false,
      "has_light": false,
      "logged": false,
      "last_seen": "2026-05-22T15:00:00Z",
      "age_seconds": 1828,
      "online": false
    }
  ]
}
```

Sensor registration lives in server config (a TOML file). Adding a sensor is an edit-and-restart operation, not an API call. The `logged` flag indicates whether the sensor's readings are stored in the database; consumers should not call `/api/v1/history/{sensor_id}` for sensors where `logged: false`.

---

### `GET /api/v1/astronomy`

Returns just the astronomy block. Useful for consumers that want sun/moon data without sensor readings.

**Query parameters:**

| Param | Type | Default | Notes |
|---|---|---|---|
| `lat` | float | outdoor sensor | Override reference latitude |
| `lon` | float | outdoor sensor | Override reference longitude |

**Response 200:**

```json
{
  "server_time": "2026-05-22T15:30:28Z",
  "astronomy": { /* Astronomy */ }
}
```

---

### `GET /api/v1/external` *(post-launch)*

The optional internet-sourced regional block alone (the same object embedded in `/current`).

**Query parameters:** none.

**Response 200:** `{ "server_time": ..., "external": ExternalBlock | null }` — `external` is `null`
when the feed is disabled or no successful fetch has occurred (offline-first).

#### `ExternalBlock` (provenance `EXTERNAL`)

All fields optional. Wind is stored internally in m/s and exposed in every unit. Source of truth:
`ExternalBlock` in `server/weather_server/schemas.py`.

| Field | Notes |
|---|---|
| `provider` / `source` / `station_id` / `distance_km` | provenance: `open-meteo`/`nws`/`wunderground`; human label (e.g. `nws:KBJC`); station + distance for station providers |
| `observed_at` / `fetched_at` / `age_seconds` / `stale` / `confidence` | freshness; `stale` once age exceeds ~3× refresh; `confidence` = `low` when the optional NWS cross-check diverges |
| `wind_speed_ms` / `_kmh` / `_mph` / `_kt` | wind speed in four units |
| `wind_gust_ms` / `_kmh` / `_mph` | gust |
| `wind_direction_deg` / `wind_direction_cardinal` | bearing + 16-point compass label |
| `cloud_cover_pct` / `uv_index` / `precip_mm` / `visibility_m` / `visibility_km` | regional conditions (model/station) |
| `wind_chill_c/f` / `apparent_temperature_c/f` | fused: NWS wind chill + BOM full-range apparent temp (local temp/humidity + external wind) |
| `beaufort_force` / `beaufort_description` | Beaufort scale |
| `thsw_index_c/f` | temp-humidity-sun-wind index (uses `derived.sky` irradiance); estimate |
| `et0_mm_hour` | reference evapotranspiration, FAO-56 hourly Penman-Monteith |

---

### `GET /api/v1/summary/{sensor_id}` *(post-launch)*

Windowed history summary derived from the logged time series (provenance `D-HISTORY`). **Outdoor
only** — other sensors return `404` `history_not_available`, unknown ids `404` `sensor_not_found`.

**Query parameters:** `period` ∈ `today` (default) | `24h` | `7d` | `30d`.

**Response 200** (`SummaryResponse`): `sensor_id`, `period`, `from`/`to`, `timezone`, `sample_count`,
plus — `temperature_c`/`temperature_f`/`humidity_pct`/`pressure_station_hpa`/`pressure_sealevel_hpa`
each as a `{min,max,avg}` block; `dewpoint_avg_c`; `diurnal_range_c`; `pressure_tendency_hpa_3h` +
`pressure_trend` (`rising`/`falling`/`steady` — observed, **not** a forecast);
`temperature_trend_c_per_hour`; `heating_degree_days_f` / `cooling_degree_days_f` /
`growing_degree_days_f` (accumulated, US bases); `light_integral_mol_m2` (DLI);
`hargreaves_et0_mm` (temperature-only reference ET₀ — wind isn't logged).

---

### `GET /api/v1/branding` *(post-launch)*

Returns the parsed `branding.toml` (header/footer/taglines/state copy) the dashboard renders. No
parameters.

---

### `GET /api/v1/health`

System self-check. Used by uptime monitors and by the dashboard's "all systems normal" indicator.

**Response 200:**

```json
{
  "ok": true,
  "server_time": "2026-05-22T15:30:28Z",
  "db_reachable": true,
  "sensors": [
    {"sensor_id": "outdoor", "online": true, "age_seconds": 28},
    {"sensor_id": "indoor", "online": true, "age_seconds": 12},
    {"sensor_id": "basement", "online": false, "age_seconds": 1828}
  ],
  "loggers": [
    {"sensor_id": "outdoor", "last_write_seconds_ago": 28, "ok": true}
  ]
}
```

`ok` is `true` iff `db_reachable` is `true` and every logger's `ok` is `true`. Individual sensors being offline does **not** make `ok: false` — a sensor that's been unplugged is a known state, not a system fault. Only the outdoor logger appears here because only the outdoor sensor is logged.

---

## Error responses

Standard shape for all errors:

```json
{
  "error": {
    "code": "sensor_not_found",
    "message": "No sensor with id 'kitchen' is registered.",
    "details": {}
  }
}
```

| HTTP | `code` | When |
|---|---|---|
| 400 | `bad_request` | Malformed query parameters |
| 404 | `sensor_not_found` | Unknown `sensor_id` in path |
| 404 | `history_not_available` | `sensor_id` is valid but is not logged (only `outdoor` is) |
| 500 | `internal_error` | Uncaught exception (logged server-side) |
| 503 | `db_unavailable` | SQLite open failed or DB file missing |
| 503 | `sensor_no_data` | `GET /current/{sensor_id}`: the sensor has never successfully reported and is currently unreachable. Once any successful poll has been recorded, later failures return `200` with the last-known reading and `online: false` instead. |

---

## What this design deliberately excludes

1. **No `/data` or `/?url=...` proxy endpoint.** Removing this closes SEC-01, SEC-03, and ARCH-06 in one stroke. Clients no longer ask the server to fetch arbitrary URLs; they ask for data by name.
2. **No write endpoints in v1.** Calibration changes go through config + restart, or a separate admin path we can scope later.
3. **No forecasting.** Per the decision logged in `weather-station-findings.md`.
4. **No WebSocket / SSE streaming.** Polling at 5–30s intervals is fine for the data rates here. Streaming can be added in v2 if a real consumer demands it.
5. **No raw sensor passthrough.** Consumers can't ask "give me what came off the wire from the ESP32." They get the structured, processed `SensorReading`. The ESP32 wire format becomes a private implementation detail of the logger.
6. **No client-driven aggregation.** History buckets are server-side. Clients don't get the raw 86,400-row firehose for a 24h view.

---

## How this maps back to the findings list

This API design, when implemented, closes or reduces the scope of the following findings:

| Finding | Effect |
|---|---|
| SEC-01 | Closed — proxy SSRF endpoint removed |
| SEC-03 | Closed — no client-supplied URLs to validate |
| SEC-04 | Closed — `/setOffset` endpoint on ESP32 removed; calibration is server-side |
| SEC-06 | Reduced — CORS can be tightened since the SSRF risk is gone |
| BUG-08 | Closed — server parses raw sensor wire format; downstream JSON is well-formed |
| BUG-15 | Addressed — new server uses a real framework (e.g. FastAPI/aiohttp), not single-threaded `HTTPServer` |
| ARCH-01 | Addressed — split into a schema with raw readings vs derived columns vs device telemetry |
| ARCH-03 | Closed — history endpoint requires server-side bucketing |
| ARCH-04 | Closed — concerns are split across endpoints |
| ARCH-05 | Closed — `/api/v1/current` is the single source for both live and historical data freshness |
| ARCH-06 | Closed — no more browser→proxy→sensor roundtrip |
| ARCH-07 | Unchanged here — WiFi credentials are still a sketch-level concern |
| BUG-05, BUG-06 | Addressed — `TEMP_OFFSET` moves to server config; the ESP32 no longer stores it |
| BUG-07 | Closed — server-side processing reports per-metric validity, not a single bool |
| BUG-21 | Closed — pressure is exposed as four explicitly-named fields (station vs sea-level, hPa vs inHg); no consumer can mislabel |
| PERF-04 | Closed — history is downsampled server-side |
| QUAL-05 | Improved — the ESP32 no longer needs to serve an HTML status page |

---

## Resolved design decisions

The questions raised in the first draft have been worked through. The decisions below are the operational answers; implementation proceeds against these.

### Server framework: **FastAPI**

Pydantic models give the schemas in this document as actual runtime-validated Python types. Free OpenAPI docs at `/docs`. Async support means a slow DB query doesn't block other handlers (closes BUG-15). Runs comfortably on a Pi Zero 2 W with uvicorn. The alternative (Flask + Marshmallow) ends up the same size with more glue code.

### Config file format: **TOML**

Python 3.11+ has `tomllib` in the standard library — zero new dependencies for reading. More rigid than YAML, which means fewer foot-guns (the `yes`/`true`/`no`/`false` ambiguity, the "Norway problem," indentation surprises). Becoming the de facto standard in the Python ecosystem.

A representative config file:

```toml
[server]
host = "0.0.0.0"
port = 8005
db_path = "weather.db"
dashboard_dir = "../dashboard"      # post-launch; static dashboard served at /dashboard/
branding_path = "../branding.toml"  # post-launch; parsed for /api/v1/branding

[logger]
interval_seconds = 60
http_timeout_seconds = 10

[cache]
ttl_seconds = 5

# Post-launch: optional internet feed. OFF by default (absent section / enabled=false);
# when off the server is LAN-only and the `external` block is null.
[external]
enabled = true
provider = "open-meteo"             # open-meteo | nws | wunderground
refresh_interval_seconds = 300
http_timeout_seconds = 8
fetch = ["wind", "cloud", "uv", "precip", "visibility"]
cross_check = false
# station_id / api_key (wunderground), lat_override / lon_override — all optional

[[sensors]]
id = "outdoor"
role = "outdoor"
ip = "192.168.1.60"
has_gps = true
has_light = true
online_threshold_seconds = 120
temp_offset_c = -0.5
fallback_altitude_m = 1609.3   # used for sea-level adjustment if GPS has no fix
fallback_lat = 39.7392         # used by astronomy if GPS has no fix
fallback_lon = -104.9903       # used by astronomy if GPS has no fix

[[sensors]]
id = "indoor"
role = "indoor"
ip = "192.168.1.61"
has_gps = false
has_light = false
online_threshold_seconds = 120
temp_offset_c = 0.0

[[sensors]]
id = "basement"
role = "indoor"
ip = "192.168.1.63"
has_gps = false
has_light = false
online_threshold_seconds = 300
temp_offset_c = 0.0
```

### Derived values: **server-side at read time, raw only in DB**

The DB stores only what the sensor reported (RAW) plus calibration metadata. Every value tagged `D-READING`, `D-LOCATION`, or `D-TIME` is computed at request time by the API server.

Rationale:
- Derivation cost is microseconds per row. A 7-day history at 30-minute buckets is ~336 rows — total derivation cost under a millisecond.
- Formula bugs (BUG-21 being a perfect example) get fixed in one place and immediately apply to all historical data. No backfill required.
- Schema doesn't change when a new derived field is added.
- The math lives in exactly one place (the API server's derivation module), with one set of unit tests.

The tradeoff — slightly more CPU per request — is irrelevant at this data scale.

### Pressure storage and naming

- **Stored in the DB:** `pressure_pa` (raw station pressure in Pascals, the SI unit, exactly as the BME280 reports it).
- **Exposed by the API:** four distinct fields — `pressure_station_hpa`, `pressure_station_inhg`, `pressure_sealevel_hpa`, `pressure_sealevel_inhg`. Each field's name fully describes which physical quantity it represents.
- **Sea-level adjustment** uses `location.altitude_m` from the live GPS reading. For sensors without GPS, the config file's `fallback_altitude_m` is used. If neither is available, sea-level fields are `null`.
- The dashboard, tray, and any future consumer pick which pair (station vs sea-level) to display. The NWS-comparable values are the sea-level pair.

This directly resolves BUG-21.

### Timezone resolution: **dynamic via `timezonefinder`**

The outdoor sensor's GPS coordinates feed `timezonefinder` to resolve an IANA timezone name, which then drives all local-time formatting in the astronomy block. The data ships offline with the library, so no internet connection is required.

Supports the project's "deploy anywhere with no configuration, no network dependencies" goal. The ~80 MB disk footprint is the cost; on a Pi SD card it's noise.

If the outdoor sensor has no GPS fix and there's no cached previous fix, the server falls back to UTC and sets `astronomy.reference_location.source = "config_default"` so consumers can render a "no location data yet" state.

### Process model

Settled in `server/weather_server/main.py`: single FastAPI process, outdoor logging runs as a background asyncio task within it, indoor and basement sensors are polled on-demand by request handlers (with a 5-second TTL cache to deduplicate concurrent fetches). One systemd unit, one log file. The choice is reversible if real-world latency patterns warrant moving indoor/basement polling to a background task; the API contract is unaffected.

### Database schema

Settled in `server/weather_server/db.py`. Key points that affect the API:

- **Storage engine:** SQLite, WAL mode, single file. Replaces MariaDB.
- **Greenfield:** no migration from existing tables.
- **Outdoor only:** only the outdoor sensor is logged. Indoor and basement sensors remain live-only.
- **Raw storage:** the DB stores raw sensor values; all derivations happen in the API at read time.
- **One table:** `outdoor_readings`, 16 data columns, one index on `timestamp`.

API implications, reflected in the endpoint specs below:

- `/api/v1/history/{sensor_id}` returns `404` for any sensor other than `outdoor`.
- `/api/v1/sensors` includes a per-sensor `logged: bool` field so consumers know whether history is available for that sensor.
- `/api/v1/health.loggers` contains exactly one entry (`outdoor`).
- `/api/v1/current` continues to return all three sensors. The outdoor reading comes from the DB (latest row); the indoor and basement readings come from live polls of those sensors.
