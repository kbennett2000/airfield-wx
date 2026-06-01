# Glossary

Plain-language definitions of the terms used across airfield-wx — both the **aviation** terms and the
**computing** ones. You don't need all of these to get started; come back when a word trips you up.

> **UNOFFICIAL — NOT FOR FLIGHT PLANNING.** See the [README](../../README.md).

## Aviation terms

**Density altitude** — the altitude your airplane *thinks* it's at, performance-wise, once you account
for how hot and how high you are. Hot, high, and humid air is "thin," so the engine, wing, and prop all
behave as if you were higher up. It's the headline number on the dashboard because it's what decides
takeoff roll and climb. Measured in **feet**.

**Pressure altitude** — altitude above the standard-pressure datum (29.92 inHg / 1013.2 hPa). It's a
building block for density altitude; you'd see it if you set your altimeter to 29.92.

**Altimeter setting / QNH** — the local pressure value you dial into your altimeter's **Kollsman
window** so it reads true field elevation on the ground. airfield-wx derives it from your measured
station pressure and your field elevation. (QNH is the international shorthand for this setting.)

**Station pressure** — the raw air pressure measured *at the sensor*, before any correction for
elevation. Altimeter setting and sea-level pressure are both computed from it.

**METAR** — a routine aviation weather observation issued by an airport, usually hourly. It reports
wind, visibility, cloud layers, temperature, and the altimeter setting in a terse coded line.
airfield-wx can fetch the **nearest** METAR (when you have internet) and always labels it with which
station it came from, how far away, and how old it is — because that's conditions *there*, not
necessarily over your strip.

**Flight category (VFR / MVFR / IFR / LIFR)** — a quick color-coded summary of how good or bad the
ceiling and visibility are at a reporting station: **VFR** (good, green), **MVFR** (marginal, blue),
**IFR** (instrument conditions, red), **LIFR** (low IFR, magenta). airfield-wx only ever shows this when
it comes **from a METAR** — it never guesses a category from your own sensors, because you have no
cloud-height sensor.

**Ceiling** — the height above the ground of the lowest *broken* or *overcast* cloud layer — effectively
the "roof." Reported by the METAR; airfield-wx does not measure it (no ceilometer).

**Visibility** — how far you can see horizontally, in statute miles (SM) or kilometers. From the METAR.

**Magnetic variation (declination)** — the angle between **true** north (the geographic pole) and
**magnetic** north (where your compass points), which differs by location and drifts over years.
airfield-wx computes it from your GPS position using the bundled WMM2025 model, fully offline.

**True vs magnetic heading** — directions can be measured from true north or from magnetic north.
Runways are painted and flown in **magnetic**; the math is cleaner in **true**. airfield-wx works
internally in true and shows you magnetic (what you actually fly), noting the variation it used.

**Headwind / crosswind component** — when the wind isn't straight down the runway, it splits into a
**headwind** part (along the runway, slowing your groundspeed — helpful) and a **crosswind** part
(across it — the part you have to fly out on landing and takeoff). airfield-wx breaks the wind into
these for each runway end.

**Favored runway** — of the runway ends available, the one most into the wind (most headwind, least
tailwind). airfield-wx highlights it and shows its components — but **it does not tell you whether to
fly**; you decide.

**Knots** — nautical miles per hour, the aviation-universal unit for wind and airspeed. airfield-wx
always shows wind in knots (and altitudes in feet) regardless of your other unit choices.

**Dewpoint / spread** — the dewpoint is the temperature at which the air would be saturated; the
**spread** (temperature minus dewpoint) hints at how close the air is to forming cloud or fog. A small
spread = moist air, possible low cloud.

**Anemometer / wind vane** — the wind instrument: the **anemometer** measures wind *speed* (spinning
cups, counted as electrical pulses), and the **vane** measures wind *direction* (a wind-pointer whose
angle is read as a voltage). airfield-wx supports the Davis 6410 and the budget SparkFun Weather Meter.

**Vane offset** — a one-time per-install calibration that tells the software which way the vane's "zero"
is physically pointing, so raw vane angles become true compass directions. Set once in config, not in
code.

**GPS fix** — when the GPS module has locked onto enough satellites to report a position. airfield-wx
uses the fix to find your field, its runways, magnetic variation, timezone, and the nearest METAR
station — so nothing about your location is hard-coded.

## Computing terms

**ESP32** — a small, inexpensive Wi-Fi microcontroller (a tiny computer on a board). It reads the
sensors and serves their values as JSON over your network. You "flash" your program onto it once.

**The terminal (command line)** — a text window where you type commands instead of clicking. On a
Raspberry Pi or Linux box you'll use it to install and run the server. The install guide shows exactly
what to type, and copy-paste is expected — you don't have to memorize anything.

**SSH** — "secure shell," a way to open a terminal on another computer (e.g. your Raspberry Pi) from
your laptop over the network, so you don't need a screen and keyboard plugged into the Pi.

**systemd service** — the standard Linux way to run a program automatically at boot and restart it if it
crashes. `./install.sh` sets airfield-wx up as one so the server is always running on your network.

**LAN (local area network)** — your home network. airfield-wx is **LAN-only**: it talks to your sensors
and serves the dashboard inside your house, with no cloud, no accounts, and nothing exposed to the
internet.

**Fixture / demo mode** — a mode where the server reads canned sample readings from files instead of
real sensors, so you can explore the whole dashboard before building any hardware. Turned on by the
`[development]` block in `weather.toml`.

**The API** — the server's read-only web addresses under `/api/v1/` (like `/api/v1/current`) that return
the data as JSON. The dashboard and the optional tray widget both read from it; you can too.
