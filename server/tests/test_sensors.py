from pathlib import Path
from typing import Any

import pytest
import requests

from weather_server.config import SensorConfig
from weather_server.sensors import FixtureSensorSource, HttpSensorSource, make_source


def _write_fixtures(d: Path) -> None:
    (d / "outdoor.json").write_text(
        '[{"temperature_c": 1.0}, {"temperature_c": 2.0}, {"temperature_c": 3.0}]'
    )
    (d / "indoor.json").write_text('{"temperature_c": 22.0}')
    (d / "basement.json").write_text('{"temperature_c": 18.0, "offline": true}')


@pytest.fixture
def src(tmp_path: Path) -> FixtureSensorSource:
    _write_fixtures(tmp_path)
    return FixtureSensorSource(tmp_path)


async def test_outdoor_walks_and_loops(src: FixtureSensorSource) -> None:
    outdoor = SensorConfig(id="outdoor", role="outdoor", ip="x")
    temps = []
    for _ in range(7):
        payload = await src.poll(outdoor)
        assert payload is not None
        temps.append(payload["temperature_c"])
    assert temps == [1.0, 2.0, 3.0, 1.0, 2.0, 3.0, 1.0]


async def test_indoor_snapshot_returns_same_value(src: FixtureSensorSource) -> None:
    indoor = SensorConfig(id="indoor", role="indoor", ip="x")
    a = await src.poll(indoor)
    b = await src.poll(indoor)
    assert a == b == {"temperature_c": 22.0}


async def test_offline_flag_returns_none(src: FixtureSensorSource) -> None:
    basement = SensorConfig(id="basement", role="indoor", ip="x")
    assert await src.poll(basement) is None


async def test_missing_fixture_file_returns_none(src: FixtureSensorSource) -> None:
    ghost = SensorConfig(id="ghost", role="indoor", ip="x")
    assert await src.poll(ghost) is None


def test_all_outdoor_rows_returns_full_list(src: FixtureSensorSource) -> None:
    assert len(src.all_outdoor_rows()) == 3


def test_make_source_without_fixture_dir_returns_http_source() -> None:
    src = make_source(None, http_timeout_seconds=3.0)
    assert isinstance(src, HttpSensorSource)


def test_make_source_with_fixture_dir_returns_fixture_source(tmp_path: Path) -> None:
    _write_fixtures(tmp_path)
    src = make_source(str(tmp_path))
    assert isinstance(src, FixtureSensorSource)


# ─── HttpSensorSource ──────────────────────────────────────────────────────


class _FakeResponse:
    def __init__(self, text: str, status: int = 200) -> None:
        self.text = text
        self.status_code = status

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise requests.exceptions.HTTPError(f"status {self.status_code}")


async def test_http_source_parses_outdoor_wire_format(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sample = '{"temperatureC":20.5,"humidity":40.0,"pressure":805.0,"lux":12345,"ir":100,"visible":7000,"latitude":39.74,"longitude":-104.99,"altitude":1609.3,"speed":0.0,"course":0.0,"satellites":9,"tempOffset":0.0,"rssi":-60,"uptime":12345678,"freeHeap":160000}'  # noqa: E501
    captured: dict[str, Any] = {}

    def fake_get(url: str, timeout: float) -> _FakeResponse:
        captured["url"] = url
        captured["timeout"] = timeout
        return _FakeResponse(sample)

    monkeypatch.setattr(requests, "get", fake_get)

    src = HttpSensorSource(http_timeout_seconds=5.0)
    payload = await src.poll(
        SensorConfig(id="outdoor", role="outdoor", ip="192.168.1.60", has_gps=True)
    )
    assert captured["url"] == "http://192.168.1.60/data"
    assert captured["timeout"] == 5.0
    assert payload is not None
    assert payload["temperature_c"] == pytest.approx(20.5)
    assert payload["pressure_pa"] == pytest.approx(80500.0)
    assert payload["uptime_s"] == 12345
    assert payload["full_spectrum"] == 7100


async def test_http_source_returns_none_on_connection_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def boom(url: str, timeout: float) -> _FakeResponse:
        raise requests.exceptions.ConnectionError("network unreachable")

    monkeypatch.setattr(requests, "get", boom)
    src = HttpSensorSource()
    payload = await src.poll(SensorConfig(id="indoor", role="indoor", ip="10.0.0.1"))
    assert payload is None


async def test_http_source_returns_none_on_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def timeout(url: str, timeout: float) -> _FakeResponse:
        raise requests.exceptions.ConnectTimeout("took too long")

    monkeypatch.setattr(requests, "get", timeout)
    src = HttpSensorSource(http_timeout_seconds=0.1)
    assert await src.poll(SensorConfig(id="x", role="outdoor", ip="10.0.0.1")) is None


async def test_http_source_returns_none_on_non_200(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(requests, "get", lambda url, timeout: _FakeResponse("oops", status=500))
    src = HttpSensorSource()
    assert await src.poll(SensorConfig(id="x", role="outdoor", ip="10.0.0.1")) is None


async def test_http_source_handles_nan_response_via_adapter(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # BUG-08 path: nan tokens come through the wire and the source must
    # absorb them via wire_format.sanitize_nan_tokens.
    sample = '{"temperatureC":20.0,"humidity":nan,"pressure":805.0}'
    monkeypatch.setattr(requests, "get", lambda url, timeout: _FakeResponse(sample))
    src = HttpSensorSource()
    payload = await src.poll(SensorConfig(id="indoor", role="indoor", ip="10.0.0.1"))
    assert payload is not None
    assert payload["temperature_c"] == pytest.approx(20.0)
    assert "humidity_pct" not in payload  # nan was dropped


def test_http_source_all_outdoor_rows_is_empty() -> None:
    assert HttpSensorSource().all_outdoor_rows() == []
