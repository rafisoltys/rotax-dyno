"""Unit tests for the calibration engine."""

import pytest

from rotax_dyno_daq.calibration.engine import (
    CalibrationEngine,
    LinearCalibration,
    LookupTableCalibration,
    ValidationResult,
)
from rotax_dyno_daq.core.enums import CalibrationType, SampleValidity
from rotax_dyno_daq.core.models import (
    CalibrationProfile,
    LinearCalibrationParams,
    LookupTableParams,
)


class TestLinearCalibration:
    """Tests for LinearCalibration.convert()."""

    def test_identity_calibration(self):
        """slope=1, offset=0 should return the raw value unchanged."""
        cal = LinearCalibration(slope=1.0, offset=0.0)
        assert cal.convert(2.5) == 2.5

    def test_slope_only(self):
        """slope=2, offset=0 should double the raw value."""
        cal = LinearCalibration(slope=2.0, offset=0.0)
        assert cal.convert(3.0) == 6.0

    def test_offset_only(self):
        """slope=1, offset=10 should add 10 to the raw value."""
        cal = LinearCalibration(slope=1.0, offset=10.0)
        assert cal.convert(5.0) == 15.0

    def test_slope_and_offset(self):
        """slope=2.5, offset=-1.0 should compute 2.5*raw - 1.0."""
        cal = LinearCalibration(slope=2.5, offset=-1.0)
        assert cal.convert(4.0) == pytest.approx(9.0)

    def test_negative_slope(self):
        """Negative slope should invert the relationship."""
        cal = LinearCalibration(slope=-1.0, offset=5.0)
        assert cal.convert(3.0) == 2.0

    def test_zero_input(self):
        """Zero input should return just the offset."""
        cal = LinearCalibration(slope=3.0, offset=7.0)
        assert cal.convert(0.0) == 7.0


class TestLookupTableCalibration:
    """Tests for LookupTableCalibration.convert()."""

    def test_exact_point_match(self):
        """Raw value exactly at a defined point should return that point's value."""
        cal = LookupTableCalibration(points=[(0.0, 0.0), (5.0, 100.0)])
        value, out_of_range = cal.convert(0.0)
        assert value == 0.0
        assert out_of_range is False

    def test_exact_upper_point_match(self):
        """Raw value exactly at the upper point should return that value."""
        cal = LookupTableCalibration(points=[(0.0, 0.0), (5.0, 100.0)])
        value, out_of_range = cal.convert(5.0)
        assert value == 100.0
        assert out_of_range is False

    def test_midpoint_interpolation(self):
        """Midpoint between two points should interpolate linearly."""
        cal = LookupTableCalibration(points=[(0.0, 0.0), (10.0, 100.0)])
        value, out_of_range = cal.convert(5.0)
        assert value == pytest.approx(50.0)
        assert out_of_range is False

    def test_quarter_point_interpolation(self):
        """Quarter point should interpolate to 25%."""
        cal = LookupTableCalibration(points=[(0.0, 0.0), (4.0, 100.0)])
        value, out_of_range = cal.convert(1.0)
        assert value == pytest.approx(25.0)
        assert out_of_range is False

    def test_multi_segment_interpolation(self):
        """Interpolation across multiple segments."""
        cal = LookupTableCalibration(
            points=[(0.0, 0.0), (1.0, 10.0), (2.0, 50.0), (3.0, 100.0)]
        )
        # Between first two points
        value, _ = cal.convert(0.5)
        assert value == pytest.approx(5.0)

        # Between second and third points
        value, _ = cal.convert(1.5)
        assert value == pytest.approx(30.0)

        # Between third and fourth points
        value, _ = cal.convert(2.5)
        assert value == pytest.approx(75.0)

    def test_clamp_below_minimum(self):
        """Raw value below minimum should clamp to first point's value."""
        cal = LookupTableCalibration(points=[(1.0, 10.0), (5.0, 100.0)])
        value, out_of_range = cal.convert(0.0)
        assert value == 10.0
        assert out_of_range is True

    def test_clamp_above_maximum(self):
        """Raw value above maximum should clamp to last point's value."""
        cal = LookupTableCalibration(points=[(1.0, 10.0), (5.0, 100.0)])
        value, out_of_range = cal.convert(10.0)
        assert value == 100.0
        assert out_of_range is True

    def test_unsorted_points_are_sorted(self):
        """Points provided out of order should be sorted by voltage."""
        cal = LookupTableCalibration(points=[(5.0, 100.0), (0.0, 0.0), (2.5, 50.0)])
        value, out_of_range = cal.convert(1.25)
        assert value == pytest.approx(25.0)
        assert out_of_range is False

    def test_non_linear_mapping(self):
        """Non-linear mapping (different slopes per segment)."""
        cal = LookupTableCalibration(
            points=[(0.0, 0.0), (1.0, 100.0), (2.0, 150.0)]
        )
        # First segment: slope = 100/1 = 100
        value, _ = cal.convert(0.5)
        assert value == pytest.approx(50.0)

        # Second segment: slope = 50/1 = 50
        value, _ = cal.convert(1.5)
        assert value == pytest.approx(125.0)


