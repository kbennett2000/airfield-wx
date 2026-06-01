"""Single source of truth for the internal SensorPayload field names.

The same names appear in fixtures/*.json, in DB columns (with one
exception — see K_FULL_SPECTRUM), and in poll responses.
"""

K_TEMP_C = "temperature_c"
K_HUMIDITY = "humidity_pct"
K_PRESSURE_PA = "pressure_pa"

K_LUX = "lux"
K_IR = "ir"
K_VISIBLE = "visible"
# DB column and internal payload use `full_spectrum`. The API exposes it
# as `full` per 02-api-design.md.
K_FULL_SPECTRUM = "full_spectrum"

K_LATITUDE = "latitude"
K_LONGITUDE = "longitude"
K_ALTITUDE_M = "altitude_m"
K_SATELLITES = "satellites"
K_SPEED_KMH = "speed_kmh"
K_COURSE_DEG = "course_deg"

K_RSSI_DBM = "rssi_dbm"
K_UPTIME_S = "uptime_s"
K_FREE_HEAP_BYTES = "free_heap_bytes"
