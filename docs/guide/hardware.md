# Hardware & Build Guide

How to build, wire, and flash the outdoor sensor unit, and the one field-calibration step (vane north
alignment).

> **UNOFFICIAL — NOT FOR FLIGHT PLANNING.** This device is not a certified instrument. See the
> [README](../../README.md).

## Sensor suite

The outdoor unit is an **ESP32** with three sensors:

| Sensor | Measures | Interface |
|---|---|---|
| **BME280** | temperature, humidity, station pressure | I²C |
| **GPS** (e.g. NEO-6M) | latitude, longitude, altitude, satellites | UART (serial) |
| **Anemometer** | wind speed (pulses) + direction (vane) | digital pulse + analog (ADC) |

There is **no light sensor** — sky/cloud/UV come from the optional METAR feed instead, which is more
reliable for a pilot than a backyard lux estimate. An optional SSD1306 OLED shows a local status page.

**Anemometer choices:**

- **Primary — Davis 6410.** Continuous-potentiometer direction (smooth 0–360°), rugged, reed-switch
  speed. The sketch's defaults are tuned for it.
- **Budget — SparkFun Weather Meter Kit.** Reed-switch speed + a resistor-network vane (8/16 discrete
  positions). Works fine with two changes noted below.

## ESP32 pin assignments

These are taken **directly from `sketches/outdoor.ino`** — they are the authoritative pinout. If you
re-pin, change the `#define`s in the sketch, not just this table.

| Function | ESP32 pin | Notes |
|---|---|---|
| BME280 SDA | **GPIO 21** | I²C (`Wire.begin(21, 22)`), address **0x76** |
| BME280 SCL | **GPIO 22** | I²C |
| GPS RX (ESP32 receives) | **GPIO 16** | `Serial2` @ **9600** baud |
| GPS TX (ESP32 sends) | **GPIO 17** | `Serial2` |
| Anemometer **speed** pulse | **GPIO 25** | `INPUT_PULLUP`, FALLING-edge interrupt (`WIND_SPEED_PIN`) |
| Anemometer **direction** wiper | **GPIO 34** | ADC1, 12-bit (`WIND_DIR_PIN`) |
| OLED (optional) | GPIO 21/22 | I²C, address 0x3C |

GPIO 34 is input-only with no internal pull — correct for the vane wiper. The speed pin uses the
internal pull-up; the reed switch closes it to ground once per revolution.

## Wiring the anemometer

**Speed (pulse):** the reed/hall switch connects **GPIO 25 ↔ GND**. Each closure is one count; the
firmware debounces (`WIND_DEBOUNCE_US = 1000`, ~1 ms) and converts pulse frequency to m/s.

**Direction (Davis 6410, continuous pot):** three wires — **3.3 V → pot high**, **wiper → GPIO 34**,
**GND → pot low**. The firmware reads the ADC and maps it linearly to 0–360° in `vaneDegrees()`.

> Confirm the exact wire colors against your unit's datasheet before powering up — connector pinouts
> vary between revisions.

**Speed-constant calibration (`#define` in the sketch):**

```c
#define WIND_MPH_PER_HZ 2.25f   // Davis 6410 (per datasheet)
```

- **Davis 6410:** leave at `2.25`.
- **SparkFun Weather Meter:** change to `1.492` (≈ 2.4 km/h per Hz), **and** replace the linear
  `vaneDegrees()` map with a nearest-voltage lookup table — the SparkFun vane is a resistor network
  with discrete positions, not a continuous pot.

The firmware converts to **m/s** on the wire; the server converts to knots for display (knots are
fixed, aviation-universal).

## Mounting topologies (ADR-0006)

Good wind data wants the vane/cups **high on a clear mast**, away from building eddies. The BME280 wants
**shaded, ventilated** air near the area of interest, and the GPS wants sky view. At many fields these
can't all live in one spot — so airfield-wx supports three arrangements. The first two are the default
(one device); the third is an opt-in for sites where wind and thermo can't co-locate.

| # | Topology | Devices | Wind source |
|---|----------|---------|-------------|
| 1 | **All-in-one** *(default)* | one ESP32: BME280 + GPS + anemometer | outdoor payload |
| 2 | **Remote anemometer on a cable** *(default-equivalent)* | one ESP32; anemometer high on a mast, wires run down to it | outdoor payload |
| 3 | **Separate wind station** *(opt-in)* | a **second** ESP32 + anemometer (no BME280, no GPS) at the good wind spot | the wind station |

