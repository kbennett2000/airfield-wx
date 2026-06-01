"""Reading-bound derivations.

Includes the pressure-quadruple test from CLAUDE.md done criteria,
plus the additional Denver test case using realistic station pressure
that does produce sealevel ≈1023 hPa with the hypsometric formula.
"""

import math

import pytest

from weather_server.derivations import readings


def test_c_to_f_known_points() -> None:
    assert readings.c_to_f(0) == pytest.approx(32.0)
    assert readings.c_to_f(100) == pytest.approx(212.0)
    assert readings.c_to_f(-40) == pytest.approx(-40.0)


def test_apply_calibration_adds_offset() -> None:
    assert readings.apply_calibration(18.9, -0.5) == pytest.approx(18.4)


def test_dewpoint_magnus_round_numbers() -> None:
    # At 100% RH, dewpoint == air temperature.
    assert readings.dewpoint_c(20.0, 100.0) == pytest.approx(20.0, abs=0.01)
    # Sanity: dewpoint < air temp when RH < 100%.
    dp = readings.dewpoint_c(20.0, 50.0)
    assert dp is not None and dp < 20.0


def test_dewpoint_invalid_humidity_returns_none() -> None:
    assert readings.dewpoint_c(20.0, 0.0) is None
    assert readings.dewpoint_c(20.0, 101.0) is None


def test_absolute_humidity_positive_for_normal_air() -> None:
    ah = readings.absolute_humidity_g_m3(20.0, 50.0)
    assert ah is not None and 8.0 < ah < 10.0


def test_pressure_pa_to_hpa() -> None:
    assert readings.pressure_pa_to_hpa(80443) == pytest.approx(804.43)


def test_pressure_hpa_to_inhg_doc_example() -> None:
    assert readings.pressure_hpa_to_inhg(804.43) == pytest.approx(23.75, abs=0.005)


def test_pressure_station_to_sealevel_formula() -> None:
    # Hypsometric formula consistency check.
    station = 1013.25
    altitude = 0.0
    temp = 15.0
    assert readings.pressure_station_to_sealevel_hpa(station, altitude, temp) == pytest.approx(
        1013.25
    )


def test_pressure_quadruple_matches_doc_example() -> None:
    """The example in 02-api-design.md uses raw.pressure_pa = 84725 with
    altitude_m = 1609.3 and calibrated temp = 18.4°C, producing
    station 847.25 hPa / 25.02 inHg and sealevel 1023.0 hPa / 30.21 inHg.
    This test is the doc-conformance check: if the spec example changes,
    this should change with it."""
    derived = readings.derive_reading(
        {
            "temperature_c": 18.9,
            "humidity_pct": 42.1,
            "pressure_pa": 84725,
            "altitude_m": 1609.3,
        },
        temp_offset_c=-0.5,
        fallback_altitude_m=1609.3,
    )
    assert derived["pressure_station_hpa"] == pytest.approx(847.25)
    assert derived["pressure_station_inhg"] == pytest.approx(25.02, abs=0.01)
    assert derived["pressure_sealevel_hpa"] == pytest.approx(1023.0, abs=1.0)
    assert derived["pressure_sealevel_inhg"] == pytest.approx(30.21, abs=0.05)


def test_pressure_quadruple_hypsometric_formula_check() -> None:
    """Independent math check on the hypsometric formula, using a
    different station pressure than the doc example. Pins the formula's
    output to ~971 hPa / 28.69 inHg for pressure_pa=80443 at Denver
    elevation. Not a doc test — pure math correctness."""
    derived = readings.derive_reading(
        {
            "temperature_c": 18.4,
            "humidity_pct": 42.0,
            "pressure_pa": 80443,
            "altitude_m": 1609.3,
        },
        temp_offset_c=0.0,
    )
    assert derived["pressure_station_hpa"] == pytest.approx(804.43)
    assert derived["pressure_station_inhg"] == pytest.approx(23.75, abs=0.01)
    assert derived["pressure_sealevel_hpa"] == pytest.approx(971.4, abs=1.0)
    assert derived["pressure_sealevel_inhg"] == pytest.approx(28.69, abs=0.05)


def test_pressure_sealevel_falls_back_to_configured_altitude() -> None:
    derived = readings.derive_reading(
        {"temperature_c": 22.0, "humidity_pct": 40.0, "pressure_pa": 80520},
        fallback_altitude_m=1609.3,
    )
    assert derived["pressure_sealevel_hpa"] is not None


