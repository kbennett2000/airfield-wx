import pytest

from weather_server.derivations import location


def test_altitude_m_to_ft() -> None:
    assert location.altitude_m_to_ft(1609.3) == pytest.approx(5279.86, abs=0.1)
    assert location.altitude_m_to_ft(0) == 0


def test_dms_denver() -> None:
    s = location.decimal_to_dms(39.7392, -104.9903)
    assert "N" in s and "W" in s
    assert s.startswith("39°44")


def test_dms_southern_hemisphere() -> None:
    s = location.decimal_to_dms(-33.8688, 151.2093)
    assert "S" in s and "E" in s
    assert s.startswith("33°52")


def test_maidenhead_denver() -> None:
    # 39.7392, -104.9903 → DM79mr.
    assert location.maidenhead(39.7392, -104.9903) == "DM79mr"


def test_maidenhead_london() -> None:
    # London (51.5074, -0.1278) → IO91wm
    assert location.maidenhead(51.5074, -0.1278) == "IO91wm"


def test_maidenhead_4char_precision() -> None:
    assert location.maidenhead(39.7392, -104.9903, precision=2) == "DM79"


def test_maidenhead_rejects_invalid() -> None:
    with pytest.raises(ValueError):
        location.maidenhead(95.0, 0.0)
