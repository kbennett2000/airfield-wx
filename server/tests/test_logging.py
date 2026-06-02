"""History logging is opt-in, OFF by default (Cycle 13).

Default (off): no logger task spawned, nothing written, /history + /summary
404 "logging disabled", but /current and every other live endpoint work via
the read-time live-poll path. Enabled: today's behavior (logger runs, /history
+ /summary serve data).
"""

from __future__ import annotations

from fastapi.testclient import TestClient

# ── default: logging OFF ──────────────────────────────────────────────────────


def test_no_logger_tasks_spawned_by_default(client: TestClient) -> None:
    assert client.app.state.logger_task is None
    assert client.app.state.wind_logger_task is None


def test_nothing_written_when_disabled(client: TestClient) -> None:
    # No logger loop ⇒ the readings table stays empty (stateless appliance).
    client.get("/api/v1/current")  # exercise a read; it must not write
    count = client.app.state.db.execute("SELECT COUNT(*) FROM outdoor_readings").fetchone()[0]
    assert count == 0


def test_history_404_logging_disabled(client: TestClient) -> None:
    r = client.get("/api/v1/history/outdoor?hours=24")
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "logging_disabled"


def test_summary_404_logging_disabled(client: TestClient) -> None:
    r = client.get("/api/v1/summary/outdoor")
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "logging_disabled"


def test_current_complete_when_logging_disabled(client: TestClient) -> None:
    # The live instrument is fully functional with logging off — the outdoor
    # reading comes from a live cached poll, not the (empty) log.
    body = client.get("/api/v1/current").json()
    assert "outdoor" in body["sensors"]
    assert body["sensors"]["outdoor"]["online"] is True
    assert body["astronomy"] is not None


def test_other_endpoints_unaffected_when_disabled(client: TestClient) -> None:
    for path in (
        "/api/v1/current/outdoor",
        "/api/v1/airport",
        "/api/v1/astronomy",
        "/api/v1/external",
        "/api/v1/sensors",
        "/api/v1/health",
        "/api/v1/branding",
    ):
        assert client.get(path).status_code == 200, path


# ── enabled: today's behavior ─────────────────────────────────────────────────


def test_logger_task_spawned_when_enabled(logging_client: TestClient) -> None:
    assert logging_client.app.state.logger_task is not None


def test_history_and_summary_served_when_enabled(logging_client: TestClient) -> None:
    assert logging_client.get("/api/v1/history/outdoor?hours=24").status_code == 200
    assert logging_client.get("/api/v1/summary/outdoor").status_code == 200


# ── toggle isolation: /current's outdoor block is present in BOTH states ──────


def test_current_outdoor_present_in_both_states(
    client: TestClient, logging_client: TestClient
) -> None:
    off = client.get("/api/v1/current").json()
    on = logging_client.get("/api/v1/current").json()
    assert "outdoor" in off["sensors"] and "outdoor" in on["sensors"]
