"""End-to-end integration test for Phase 2 real polling.

Spins up a fake-ESP32 HTTP server on a localhost port in a background
thread, points the weather server's [[sensors]] entries at it (no
fixture_dir), and asserts:

- The logger task writes rows fetched via real HTTP into SQLite.
- /api/v1/current reflects current upstream values.
- Changing the upstream value is visible in /current on the next tick
  (the "I walked outside and the temp dropped" verification).
- /api/v1/current/<aux> uses the TTL cache: concurrent requests for a
  non-outdoor (on-demand) station produce exactly one upstream poll.
  (`aux` is a generic non-outdoor station exercising the retained
  multi-station / TTL-cache machinery — ADR-0007 removed the indoor
  and basement instances, not the mechanism.)

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

# Real HTTP + wall-clock timing (a background logger tick must land and the row
# must be written before assertions hold). Deselected from the default fast
# suite; run explicitly via `make test-integration` / `make test-all`.
pytestmark = pytest.mark.integration

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
    "aux": {
        "temperatureC": 22.4,
        "humidity": 38.7,
        "pressure": 805.2,
    },
}
_POLL_COUNTS: dict[str, int] = {"outdoor": 0, "aux": 0}
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
    aux_srv, aux_port, _ = _start_fake_esp32("aux")

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
id = "aux"
role = "aux"
ip = "127.0.0.1:{aux_port}"
has_gps = false
has_light = false
online_threshold_seconds = 120
temp_offset_c = 0.0
"""
    )
    monkeypatch.setenv("WEATHER_CONFIG", str(cfg))

    from weather_server.main import create_app

    app = create_app()
    with TestClient(app) as tc:
        yield tc

    outdoor_srv.shutdown()
    aux_srv.shutdown()


def test_real_http_logger_writes_outdoor_row(integration_client: TestClient) -> None:
    # The lifespan startup fires the logger task. Wait until the row has been
    # polled AND written (visible in /current), not merely polled — those are
    # separate events and racing on the first alone is the old flake.
    outdoor = _wait_for_outdoor(integration_client)
    assert outdoor["online"] is True
    assert outdoor["raw"]["temperature_c"] == pytest.approx(18.5)
    assert outdoor["raw"]["pressure_pa"] == pytest.approx(84725.0)


def test_temperature_change_visible_in_current_within_one_interval(
    integration_client: TestClient,
) -> None:
    outdoor = _wait_for_outdoor(integration_client)
    assert outdoor["raw"]["temperature_c"] == pytest.approx(18.5)

    # Simulate "the user walked outside and the temp dropped 3 degrees".
    with _STATE_LOCK:
        _STATE["outdoor"]["temperatureC"] = 15.5

    # Wait until the change is observable in /current (next logger tick + write).
    def _temp_changed() -> bool:
        current = _current_outdoor(integration_client)
        return current is not None and current["raw"]["temperature_c"] == pytest.approx(15.5)

    _wait_for(_temp_changed)
    after = _current_outdoor(integration_client)
    assert after is not None
    assert after["raw"]["temperature_c"] == pytest.approx(15.5)


def test_aux_concurrent_requests_collapse_to_one_upstream_poll(
    integration_client: TestClient,
) -> None:
    # Clear any aux polls that happened during the test's first /current call.
    with _STATE_LOCK:
        _POLL_COUNTS["aux"] = 0

    async def hit() -> int:
        loop = asyncio.get_event_loop()
        return (
            await loop.run_in_executor(None, integration_client.get, "/api/v1/current/aux")
        ).status_code

    async def run() -> list[int]:
        return await asyncio.gather(*[hit() for _ in range(5)])

    results = asyncio.run(run())
    assert all(s == 200 for s in results)
    # TTL cache should dedupe the 5 near-simultaneous requests to 1 poll.
    with _STATE_LOCK:
        assert _POLL_COUNTS["aux"] == 1, (
            f"expected 1 upstream poll under TTL cache, got {_POLL_COUNTS['aux']}"
        )


def _current_outdoor(client: TestClient) -> dict | None:
    """The outdoor sensor block from /current, or None if not present yet."""
    body = client.get("/api/v1/current").json()
    return body["sensors"].get("outdoor")


def _wait_for_outdoor(client: TestClient, timeout: float = 15.0) -> dict:
    """Wait until the outdoor reading has been polled AND written through to
    /current. Returns the outdoor block."""
    result: dict | None = None

    def _ready() -> bool:
        nonlocal result
        result = _current_outdoor(client)
        return result is not None

    _wait_for(_ready, timeout=timeout)
    assert result is not None
    return result


def _wait_for(predicate, timeout: float = 15.0, interval: float = 0.05) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(interval)
    raise AssertionError(f"predicate did not become true within {timeout}s")