def test_pressure_sealevel_none_when_no_altitude_anywhere() -> None:
    derived = readings.derive_reading(
        {"temperature_c": 22.0, "humidity_pct": 40.0, "pressure_pa": 80520},
    )
    assert derived["pressure_sealevel_hpa"] is None
    assert derived["pressure_sealevel_inhg"] is None
    assert derived["pressure_station_hpa"] is not None


def test_calibration_offset_propagates_to_derived_temp() -> None:
    derived = readings.derive_reading(
        {"temperature_c": 20.0, "humidity_pct": 50.0},
        temp_offset_c=-0.5,
    )
    assert derived["temperature_c"] == pytest.approx(19.5)
    assert derived["temperature_f"] == pytest.approx(readings.c_to_f(19.5))


def test_missing_inputs_skip_outputs() -> None:
    derived = readings.derive_reading({})
    assert derived == {}


def test_heat_index_below_gate_returns_air_temp() -> None:
    # Cool air: heat index undefined, function returns the input temperature.
    assert readings.heat_index_f(75.0, 90.0) == pytest.approx(75.0)
    # Warm but dry enough that the NWS simple-formula gate rejects: also returns air temp.
    assert readings.heat_index_f(80.0, 40.0) == pytest.approx(80.0)


def test_heat_index_warm_humid_matches_nws_table() -> None:
    # NWS heat-index table: 90°F + 70% RH → ~105°F.
    assert readings.heat_index_f(90.0, 70.0) == pytest.approx(105.3, abs=1.0)


def test_heat_index_high_humidity_adjustment_applies() -> None:
    # 85°F + 90% RH falls in the R>85, 80≤T≤87 adjustment band. NWS table ≈101-102°F.
    assert readings.heat_index_f(85.0, 90.0) == pytest.approx(101.8, abs=1.5)


def test_heat_index_invalid_humidity_returns_air_temp() -> None:
    assert readings.heat_index_f(95.0, 0.0) == pytest.approx(95.0)
    assert readings.heat_index_f(95.0, 101.0) == pytest.approx(95.0)


def test_derive_reading_includes_feels_like_pair() -> None:
    # Hot + humid: feels-like exceeds air temp.
    derived = readings.derive_reading(
        {"temperature_c": 32.222, "humidity_pct": 70.0},  # 32.222°C ≈ 90°F
    )
    assert derived["feels_like_f"] == pytest.approx(105.3, abs=1.0)
    # °C field is the °F value converted back, not the input temperature.
    assert derived["feels_like_c"] == pytest.approx(
        (derived["feels_like_f"] - 32.0) * 5.0 / 9.0
    )


def test_derive_reading_feels_like_equals_air_temp_in_mild_conditions() -> None:
    # 20°C / 50% RH is well below the heat-index regime — feels-like collapses to air temp.
    derived = readings.derive_reading(
        {"temperature_c": 20.0, "humidity_pct": 50.0},
    )
    assert derived["feels_like_c"] == pytest.approx(20.0, abs=0.01)
    assert derived["feels_like_f"] == pytest.approx(derived["temperature_f"])


def test_derive_reading_omits_feels_like_when_humidity_missing() -> None:
    derived = readings.derive_reading({"temperature_c": 25.0})
    assert "feels_like_f" not in derived
    assert "feels_like_c" not in derived


def test_wet_bulb_stull_reference() -> None:
    # Stull (2011) worked example: 20°C / 50% RH → 13.7°C.
    assert readings.wet_bulb_c(20.0, 50.0) == pytest.approx(13.70, abs=0.05)


def test_wet_bulb_equals_air_temp_at_saturation() -> None:
    assert readings.wet_bulb_c(20.0, 100.0) == pytest.approx(20.0, abs=0.1)


def test_wet_bulb_between_dewpoint_and_air_temp() -> None:
    dp = readings.dewpoint_c(25.0, 50.0)
    wb = readings.wet_bulb_c(25.0, 50.0)
    assert dp is not None and wb is not None
    assert dp <= wb <= 25.0


def test_wet_bulb_invalid_humidity_none() -> None:
    assert readings.wet_bulb_c(20.0, 0.0) is None