class TestCalibrationEngine:
    """Tests for CalibrationEngine.apply()."""

    def test_uncalibrated_channel(self):
        """Channel with no profile should return UNCALIBRATED."""
        engine = CalibrationEngine()
        sample = engine.apply("unknown_channel", 2.5, timestamp_ms=100.0)
        assert sample.validity == SampleValidity.UNCALIBRATED
        assert sample.calibrated_value == 2.5
        assert sample.unit == "V"

    def test_linear_calibration_valid(self):
        """Valid voltage with linear calibration should return VALID sample."""
        engine = CalibrationEngine()
        profile = CalibrationProfile(
            calibration_type=CalibrationType.LINEAR,
            unit_label="bar",
            min_valid_voltage=0.5,
            max_valid_voltage=4.5,
            linear_params=LinearCalibrationParams(slope=2.0, offset=-1.0),
        )
        engine.update_profile("oil_pressure", profile)

        sample = engine.apply("oil_pressure", 2.5, timestamp_ms=50.0)
        assert sample.validity == SampleValidity.VALID
        assert sample.calibrated_value == pytest.approx(4.0)  # 2*2.5 - 1
        assert sample.unit == "bar"
        assert sample.channel_id == "oil_pressure"
        assert sample.timestamp_ms == 50.0
        assert sample.raw_value == 2.5

    def test_linear_calibration_out_of_range_low(self):
        """Voltage below min_valid_voltage should return INVALID."""
        engine = CalibrationEngine()
        profile = CalibrationProfile(
            calibration_type=CalibrationType.LINEAR,
            unit_label="bar",
            min_valid_voltage=0.5,
            max_valid_voltage=4.5,
            linear_params=LinearCalibrationParams(slope=2.0, offset=0.0),
        )
        engine.update_profile("oil_pressure", profile)

        sample = engine.apply("oil_pressure", 0.3)
        assert sample.validity == SampleValidity.INVALID

    def test_linear_calibration_out_of_range_high(self):
        """Voltage above max_valid_voltage should return INVALID."""
        engine = CalibrationEngine()
        profile = CalibrationProfile(
            calibration_type=CalibrationType.LINEAR,
            unit_label="bar",
            min_valid_voltage=0.5,
            max_valid_voltage=4.5,
            linear_params=LinearCalibrationParams(slope=2.0, offset=0.0),
        )
        engine.update_profile("oil_pressure", profile)

        sample = engine.apply("oil_pressure", 4.8)
        assert sample.validity == SampleValidity.INVALID

    def test_lookup_table_valid(self):
        """Valid voltage with lookup table should interpolate correctly."""
        engine = CalibrationEngine()
        profile = CalibrationProfile(
            calibration_type=CalibrationType.LOOKUP_TABLE,
            unit_label="kPa",
            min_valid_voltage=0.5,
            max_valid_voltage=4.5,
            lookup_params=LookupTableParams(
                points=[(0.5, 0.0), (2.5, 500.0), (4.5, 1000.0)]
            ),
        )
        engine.update_profile("charge_pressure", profile)

        sample = engine.apply("charge_pressure", 1.5, timestamp_ms=200.0)
        assert sample.validity == SampleValidity.VALID
        assert sample.calibrated_value == pytest.approx(250.0)
        assert sample.unit == "kPa"

    def test_lookup_table_out_of_range_clamp(self):
        """Voltage within valid range but outside lookup table should clamp with OUT_OF_RANGE."""
        engine = CalibrationEngine()
        # Valid voltage range is wider than lookup table range
        profile = CalibrationProfile(
            calibration_type=CalibrationType.LOOKUP_TABLE,
            unit_label="kPa",
            min_valid_voltage=0.0,
            max_valid_voltage=5.0,
            lookup_params=LookupTableParams(
                points=[(1.0, 100.0), (4.0, 400.0)]
            ),
        )
        engine.update_profile("sensor", profile)

        # Below lookup table minimum but within valid voltage range
        sample = engine.apply("sensor", 0.5)
        assert sample.validity == SampleValidity.OUT_OF_RANGE
        assert sample.calibrated_value == 100.0

        # Above lookup table maximum but within valid voltage range
        sample = engine.apply("sensor", 4.5)
        assert sample.validity == SampleValidity.OUT_OF_RANGE
        assert sample.calibrated_value == 400.0

    def test_hot_swap_profile(self):
        """Updating a profile should take effect on the next sample."""
        engine = CalibrationEngine()

        # Initial profile
        profile1 = CalibrationProfile(
            calibration_type=CalibrationType.LINEAR,
            unit_label="bar",
            min_valid_voltage=0.0,
            max_valid_voltage=5.0,
            linear_params=LinearCalibrationParams(slope=1.0, offset=0.0),
        )
        engine.update_profile("ch1", profile1)
        sample = engine.apply("ch1", 2.0)
        assert sample.calibrated_value == pytest.approx(2.0)

        # Hot-swap to new profile
        profile2 = CalibrationProfile(
            calibration_type=CalibrationType.LINEAR,
            unit_label="kPa",
            min_valid_voltage=0.0,
            max_valid_voltage=5.0,
            linear_params=LinearCalibrationParams(slope=100.0, offset=0.0),
        )
        engine.update_profile("ch1", profile2)
        sample = engine.apply("ch1", 2.0)
        assert sample.calibrated_value == pytest.approx(200.0)
        assert sample.unit == "kPa"


