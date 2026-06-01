"""Shared geo helpers."""

import pytest

from weather_server import geo


def test_haversine_known_distance() -> None:
    # ~same point: zero.
    assert geo.haversine_km(39.0, -104.0, 39.0, -104.0) == pytest.approx(0.0, abs=1e-9)
    # One degree of latitude is ~111.19 km along a meridian.
    assert geo.haversine_km(39.0, -104.0, 40.0, -104.0) == pytest.approx(111.19, abs=0.5)


def test_haversine_symmetric() -> None:
    a = geo.haversine_km(47.45, -122.31, 47.62, -122.35)
    b = geo.haversine_km(47.62, -122.35, 47.45, -122.31)
    assert a == pytest.approx(b)


def test_km_per_nm_constant() -> None:
    assert geo.KM_PER_NM == 1.852