def test_saturation_vapor_pressure_reference() -> None:
    # es(25°C) ≈ 31.6 hPa.
    assert readings.saturation_vapor_pressure_hpa(25.0) == pytest.approx(31.6, abs=0.2)


def test_vapor_pressure_deficit_reference() -> None:
    # 25°C / 50% RH → VPD ≈ 1.58 kPa.
    assert readings.vapor_pressure_deficit_kpa(25.0, 50.0) == pytest.approx(1.58, abs=0.02)


def test_vapor_pressure_zero_at_saturation() -> None:
    assert readings.vapor_pressure_deficit_kpa(25.0, 100.0) == pytest.approx(0.0, abs=1e-9)


def test_mixing_and_specific_humidity_reference() -> None:
    assert readings.mixing_ratio_g_kg(25.0, 50.0, 101325) == pytest.approx(9.86, abs=0.05)
    q = readings.specific_humidity_g_kg(25.0, 50.0, 101325)
    assert q is not None and q < 9.86  # specific humidity < mixing ratio


def test_air_density_standard_conditions() -> None:
    # Dry air at 15°C and 101325 Pa → 1.225 kg/m³.
    assert readings.air_density_kg_m3(15.0, 0.001, 101325) == pytest.approx(1.225, abs=0.001)


def test_air_density_moist_is_lower_than_dry() -> None:
    dry = readings.air_density_kg_m3(25.0, 0.001, 101325)
    moist = readings.air_density_kg_m3(25.0, 80.0, 101325)
    assert dry is not None and moist is not None and moist < dry


def test_pressure_altitude_zero_at_standard() -> None:
    assert readings.pressure_altitude_m(101325) == pytest.approx(0.0, abs=1.0)


def test_density_altitude_zero_at_standard_density() -> None:
    assert readings.density_altitude_m(1.225) == pytest.approx(0.0, abs=1.0)


def test_humidex_warm_humid_raises_temp() -> None:
    assert readings.humidex_c(30.0, 20.0) == pytest.approx(37.6, abs=0.5)


def test_humidex_cool_returns_air_temp() -> None:
    assert readings.humidex_c(10.0, 5.0) == pytest.approx(10.0)


def test_frost_point_below_freezing() -> None:
    fp = readings.frost_point_c(-10.0, 60.0)
    assert fp is not None and fp < -10.0  # frost point below air temp when subsaturated


def test_cloud_base_from_spread() -> None:
    assert readings.cloud_base_m(25.0, 10.0) == pytest.approx(1875.0)
    # Saturated/fog: spread <= 0 clamps to ground level.
    assert readings.cloud_base_m(10.0, 12.0) == 0.0


def test_derive_reading_includes_extended_thermo() -> None:
    derived = readings.derive_reading(
        {"temperature_c": 25.0, "humidity_pct": 50.0, "pressure_pa": 101325},
    )
    for key in (
        "wet_bulb_c",
        "wet_bulb_f",
        "vapor_pressure_deficit_kpa",
        "mixing_ratio_g_kg",
        "specific_humidity_g_kg",
        "air_density_kg_m3",
        "pressure_altitude_ft",
        "density_altitude_ft",
        "humidex_c",
        "cloud_base_m",
        "frost_point_c",
    ):
        assert key in derived and derived[key] is not None


def test_extended_thermo_skips_pressure_fields_without_pressure() -> None:
    derived = readings.derive_reading({"temperature_c": 25.0, "humidity_pct": 50.0})
    assert "wet_bulb_c" in derived  # humidity-only fields present
    assert "mixing_ratio_g_kg" not in derived  # pressure-dependent fields absent
    assert "air_density_kg_m3" not in derived


def test_extended_thermo_absent_without_humidity() -> None:
    derived = readings.derive_reading({"temperature_c": 25.0, "pressure_pa": 101325})
    assert "wet_bulb_c" not in derived
    assert "vapor_pressure_hpa" not in derived


def test_map_raw_maps_full_spectrum_to_full() -> None:
    raw = readings.map_raw(
        {
            "temperature_c": 18.4,
            "humidity_pct": 42.1,
            "pressure_pa": 80443,
            "lux": 12450.0,
            "ir": 230,
            "visible": 8200,
            "full_spectrum": 8430,
        }
    )
    assert raw["full"] == 8430
    assert "full_spectrum" not in raw
    assert raw["lux"] == 12450.0
    assert math.isclose(raw["pressure_pa"], 80443)