class TestCalibrationProfileValidation:
    """Tests for CalibrationEngine.validate_profile()."""

    def test_valid_linear_profile(self):
        """Valid linear profile should pass validation."""
        engine = CalibrationEngine()
        profile = CalibrationProfile(
            calibration_type=CalibrationType.LINEAR,
            unit_label="bar",
            min_valid_voltage=0.5,
            max_valid_voltage=4.5,
            linear_params=LinearCalibrationParams(slope=2.0, offset=-1.0),
        )
        result = engine.validate_profile(profile)
        assert result.valid is True
        assert result.errors == []

    def test_valid_lookup_table_profile(self):
        """Valid lookup table profile should pass validation."""
        engine = CalibrationEngine()
        profile = CalibrationProfile(
            calibration_type=CalibrationType.LOOKUP_TABLE,
            unit_label="kPa",
            min_valid_voltage=0.5,
            max_valid_voltage=4.5,
            lookup_params=LookupTableParams(
                points=[(0.5, 0.0), (2.5, 500.0), (4.5, 1000.0)]
            ),
        )
        result = engine.validate_profile(profile)
        assert result.valid is True

    def test_lookup_table_too_few_points(self):
        """Lookup table with fewer than 2 points should fail validation."""
        engine = CalibrationEngine()
        profile = CalibrationProfile(
            calibration_type=CalibrationType.LOOKUP_TABLE,
            unit_label="kPa",
            min_valid_voltage=0.5,
            max_valid_voltage=4.5,
            lookup_params=LookupTableParams(points=[(1.0, 10.0)]),
        )
        result = engine.validate_profile(profile)
        assert result.valid is False
        assert any("at least 2" in e for e in result.errors)

    def test_lookup_table_zero_points(self):
        """Lookup table with 0 points should fail validation."""
        engine = CalibrationEngine()
        profile = CalibrationProfile(
            calibration_type=CalibrationType.LOOKUP_TABLE,
            unit_label="kPa",
            min_valid_voltage=0.5,
            max_valid_voltage=4.5,
            lookup_params=LookupTableParams(points=[]),
        )
        result = engine.validate_profile(profile)
        assert result.valid is False
        assert any("at least 2" in e for e in result.errors)

    def test_lookup_table_duplicate_voltages(self):
        """Lookup table with duplicate voltages should fail validation."""
        engine = CalibrationEngine()
        profile = CalibrationProfile(
            calibration_type=CalibrationType.LOOKUP_TABLE,
            unit_label="kPa",
            min_valid_voltage=0.5,
            max_valid_voltage=4.5,
            lookup_params=LookupTableParams(
                points=[(1.0, 10.0), (1.0, 20.0), (3.0, 50.0)]
            ),
        )
        result = engine.validate_profile(profile)
        assert result.valid is False
        assert any("duplicate" in e for e in result.errors)

    def test_lookup_table_too_many_points(self):
        """Lookup table with more than 64 points should fail validation."""
        engine = CalibrationEngine()
        # Generate 65 unique voltage-value pairs
        points = [(float(i) * 0.1, float(i) * 10.0) for i in range(65)]
        profile = CalibrationProfile(
            calibration_type=CalibrationType.LOOKUP_TABLE,
            unit_label="kPa",
            min_valid_voltage=0.0,
            max_valid_voltage=10.0,
            lookup_params=LookupTableParams(points=points),
        )
        result = engine.validate_profile(profile)
        assert result.valid is False
        assert any("at most 64" in e for e in result.errors)

    def test_lookup_table_exactly_64_points_valid(self):
        """Lookup table with exactly 64 points should pass validation."""
        engine = CalibrationEngine()
        points = [(float(i) * 0.1, float(i) * 10.0) for i in range(64)]
        profile = CalibrationProfile(
            calibration_type=CalibrationType.LOOKUP_TABLE,
            unit_label="kPa",
            min_valid_voltage=0.0,
            max_valid_voltage=10.0,
            lookup_params=LookupTableParams(points=points),
        )
        result = engine.validate_profile(profile)
        assert result.valid is True

    def test_lookup_table_exactly_2_points_valid(self):
        """Lookup table with exactly 2 points should pass validation."""
        engine = CalibrationEngine()
        profile = CalibrationProfile(
            calibration_type=CalibrationType.LOOKUP_TABLE,
            unit_label="kPa",
            min_valid_voltage=0.0,
            max_valid_voltage=5.0,
            lookup_params=LookupTableParams(points=[(1.0, 10.0), (4.0, 100.0)]),
        )
        result = engine.validate_profile(profile)
        assert result.valid is True

    def test_min_voltage_greater_than_max(self):
        """min_valid_voltage > max_valid_voltage should fail validation."""
        engine = CalibrationEngine()
        profile = CalibrationProfile(
            calibration_type=CalibrationType.LINEAR,
            unit_label="bar",
            min_valid_voltage=5.0,
            max_valid_voltage=1.0,
            linear_params=LinearCalibrationParams(slope=1.0, offset=0.0),
        )
        result = engine.validate_profile(profile)
        assert result.valid is False
        assert any("min_valid_voltage" in e for e in result.errors)

    def test_min_voltage_equal_to_max(self):
        """min_valid_voltage == max_valid_voltage should fail validation."""
        engine = CalibrationEngine()
        profile = CalibrationProfile(
            calibration_type=CalibrationType.LINEAR,
            unit_label="bar",
            min_valid_voltage=2.5,
            max_valid_voltage=2.5,
            linear_params=LinearCalibrationParams(slope=1.0, offset=0.0),
        )
        result = engine.validate_profile(profile)
        assert result.valid is False
        assert any("min_valid_voltage" in e for e in result.errors)

    def test_linear_missing_params(self):
        """Linear calibration without linear_params should fail validation."""
        engine = CalibrationEngine()
        profile = CalibrationProfile(
            calibration_type=CalibrationType.LINEAR,
            unit_label="bar",
            min_valid_voltage=0.5,
            max_valid_voltage=4.5,
            linear_params=None,
        )
        result = engine.validate_profile(profile)
        assert result.valid is False
        assert any("linear_params" in e for e in result.errors)

    def test_lookup_table_missing_params(self):
        """Lookup table calibration without lookup_params should fail validation."""
        engine = CalibrationEngine()
        profile = CalibrationProfile(
            calibration_type=CalibrationType.LOOKUP_TABLE,
            unit_label="kPa",
            min_valid_voltage=0.5,
            max_valid_voltage=4.5,
            lookup_params=None,
        )
        result = engine.validate_profile(profile)
        assert result.valid is False
        assert any("lookup_params" in e for e in result.errors)


