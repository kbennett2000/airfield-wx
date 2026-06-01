"""External provider normalizers + dispatch (no network — http_get injected)."""

from __future__ import annotations

from typing import Any

import pytest

from weather_server.config import ExternalConfig
from weather_server.external import providers as p

# ── pure helpers ──────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("deg", "expected"),
    [(0, "N"), (90, "E"), (180, "S"), (270, "W"), (45, "NE"), (360, "N"), (348.75, "N")],
)
def test_cardinal_from_deg(deg: float, expected: str) -> None:
    assert p.cardinal_from_deg(deg) == expected


def test_cardinal_from_deg_none() -> None:
    assert p.cardinal_from_deg(None) is None


def test_haversine_known_distance() -> None:
    # Ponderosa Park area → nearest NWS KMNH was ~25.7 km in scoping.
    d = p.haversine_km(39.43326, -104.51888, 39.20, -104.62)
    assert 20.0 < d < 35.0


def test_assess_confidence() -> None:
    assert p.assess_confidence(10.0, 10.0) == "normal"
    assert p.assess_confidence(10.0, 2.0) == "low"  # diff 8 m/s, 80% relative
    assert p.assess_confidence(3.0, 1.0) == "normal"  # small absolute diff
    assert p.assess_confidence(None, 5.0) == "normal"
    assert p.assess_confidence(5.0, None) == "normal"


# ── open-meteo ────────────────────────────────────────────────────────────────

_OPEN_METEO_SAMPLE: dict[str, Any] = {
    "current": {
        "time": "2026-05-29T22:00",
        "wind_speed_10m": 5.41,
        "wind_direction_10m": 106,
        "wind_gusts_10m": 6.6,
        "cloud_cover": 100,
        "uv_index": 1.6,
        "precipitation": 0.0,
        "visibility": 36600.0,
    }
}


def test_normalize_open_meteo() -> None:
    obs = p.normalize_open_meteo(_OPEN_METEO_SAMPLE)
    assert obs is not None
    assert obs.provider == "open-meteo"
    assert obs.wind_speed_ms == pytest.approx(5.41)
    assert obs.wind_gust_ms == pytest.approx(6.6)
    assert obs.wind_direction_deg == pytest.approx(106)
    assert obs.cloud_cover_pct == pytest.approx(100)
    assert obs.uv_index == pytest.approx(1.6)
    assert obs.visibility_m == pytest.approx(36600.0)
    assert obs.observed_at is not None and obs.observed_at.tzinfo is not None


def test_normalize_open_meteo_garbage_returns_none() -> None:
    assert p.normalize_open_meteo({"no": "current"}) is None
    assert p.normalize_open_meteo("nonsense") is None


# ── NWS ───────────────────────────────────────────────────────────────────────

_NWS_SAMPLE: dict[str, Any] = {
    "properties": {
        "timestamp": "2026-05-29T21:00:00+00:00",
        "windSpeed": {"value": 18.0, "unitCode": "wmoUnit:km_h-1"},
        "windGust": {"value": 42.48, "unitCode": "wmoUnit:km_h-1"},
        "windDirection": {"value": 310, "unitCode": "wmoUnit:degree_(angle)"},
        "visibility": {"value": 16090, "unitCode": "wmoUnit:m"},
    }
}


def test_normalize_nws_converts_kmh_to_ms() -> None:
    obs = p.normalize_nws(_NWS_SAMPLE, "KBJC", 13.0)
    assert obs is not None
    assert obs.provider == "nws"
    assert obs.station_id == "KBJC"
    assert obs.distance_km == 13.0
    assert obs.wind_speed_ms == pytest.approx(18.0 / 3.6)
    assert obs.wind_gust_ms == pytest.approx(42.48 / 3.6)
    assert obs.wind_direction_deg == pytest.approx(310)
    assert obs.visibility_m == pytest.approx(16090)


