"""End-to-end integration test for Phase 2 real polling.

Spins up a fake-ESP32 HTTP server on a localhost port in a background
thread, points the weather server's [[sensors]] entries at it (no
fixture_dir), and asserts:

- The logger task writes rows fetched via real HTTP into SQLite.
- /api/v1/current reflects current upstream values.
- Changing the upstream value is visible in /current on the next tick
  (the "I walked outside and the temp dropped" verification).
- /api/v1/current/indoor uses the TTL cache: two concurrent requests
  produce exactly one upstream poll.

This is the closest substitute for the user's
"point at the actual Pi" verification that doesn't need hardware.
"""

from __future__ import annotations

import asyncio
import json
import threading
import time
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

_STATE: dict[str, dict] = {
    "outdoor": {
        "temperatureC": 18.5,
        "humidity": 42.0,
        "pressure": 847.25,
        "lux": 12000.0,
        "ir": 200,
        "visible": 8000,
        "latitude": 39.7392,
        "longitude": -104.9903,
        "altitude": 1609.3,
        "speed": 0.0,
        "course": 0.0,
        "satellites": 9,
        "tempOffset": 0.0,
        "rssi": -60,
        "uptime": 100000,
        "freeHeap": 178000,
    },
    "indoor": {
        "temperatureC": 22.4,
        "humidity": 38.7,
        "pressure": 805.2,
    },
    "basement": {
        "temperatureC": 18.1,
        "humidity": 52.3,
        "pressure": 807.4,
    },
}
_POLL_COUNTS: dict[str, int] = {"outdoor": 0, "indoor": 0, "basement": 0}
_STATE_LOCK = threading.Lock()


class _FakeESP32Handler(BaseHTTPRequestHandler):
    server_role_map: dict[int, str] = {}

    def do_GET(self) -> None:  # noqa: N802 — required by BaseHTTPRequestHandler
        if self.path != "/data":
            self.send_response(404)
            self.end_headers()
            return
        role = self.server_role_map.get(self.server.server_port, "outdoor")
        with _STATE_LOCK:
            payload = json.dumps(_STATE[role])
            _POLL_COUNTS[role] += 1
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(payload.encode())

    def log_message(self, *_args: object) -> None:
        return


def _start_fake_esp32(role: str) -> tuple[HTTPServer, int, threading.Thread]:
    server = HTTPServer(("127.0.0.1", 0), _FakeESP32Handler)
    port = server.server_address[1]
    _FakeESP32Handler.server_role_map[port] = role
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, port, thread


@pytest.fixture
def integration_client(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Iterator[TestClient]:
    # Reset shared state for the test.
    with _STATE_LOCK:
        for k in _POLL_COUNTS:
            _POLL_COUNTS[k] = 0

    outdoor_srv, outdoor_port, _ = _start_fake_esp32("outdoor")
    indoor_srv, indoor_port, _ = _start_fake_esp32("indoor")
    basement_srv, basement_port, _ = _start_fake_esp32("basement")

    db_path = tmp_path / "weather.db"
    cfg = tmp_path / "weather.toml"
    cfg.write_text(
        f"""
[server]
host = "127.0.0.1"
port = 8005
db_path = "{db_path}"

[logger]
interval_seconds = 1
http_timeout_seconds = 3

[cache]
ttl_seconds = 5

[[sensors]]
id = "outdoor"
role = "outdoor"
ip = "127.0.0.1:{outdoor_port}"
has_gps = true
has_light = true
online_threshold_seconds = 120
temp_offset_c = 0.0
fallback_altitude_m = 1609.3
fallback_lat = 39.7392
fallback_lon = -104.9903

[[sensors]]
id = "indoor"
role = "indoor"
ip = "127.0.0.1:{indoor_port}"
has_gps = false
has_light = false
online_threshold_seconds = 120
temp_offset_c = 0.0

[[sensors]]
id = "basement"
role = "indoor"
ip = "127.0.0.1:{basement_port}"
has_gps = false
has_light = false
online_threshold_seconds = 300
temp_offset_c = 0.0
"""
    )
    monkeypatch.setenv("WEATHER_CONFIG", str(cfg))

    from weather_server.main import create_app

    app = create_app()
    with TestClient(app) as tc:
        yield tc

    outdoor_srv.shutdown()
    indoor_srv.shutdown()
    basement_srv.shutdown()


def test_real_http_logger_writes_outdoor_row(integration_client: TestClient) -> None:
    # The lifespan startup fires the logger task; wait one tick.
    _wait_for(lambda: _POLL_COUNTS["outdoor"] >= 1, timeout=3.0)

    r = integration_client.get("/api/v1/current")
    assert r.status_code == 200
    body = r.json()
    outdoor = body["sensors"]["outdoor"]
    assert outdoor["online"] is True
    assert outdoor["raw"]["temperature_c"] == pytest.approx(18.5)
    assert outdoor["raw"]["pressure_pa"] == pytest.approx(84725.0)


def test_temperature_change_visible_in_current_within_one_interval(
    integration_client: TestClient,
) -> None:
    _wait_for(lambda: _POLL_COUNTS["outdoor"] >= 1, timeout=3.0)

    r1 = integration_client.get("/api/v1/current")
    t_before = r1.json()["sensors"]["outdoor"]["raw"]["temperature_c"]
    assert t_before == pytest.approx(18.5)

    # Simulate "the user walked outside and the temp dropped 3 degrees".
    with _STATE_LOCK:
        _STATE["outdoor"]["temperatureC"] = 15.5

    # Wait for the next logger tick (interval_seconds=1).
    initial = _POLL_COUNTS["outdoor"]
    _wait_for(lambda: _POLL_COUNTS["outdoor"] > initial, timeout=3.0)

    r2 = integration_client.get("/api/v1/current")
    t_after = r2.json()["sensors"]["outdoor"]["raw"]["temperature_c"]
    assert t_after == pytest.approx(15.5)


def test_indoor_concurrent_requests_collapse_to_one_upstream_poll(
    integration_client: TestClient,
) -> None:
    # Clear any indoor polls that happened during the test's first /current call.
    with _STATE_LOCK:
        _POLL_COUNTS["indoor"] = 0

    async def hit() -> int:
        loop = asyncio.get_event_loop()
        return (
            await loop.run_in_executor(None, integration_client.get, "/api/v1/current/indoor")
        ).status_code

    async def run() -> list[int]:
        return await asyncio.gather(*[hit() for _ in range(5)])

    results = asyncio.run(run())
    assert all(s == 200 for s in results)
    # TTL cache should dedupe the 5 near-simultaneous requests to 1 poll.
    with _STATE_LOCK:
        assert _POLL_COUNTS["indoor"] == 1, (
            f"expected 1 upstream poll under TTL cache, got {_POLL_COUNTS['indoor']}"
        )


def _wait_for(predicate, timeout: float = 3.0, interval: float = 0.05) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(interval)
    raise AssertionError(f"predicate did not become true within {timeout}s")