class TestCalibrationEngineEdgeCases:
    """Additional edge case tests for CalibrationEngine (task 3.2)."""

    def test_voltage_exactly_at_min_valid_is_valid(self):
        """Voltage exactly at min_valid_voltage boundary should be VALID."""
        engine = CalibrationEngine()
        profile = CalibrationProfile(
            calibration_type=CalibrationType.LINEAR,
            unit_label="bar",
            min_valid_voltage=0.5,
            max_valid_voltage=4.5,
            linear_params=LinearCalibrationParams(slope=2.0, offset=0.0),
        )
        engine.update_profile("ch1", profile)

        sample = engine.apply("ch1", 0.5)
        assert sample.validity == SampleValidity.VALID
        assert sample.calibrated_value == pytest.approx(1.0)

    def test_voltage_exactly_at_max_valid_is_valid(self):
        """Voltage exactly at max_valid_voltage boundary should be VALID."""
        engine = CalibrationEngine()
        profile = CalibrationProfile(
            calibration_type=CalibrationType.LINEAR,
            unit_label="bar",
            min_valid_voltage=0.5,
            max_valid_voltage=4.5,
            linear_params=LinearCalibrationParams(slope=2.0, offset=0.0),
        )
        engine.update_profile("ch1", profile)

        sample = engine.apply("ch1", 4.5)
        assert sample.validity == SampleValidity.VALID
        assert sample.calibrated_value == pytest.approx(9.0)

    def test_hot_swap_from_lookup_to_linear(self):
        """Hot-swapping from lookup table to linear should work immediately."""
        engine = CalibrationEngine()

        # Start with lookup table
        lookup_profile = CalibrationProfile(
            calibration_type=CalibrationType.LOOKUP_TABLE,
            unit_label="kPa",
            min_valid_voltage=0.0,
            max_valid_voltage=5.0,
            lookup_params=LookupTableParams(points=[(0.0, 0.0), (5.0, 1000.0)]),
        )
        engine.update_profile("ch1", lookup_profile)
        sample = engine.apply("ch1", 2.5)
        assert sample.calibrated_value == pytest.approx(500.0)
        assert sample.unit == "kPa"

        # Hot-swap to linear
        linear_profile = CalibrationProfile(
            calibration_type=CalibrationType.LINEAR,
            unit_label="bar",
            min_valid_voltage=0.0,
            max_valid_voltage=5.0,
            linear_params=LinearCalibrationParams(slope=3.0, offset=1.0),
        )
        engine.update_profile("ch1", linear_profile)
        sample = engine.apply("ch1", 2.5)
        assert sample.calibrated_value == pytest.approx(8.5)  # 3*2.5 + 1
        assert sample.unit == "bar"

    def test_hot_swap_from_linear_to_lookup(self):
        """Hot-swapping from linear to lookup table should work immediately."""
        engine = CalibrationEngine()

        # Start with linear
        linear_profile = CalibrationProfile(
            calibration_type=CalibrationType.LINEAR,
            unit_label="bar",
            min_valid_voltage=0.0,
            max_valid_voltage=5.0,
            linear_params=LinearCalibrationParams(slope=2.0, offset=0.0),
        )
        engine.update_profile("ch1", linear_profile)
        sample = engine.apply("ch1", 2.0)
        assert sample.calibrated_value == pytest.approx(4.0)

        # Hot-swap to lookup table
        lookup_profile = CalibrationProfile(
            calibration_type=CalibrationType.LOOKUP_TABLE,
            unit_label="kPa",
            min_valid_voltage=0.0,
            max_valid_voltage=5.0,
            lookup_params=LookupTableParams(points=[(0.0, 0.0), (5.0, 500.0)]),
        )
        engine.update_profile("ch1", lookup_profile)
        sample = engine.apply("ch1", 2.0)
        assert sample.calibrated_value == pytest.approx(200.0)
        assert sample.unit == "kPa"

    def test_multiple_channels_independent(self):
        """Profiles for different channels should be independent."""
        engine = CalibrationEngine()

        profile_a = CalibrationProfile(
            calibration_type=CalibrationType.LINEAR,
            unit_label="bar",
            min_valid_voltage=0.0,
            max_valid_voltage=5.0,
            linear_params=LinearCalibrationParams(slope=1.0, offset=0.0),
        )
        profile_b = CalibrationProfile(
            calibration_type=CalibrationType.LINEAR,
            unit_label="kPa",
            min_valid_voltage=0.0,
            max_valid_voltage=5.0,
            linear_params=LinearCalibrationParams(slope=100.0, offset=0.0),
        )
        engine.update_profile("ch_a", profile_a)
        engine.update_profile("ch_b", profile_b)

        sample_a = engine.apply("ch_a", 2.0)
        sample_b = engine.apply("ch_b", 2.0)
        assert sample_a.calibrated_value == pytest.approx(2.0)
        assert sample_b.calibrated_value == pytest.approx(200.0)

    def test_hot_swap_does_not_affect_other_channels(self):
        """Hot-swapping one channel should not affect another."""
        engine = CalibrationEngine()

        profile_a = CalibrationProfile(
            calibration_type=CalibrationType.LINEAR,
            unit_label="bar",
            min_valid_voltage=0.0,
            max_valid_voltage=5.0,
            linear_params=LinearCalibrationParams(slope=1.0, offset=0.0),
        )
        profile_b = CalibrationProfile(
            calibration_type=CalibrationType.LINEAR,
            unit_label="kPa",
            min_valid_voltage=0.0,
            max_valid_voltage=5.0,
            linear_params=LinearCalibrationParams(slope=10.0, offset=0.0),
        )
        engine.update_profile("ch_a", profile_a)
        engine.update_profile("ch_b", profile_b)

        # Hot-swap ch_a
        new_profile_a = CalibrationProfile(
            calibration_type=CalibrationType.LINEAR,
            unit_label="PSI",
            min_valid_voltage=0.0,
            max_valid_voltage=5.0,
            linear_params=LinearCalibrationParams(slope=50.0, offset=0.0),
        )
        engine.update_profile("ch_a", new_profile_a)

        # ch_b should be unaffected
        sample_b = engine.apply("ch_b", 2.0)
        assert sample_b.calibrated_value == pytest.approx(20.0)
        assert sample_b.unit == "kPa"

    def test_lookup_table_voltage_at_exact_boundary_not_out_of_range(self):
        """Voltage exactly at lookup table boundary should NOT be out-of-range."""
        engine = CalibrationEngine()
        profile = CalibrationProfile(
            calibration_type=CalibrationType.LOOKUP_TABLE,
            unit_label="kPa",
            min_valid_voltage=0.0,
            max_valid_voltage=5.0,
            lookup_params=LookupTableParams(points=[(1.0, 100.0), (4.0, 400.0)]),
        )
        engine.update_profile("ch1", profile)

        # Exactly at lower lookup boundary
        sample = engine.apply("ch1", 1.0)
        assert sample.validity == SampleValidity.VALID
        assert sample.calibrated_value == pytest.approx(100.0)

        # Exactly at upper lookup boundary
        sample = engine.apply("ch1", 4.0)
        assert sample.validity == SampleValidity.VALID
        assert sample.calibrated_value == pytest.approx(400.0)

    def test_apply_preserves_timestamp(self):
        """apply() should preserve the provided timestamp in the output."""
        engine = CalibrationEngine()
        profile = CalibrationProfile(
            calibration_type=CalibrationType.LINEAR,
            unit_label="bar",
            min_valid_voltage=0.0,
            max_valid_voltage=5.0,
            linear_params=LinearCalibrationParams(slope=1.0, offset=0.0),
        )
        engine.update_profile("ch1", profile)

        sample = engine.apply("ch1", 2.5, timestamp_ms=12345.678)
        assert sample.timestamp_ms == 12345.678

    def test_apply_preserves_raw_value(self):
        """apply() should preserve the raw_value in the output."""
        engine = CalibrationEngine()
        profile = CalibrationProfile(
            calibration_type=CalibrationType.LINEAR,
            unit_label="bar",
            min_valid_voltage=0.0,
            max_valid_voltage=5.0,
            linear_params=LinearCalibrationParams(slope=2.0, offset=1.0),
        )
        engine.update_profile("ch1", profile)

        sample = engine.apply("ch1", 3.14, timestamp_ms=0.0)
        assert sample.raw_value == 3.14

    def test_invalid_voltage_returns_zero_calibrated_value(self):
        """When voltage is out of valid range, calibrated_value should be 0.0."""
        engine = CalibrationEngine()
        profile = CalibrationProfile(
            calibration_type=CalibrationType.LINEAR,
            unit_label="bar",
            min_valid_voltage=1.0,
            max_valid_voltage=4.0,
            linear_params=LinearCalibrationParams(slope=2.0, offset=0.0),
        )
        engine.update_profile("ch1", profile)

        sample = engine.apply("ch1", 0.5)
        assert sample.validity == SampleValidity.INVALID
        assert sample.calibrated_value == 0.0

    def test_uncalibrated_returns_raw_as_calibrated(self):
        """Uncalibrated channel should return raw_value as calibrated_value."""
        engine = CalibrationEngine()
        sample = engine.apply("no_profile", 3.7, timestamp_ms=100.0)
        assert sample.validity == SampleValidity.UNCALIBRATED
        assert sample.calibrated_value == 3.7
        assert sample.raw_value == 3.7
        assert sample.unit == "V"