def test_normalize_nws_handles_null_windspeed() -> None:
    sample = {"properties": {"windSpeed": {"value": None}, "windGust": {"value": 30.0}}}
    obs = p.normalize_nws(sample, "KBJC", None)
    assert obs is not None
    assert obs.wind_speed_ms is None
    assert obs.wind_gust_ms == pytest.approx(30.0 / 3.6)


# ── Weather Underground ───────────────────────────────────────────────────────

_WU_SAMPLE: dict[str, Any] = {
    "observations": [
        {
            "obsTimeUtc": "2026-05-29T22:05:00Z",
            "winddir": 200,
            "metric": {"windSpeed": 14.4, "windGust": 25.2, "precipTotal": 1.2},
        }
    ]
}


def test_normalize_wunderground() -> None:
    obs = p.normalize_wunderground(_WU_SAMPLE, "KXX00001")
    assert obs is not None
    assert obs.provider == "wunderground"
    assert obs.station_id == "KXX00001"
    assert obs.wind_speed_ms == pytest.approx(14.4 / 3.6)
    assert obs.wind_gust_ms == pytest.approx(25.2 / 3.6)
    assert obs.wind_direction_deg == pytest.approx(200)
    assert obs.precip_mm == pytest.approx(1.2)


def test_normalize_wunderground_empty_returns_none() -> None:
    assert p.normalize_wunderground({"observations": []}, "X") is None


# ── dispatch (fetch_external) ─────────────────────────────────────────────────


def _fake_http(payload: Any) -> p.HttpGetJson:
    def _get(url: str, headers: dict[str, str] | None, timeout: float) -> Any:
        return payload

    return _get


def test_fetch_external_open_meteo_online() -> None:
    cfg = ExternalConfig(enabled=True, provider="open-meteo")
    obs = p.fetch_external(cfg, 39.4, -104.5, http_get=_fake_http(_OPEN_METEO_SAMPLE))
    assert obs is not None and obs.provider == "open-meteo"


def test_fetch_external_offline_returns_none() -> None:
    cfg = ExternalConfig(enabled=True, provider="open-meteo")

    def boom(url: str, headers: dict[str, str] | None, timeout: float) -> Any:
        raise ConnectionError("no internet")

    assert p.fetch_external(cfg, 39.4, -104.5, http_get=boom) is None


def test_fetch_external_partial_null_payload_returns_none() -> None:
    cfg = ExternalConfig(enabled=True, provider="open-meteo")
    assert p.fetch_external(cfg, 39.4, -104.5, http_get=_fake_http({})) is None


def test_fetch_external_wunderground_uses_station_and_key() -> None:
    cfg = ExternalConfig(
        enabled=True, provider="wunderground", station_id="KXX00001", api_key="k"
    )
    obs = p.fetch_external(cfg, 39.4, -104.5, http_get=_fake_http(_WU_SAMPLE))
    assert obs is not None and obs.station_id == "KXX00001"


def test_wunderground_api_key_not_logged_on_failure(caplog: pytest.LogCaptureFixture) -> None:
    cfg = ExternalConfig(
        enabled=True, provider="wunderground", station_id="KX", api_key="SUPERSECRETKEY"
    )

    def boom(url: str, headers: dict[str, str] | None, timeout: float) -> Any:
        # Mimics requests/urllib3 embedding the full URL (with the key) in the error.
        raise ConnectionError(f"Max retries exceeded with url: {url}")

    import logging

    with caplog.at_level(logging.INFO):
        assert p.fetch_external(cfg, 39.0, -104.0, http_get=boom) is None
    assert "SUPERSECRETKEY" not in caplog.text
    assert "***" in caplog.text


def test_fetch_external_nws_with_configured_station() -> None:
    cfg = ExternalConfig(enabled=True, provider="nws", station_id="KBJC")
    obs = p.fetch_external(cfg, 39.4, -104.5, http_get=_fake_http(_NWS_SAMPLE))
    assert obs is not None and obs.station_id == "KBJC"
