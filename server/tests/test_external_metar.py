"""METAR external provider — parsing, derivations, discovery, and the
load-bearing fail-soft contract (any failure → None, nothing raises)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from weather_server.config import ExternalConfig
from weather_server.external import providers as p

FIXTURES = Path(__file__).parent / "fixtures"


def _load(name: str) -> Any:
    return json.loads((FIXTURES / name).read_text())


@pytest.fixture(autouse=True)
def _clear_station_cache() -> None:
    p._reset_station_cache()


# ── fake injected HTTP ──────────────────────────────────────────────────────


def _const_http(value: Any):  # type: ignore[no-untyped-def]
    def http_get(url: str, headers: dict[str, str] | None, timeout: float) -> Any:
        return value
    return http_get


def _raising_http(exc: BaseException):  # type: ignore[no-untyped-def]
    def http_get(url: str, headers: dict[str, str] | None, timeout: float) -> Any:
        raise exc
    return http_get


def _routed_http(bbox: Any = None, ids: Any = None):  # type: ignore[no-untyped-def]
    def http_get(url: str, headers: dict[str, str] | None, timeout: float) -> Any:
        if "bbox=" in url:
            if bbox is None:
                raise AssertionError("unexpected bbox call")
            return bbox
        if ids is None:
            raise AssertionError("unexpected ids call")
        return ids
    return http_get


# ── live-fixture parse ──────────────────────────────────────────────────────


def test_normalize_live_ksea_fixture() -> None:
    obs = p.normalize_metar(_load("metar_ksea.json"), 47.45, -122.31)
    assert obs is not None
    assert obs.provider == "metar"
    assert obs.station_id == "KSEA"
    assert obs.observed_at is not None and obs.observed_at.tzinfo is not None
    assert obs.temp_c == pytest.approx(15.6)
    assert obs.dewpoint_c == pytest.approx(5.0)
    assert obs.visibility_sm == pytest.approx(10.0)
    assert obs.altimeter_hpa == pytest.approx(1023.1)
    assert obs.altimeter_inhg == pytest.approx(30.21, abs=0.01)
    assert obs.ceiling_ft_agl is None  # SCT does not form a ceiling
    assert obs.sky_layers is not None
    assert [(s.cover, s.base_ft_agl) for s in obs.sky_layers] == [("SCT", 25000)]
    assert obs.flight_category == "VFR"
    assert obs.metar_raw and obs.metar_raw.startswith("METAR KSEA")
    assert obs.distance_km is not None and obs.distance_km < 2.0


# ── ceiling rules ───────────────────────────────────────────────────────────


def _layers(*pairs: tuple[str, int | None]) -> list[p.MetarSkyLayer]:
    return [p.MetarSkyLayer(cover=c, base_ft_agl=b) for c, b in pairs]


def test_ceiling_lowest_bkn_ovc() -> None:
    assert p._ceiling_ft(_layers(("BKN", 800), ("OVC", 1200))) == 800


def test_ceiling_excludes_few_sct() -> None:
    assert p._ceiling_ft(_layers(("SCT", 1500), ("FEW", 3000))) is None


def test_ceiling_vertical_visibility_counts() -> None:
    assert p._ceiling_ft(_layers(("OVX", 300))) == 300


def test_ceiling_min_regardless_of_order() -> None:
    assert p._ceiling_ft(_layers(("OVC", 1200), ("BKN", 800))) == 800


def test_ceiling_clear_is_none() -> None:
    assert p._ceiling_ft([]) is None


# ── visibility parsing ──────────────────────────────────────────────────────


def test_visibility_ten_plus() -> None:
    assert p._parse_visibility_sm("10+") == pytest.approx(10.0)


def test_visibility_numeric_string() -> None:
    assert p._parse_visibility_sm("3") == pytest.approx(3.0)


def test_visibility_numeric() -> None:
    assert p._parse_visibility_sm(6) == pytest.approx(6.0)


def test_visibility_fraction() -> None:
    assert p._parse_visibility_sm("1 1/2") == pytest.approx(1.5)


def test_visibility_unparseable_none() -> None:
    assert p._parse_visibility_sm("abc") is None
    assert p._parse_visibility_sm(None) is None


# ── altimeter conversion (via normalize) ────────────────────────────────────


def test_altimeter_hpa_to_inhg_anchor() -> None:
    payload = [{"icaoId": "XXXX", "altim": 1013.25, "lat": 0.0, "lon": 0.0}]
    obs = p.normalize_metar(payload, 0.0, 0.0)
    assert obs is not None
    assert obs.altimeter_inhg == pytest.approx(29.92, abs=0.01)


# ── flight category (pure, worse-of) ────────────────────────────────────────


@pytest.mark.parametrize(
    "ceiling,expected",
    [(400, "LIFR"), (700, "IFR"), (1000, "MVFR"), (3000, "MVFR"), (3500, "VFR"), (None, "VFR")],
)
def test_category_by_ceiling(ceiling: int | None, expected: str) -> None:
    assert p._compute_flight_category(ceiling, 10.0) == expected


@pytest.mark.parametrize(
    "vis,expected",
    [(0.5, "LIFR"), (2.0, "IFR"), (4.0, "MVFR"), (6.0, "VFR")],
)
def test_category_by_visibility(vis: float, expected: str) -> None:
    assert p._compute_flight_category(None, vis) == expected


def test_category_worse_of() -> None:
    assert p._compute_flight_category(5000, 2.0) == "IFR"  # vis worse
    assert p._compute_flight_category(400, 10.0) == "LIFR"  # ceiling worse
    assert p._compute_flight_category(None, 10.0) == "VFR"


def test_normalize_uses_api_flight_category_when_present() -> None:
    payload = [{"icaoId": "XXXX", "fltCat": "IFR", "visib": "10+", "clouds": [],
                "lat": 0.0, "lon": 0.0}]
    obs = p.normalize_metar(payload, 0.0, 0.0)
    assert obs is not None and obs.flight_category == "IFR"


# ── nearest-station discovery + override ────────────────────────────────────


def test_discovery_picks_nearest_by_haversine() -> None:
    cfg = ExternalConfig(enabled=True, provider="metar")
    # Query right next to KSEA; the bbox fixture also has KBFI/KRNT farther off.
    obs = p.fetch_external(cfg, 47.4447, -122.3144,
                           http_get=_routed_http(bbox=_load("metar_bbox_seattle.json")))
    assert obs is not None and obs.station_id == "KSEA"
    assert obs.distance_km is not None


def test_config_override_skips_discovery() -> None:
    cfg = ExternalConfig(enabled=True, provider="metar", station_id="KSEA")
    # ids path only; bbox would raise.
    obs = p.fetch_external(cfg, 39.0, -100.0,
                           http_get=_routed_http(ids=_load("metar_ksea.json")))
    assert obs is not None and obs.station_id == "KSEA"


# ── FAIL-SOFT (load-bearing): every failure → None, nothing raises ──────────


def _metar_cfg(**kw: Any) -> ExternalConfig:
    return ExternalConfig(enabled=True, provider="metar", **kw)


def test_failsoft_connection_error() -> None:
    cfg = _metar_cfg(station_id="KSEA")
    http = _raising_http(ConnectionError("down"))
    assert p.fetch_external(cfg, 47.0, -122.0, http_get=http) is None


def test_failsoft_timeout() -> None:
    cfg = _metar_cfg(station_id="KSEA")
    http = _raising_http(TimeoutError("slow"))
    assert p.fetch_external(cfg, 47.0, -122.0, http_get=http) is None


def test_failsoft_malformed_dict() -> None:
    cfg = _metar_cfg(station_id="KSEA")
    assert p.fetch_external(cfg, 47.0, -122.0, http_get=_const_http({"not": "a list"})) is None


def test_failsoft_malformed_string() -> None:
    cfg = _metar_cfg(station_id="KSEA")
    assert p.fetch_external(cfg, 47.0, -122.0, http_get=_const_http("garbage")) is None


def test_failsoft_empty_metar_list() -> None:
    cfg = _metar_cfg(station_id="KSEA")
    assert p.fetch_external(cfg, 47.0, -122.0, http_get=_const_http([])) is None


def test_failsoft_empty_bbox_discovery() -> None:
    cfg = _metar_cfg()  # no station_id → discovery
    assert p.fetch_external(cfg, 47.0, -122.0, http_get=_routed_http(bbox=[])) is None


def test_normalize_rejects_non_list() -> None:
    assert p.normalize_metar({"icaoId": "X"}, 0.0, 0.0) is None
    assert p.normalize_metar(None, 0.0, 0.0) is None
