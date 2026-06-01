# ESP32 wire-format samples

Recorded responses from the real ESP32 `/data` endpoint. These exist so the
`wire_format` adapter has a regression test that survives sketch changes
and unusual conditions (nan emissions per BUG-08, error envelope, etc.).

`outdoor_*.json` are outdoor-unit samples; `wind_station_*.json` are
separate-wind-station samples (anemometer-only — ADR-0006/topology 3).

To capture a new sample from a real sensor:

    curl -s http://<sensor-ip>/data > <role>_<descriptive>.json

Add the file here and reference it in `tests/test_wire_format.py`.

Format reference (the JSON keys the sketches emit) lives at
[../../weather_server/wire_format.py](../../weather_server/wire_format.py).