- **Topologies 1 & 2 are firmware-identical** — flash `outdoor.ino`; topology 2 is just a longer sensor
  lead. `[wind] source = "outdoor"` (the default).
- **Topology 3** adds a dedicated wind station running `wind_station.ino`. Location intelligence stays
  anchored to the **outdoor** unit's GPS (one field, one position); the wind station reports anemometer
  data only. Select it with `[wind] source = "<wind-station-id>"` (see the
  [install guide](install.md#wind-flexible-anemometer-topology)).

**Which vane offset applies where.** The `wind_vane_offset_deg` calibration (below) travels with the
device that **physically holds the anemometer** — the outdoor unit in topologies 1/2, the wind station
in topology 3. The server applies it to whichever device `[wind] source` names.

### Separate wind station pinout (`sketches/wind_station.ino`)

Anemometer-only — the same wind pins as the outdoor unit, nothing else:

| Function | ESP32 pin | Notes |
|---|---|---|
| Anemometer **speed** pulse | **GPIO 25** | `INPUT_PULLUP`, FALLING-edge interrupt (`WIND_SPEED_PIN`) |
| Anemometer **direction** wiper | **GPIO 34** | ADC1, 12-bit (`WIND_DIR_PIN`) |

Wiring, the speed constant (`WIND_MPH_PER_HZ`), and the Davis 6410 / SparkFun vane handling are
**identical to the outdoor unit** (above) — `wind_station.ino` reuses that anemometer code verbatim. It
has no BME280, GPS, or OLED.

## Flashing

The sketches are in `sketches/`:

- **`outdoor.ino`** — the field unit (BME280 + GPS + anemometer). Flash with the Arduino IDE or
  `arduino-cli` (ESP32 board support + the BME280, TinyGPS, Adafruit SSD1306 libraries).
- **`wind_station.ino`** *(topology 3 only)* — the separate wind station (anemometer only). Needs only
  the ESP32 core libraries (no BME280/TinyGPS/SSD1306).

Before flashing either, edit the top of the file:

- `ssid` / `password` — your Wi-Fi credentials.
- The static IP block (`ip`, `gateway`, `subnet`, `dns`) — pick a LAN address and record it; you'll put
  it in `server/weather.toml` as that sensor's `ip`. `wind_station.ino` defaults to `192.168.1.61` so it
  doesn't collide with the outdoor unit's `192.168.1.60` — change both to suit your LAN.

After flashing, browse to `http://<sensor-ip>/data`:

- **outdoor** → a JSON object with `temperatureC`, `pressure`, `windSpeed`, `windDirection`,
  `latitude`, etc.
- **wind station** → an anemometer-only object: `windSpeed`, `windGust`, `windDirection`, plus `rssi`,
  `uptime`, `freeHeap` (no temperature/pressure/GPS keys).

## Siting

Mount the anemometer **clear of roof/hangar turbulence** — a vane in a building eddy reads a plausible
but wrong direction, which is the classic failure mode (see [ADR-0003](../adr/0003-local-anemometer-wind-first-class.md)).
Open exposure, standard height, away from obstructions.

## Vane north-alignment calibration (the one field step)

The vane's "zero" rarely points at true north when mounted. Rather than physically re-aiming it, you
record a software offset once. **The offset is applied server-side** (`wind_vane_offset_deg`, like the
temperature offset), so the firmware always emits the **raw** vane reading.

Procedure:

1. With the server running, hold or rotate the vane to a **known true bearing** — e.g. line it up due
   **east (090° true)** using a sighting compass corrected for local variation, or align it down a
   runway whose true heading you know.
2. Read the **raw** vane reading from the API:
   ```bash
   curl -s http://<server>:8005/api/v1/current \
     | python3 -c "import json,sys; print(json.load(sys.stdin)['sensors']['outdoor']['raw']['wind_direction_deg'])"
   ```
3. Compute the offset = **(true bearing − raw reading)**, normalized into 0–360. *Hypothetical:* if you
   aimed it at 090° true and the raw reading is 075°, set the offset to **+15**.
4. Put it in `server/weather.toml` under the outdoor sensor:
   ```toml
   [[sensors]]
   id = "outdoor"
   # ...
   wind_vane_offset_deg = 15.0
   ```
5. Restart the server and confirm `derived.wind_direction_true_deg` now reads the true bearing you set
   (it equals `raw.wind_direction_deg + wind_vane_offset_deg`, normalized).

That corrected true direction is what feeds the runway crosswind/headwind solution; the dashboard also
shows the magnetic equivalent using the WMM variation for your field.

---

Next: **[Install & configuration](install.md)** to run the server and point it at your sensor.
