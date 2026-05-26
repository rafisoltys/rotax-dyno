"""Unit tests for sample rate clamping and defaults."""

import pytest

from rotax_dyno_daq.calibration.rate_config import (
    clamp_sample_rate,
    get_default_rate,
    get_rate_range,
)
from rotax_dyno_daq.core.enums import ChannelType


class TestGetRateRange:
    """Tests for get_rate_range function."""

    def test_thermocouple_range(self):
        assert get_rate_range(ChannelType.THERMOCOUPLE) == (1.0, 10.0)

    def test_pressure_range(self):
        assert get_rate_range(ChannelType.PRESSURE) == (10.0, 100.0)

    def test_rpm_range(self):
        assert get_rate_range(ChannelType.RPM) == (10.0, 100.0)

    def test_afr_range(self):
        assert get_rate_range(ChannelType.AFR) == (10.0, 50.0)


class TestGetDefaultRate:
    """Tests for get_default_rate function."""

    def test_thermocouple_default(self):
        assert get_default_rate(ChannelType.THERMOCOUPLE) == 5.0

    def test_pressure_default(self):
        assert get_default_rate(ChannelType.PRESSURE) == 10.0

    def test_rpm_default(self):
        assert get_default_rate(ChannelType.RPM) == 50.0

    def test_afr_default(self):
        assert get_default_rate(ChannelType.AFR) == 20.0


class TestClampSampleRate:
    """Tests for clamp_sample_rate function."""

    def test_none_returns_default_thermocouple(self):
        assert clamp_sample_rate(ChannelType.THERMOCOUPLE, None) == 5.0

    def test_none_returns_default_pressure(self):
        assert clamp_sample_rate(ChannelType.PRESSURE, None) == 10.0

    def test_none_returns_default_rpm(self):
        assert clamp_sample_rate(ChannelType.RPM, None) == 50.0

    def test_none_returns_default_afr(self):
        assert clamp_sample_rate(ChannelType.AFR, None) == 20.0

    def test_rate_within_range_unchanged(self):
        assert clamp_sample_rate(ChannelType.THERMOCOUPLE, 7.0) == 7.0
        assert clamp_sample_rate(ChannelType.PRESSURE, 50.0) == 50.0
        assert clamp_sample_rate(ChannelType.RPM, 75.0) == 75.0
        assert clamp_sample_rate(ChannelType.AFR, 30.0) == 30.0

    def test_rate_below_min_clamped_to_min(self):
        assert clamp_sample_rate(ChannelType.THERMOCOUPLE, 0.5) == 1.0
        assert clamp_sample_rate(ChannelType.PRESSURE, 5.0) == 10.0
        assert clamp_sample_rate(ChannelType.RPM, 1.0) == 10.0
        assert clamp_sample_rate(ChannelType.AFR, 5.0) == 10.0

    def test_rate_above_max_clamped_to_max(self):
        assert clamp_sample_rate(ChannelType.THERMOCOUPLE, 20.0) == 10.0
        assert clamp_sample_rate(ChannelType.PRESSURE, 200.0) == 100.0
        assert clamp_sample_rate(ChannelType.RPM, 150.0) == 100.0
        assert clamp_sample_rate(ChannelType.AFR, 100.0) == 50.0

    def test_rate_at_min_boundary(self):
        assert clamp_sample_rate(ChannelType.THERMOCOUPLE, 1.0) == 1.0
        assert clamp_sample_rate(ChannelType.PRESSURE, 10.0) == 10.0
        assert clamp_sample_rate(ChannelType.RPM, 10.0) == 10.0
        assert clamp_sample_rate(ChannelType.AFR, 10.0) == 10.0

    def test_rate_at_max_boundary(self):
        assert clamp_sample_rate(ChannelType.THERMOCOUPLE, 10.0) == 10.0
        assert clamp_sample_rate(ChannelType.PRESSURE, 100.0) == 100.0
        assert clamp_sample_rate(ChannelType.RPM, 100.0) == 100.0
        assert clamp_sample_rate(ChannelType.AFR, 50.0) == 50.0
