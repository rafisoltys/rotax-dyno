"""Unit tests for the AnalogVoltageReader class."""

import threading
import time
from unittest.mock import MagicMock

import pytest

from rotax_dyno_daq.acquisition.analog_voltage_reader import AnalogVoltageReader
from rotax_dyno_daq.core.data_bus import DataBus
from rotax_dyno_daq.core.enums import (
    CalibrationType,
    ChannelType,
    SampleValidity,
)
from rotax_dyno_daq.core.models import (
    CalibrationProfile,
    ChannelConfig,
    LinearCalibrationParams,
    RawSample,
)


def _make_calibration(
    min_v: float = 0.5,
    max_v: float = 4.5,
    unit: str = "bar",
) -> CalibrationProfile:
    """Create a simple linear calibration profile for testing."""
    return CalibrationProfile(
        calibration_type=CalibrationType.LINEAR,
        unit_label=unit,
        min_valid_voltage=min_v,
        max_valid_voltage=max_v,
        linear_params=LinearCalibrationParams(slope=1.0, offset=0.0),
    )


def _make_channel(
    channel_id: str = "OilP",
    channel_type: ChannelType = ChannelType.PRESSURE,
    hat_channel: int = 0,
    min_v: float = 0.5,
    max_v: float = 4.5,
    unit: str = "bar",
    sample_rate_hz: float = 10.0,
) -> ChannelConfig:
    """Create a channel config for testing."""
    return ChannelConfig(
        channel_id=channel_id,
        channel_type=channel_type,
        hat_address=0,
        hat_channel=hat_channel,
        sample_rate_hz=sample_rate_hz,
        calibration=_make_calibration(min_v=min_v, max_v=max_v, unit=unit),
        display_name=channel_id,
        enabled=True,
    )


class TestAnalogVoltageReaderPressure:
    """Tests for pressure channel validation logic."""

    def test_valid_pressure_reading(self):
        """Voltage within [min, max] produces VALID sample."""
        mock_hat = MagicMock()
        mock_hat.a_in_read.return_value = 2.5

        channel = _make_channel("OilP", ChannelType.PRESSURE, min_v=0.5, max_v=4.5)
        data_bus = DataBus()
        reader = AnalogVoltageReader(
            address=0, channels=[channel], data_bus=data_bus, hat_device=mock_hat
        )

        sample = reader.read_sample(channel)

        assert sample.validity == SampleValidity.VALID
        assert sample.raw_value == 2.5
        assert sample.channel_id == "OilP"

    def test_pressure_below_min_is_invalid(self):
        """Voltage below min_valid_voltage produces INVALID sample."""
        mock_hat = MagicMock()
        mock_hat.a_in_read.return_value = 0.3

        channel = _make_channel("OilP", ChannelType.PRESSURE, min_v=0.5, max_v=4.5)
        data_bus = DataBus()
        reader = AnalogVoltageReader(
            address=0, channels=[channel], data_bus=data_bus, hat_device=mock_hat
        )

        sample = reader.read_sample(channel)

        assert sample.validity == SampleValidity.INVALID
        assert sample.raw_value == 0.3

    def test_pressure_above_max_is_invalid(self):
        """Voltage above max_valid_voltage produces INVALID sample."""
        mock_hat = MagicMock()
        mock_hat.a_in_read.return_value = 4.8

        channel = _make_channel("OilP", ChannelType.PRESSURE, min_v=0.5, max_v=4.5)
        data_bus = DataBus()
        reader = AnalogVoltageReader(
            address=0, channels=[channel], data_bus=data_bus, hat_device=mock_hat
        )

        sample = reader.read_sample(channel)

        assert sample.validity == SampleValidity.INVALID
        assert sample.raw_value == 4.8

    def test_pressure_at_min_boundary_is_valid(self):
        """Voltage exactly at min_valid_voltage is VALID."""
        mock_hat = MagicMock()
        mock_hat.a_in_read.return_value = 0.5

        channel = _make_channel("OilP", ChannelType.PRESSURE, min_v=0.5, max_v=4.5)
        data_bus = DataBus()
        reader = AnalogVoltageReader(
            address=0, channels=[channel], data_bus=data_bus, hat_device=mock_hat
        )

        sample = reader.read_sample(channel)

        assert sample.validity == SampleValidity.VALID

    def test_pressure_at_max_boundary_is_valid(self):
        """Voltage exactly at max_valid_voltage is VALID."""
        mock_hat = MagicMock()
        mock_hat.a_in_read.return_value = 4.5

        channel = _make_channel("OilP", ChannelType.PRESSURE, min_v=0.5, max_v=4.5)
        data_bus = DataBus()
        reader = AnalogVoltageReader(
            address=0, channels=[channel], data_bus=data_bus, hat_device=mock_hat
        )

        sample = reader.read_sample(channel)

        assert sample.validity == SampleValidity.VALID


class TestAnalogVoltageReaderRPM:
    """Tests for RPM channel validation logic."""

    def test_rpm_valid_reading(self):
        """Voltage within [min, max] produces VALID sample with actual voltage."""
        mock_hat = MagicMock()
        mock_hat.a_in_read.return_value = 2.0

        channel = _make_channel("RPM", ChannelType.RPM, min_v=0.2, max_v=4.8, unit="RPM")
        data_bus = DataBus()
        reader = AnalogVoltageReader(
            address=0, channels=[channel], data_bus=data_bus, hat_device=mock_hat
        )

        sample = reader.read_sample(channel)

        assert sample.validity == SampleValidity.VALID
        assert sample.raw_value == 2.0

    def test_rpm_below_min_yields_zero(self):
        """Voltage below min_valid_voltage reports zero (not invalid)."""
        mock_hat = MagicMock()
        mock_hat.a_in_read.return_value = 0.1

        channel = _make_channel("RPM", ChannelType.RPM, min_v=0.2, max_v=4.8, unit="RPM")
        data_bus = DataBus()
        reader = AnalogVoltageReader(
            address=0, channels=[channel], data_bus=data_bus, hat_device=mock_hat
        )

        sample = reader.read_sample(channel)

        assert sample.validity == SampleValidity.VALID
        assert sample.raw_value == 0.0

    def test_rpm_above_max_is_invalid(self):
        """Voltage above max_valid_voltage flags as INVALID."""
        mock_hat = MagicMock()
        mock_hat.a_in_read.return_value = 5.0

        channel = _make_channel("RPM", ChannelType.RPM, min_v=0.2, max_v=4.8, unit="RPM")
        data_bus = DataBus()
        reader = AnalogVoltageReader(
            address=0, channels=[channel], data_bus=data_bus, hat_device=mock_hat
        )

        sample = reader.read_sample(channel)

        assert sample.validity == SampleValidity.INVALID
        assert sample.raw_value == 5.0

    def test_rpm_at_min_boundary_is_valid(self):
        """Voltage exactly at min_valid_voltage is VALID (not zero)."""
        mock_hat = MagicMock()
        mock_hat.a_in_read.return_value = 0.2

        channel = _make_channel("RPM", ChannelType.RPM, min_v=0.2, max_v=4.8, unit="RPM")
        data_bus = DataBus()
        reader = AnalogVoltageReader(
            address=0, channels=[channel], data_bus=data_bus, hat_device=mock_hat
        )

        sample = reader.read_sample(channel)

        assert sample.validity == SampleValidity.VALID
        assert sample.raw_value == 0.2

    def test_rpm_at_max_boundary_is_valid(self):
        """Voltage exactly at max_valid_voltage is VALID."""
        mock_hat = MagicMock()
        mock_hat.a_in_read.return_value = 4.8

        channel = _make_channel("RPM", ChannelType.RPM, min_v=0.2, max_v=4.8, unit="RPM")
        data_bus = DataBus()
        reader = AnalogVoltageReader(
            address=0, channels=[channel], data_bus=data_bus, hat_device=mock_hat
        )

        sample = reader.read_sample(channel)

        assert sample.validity == SampleValidity.VALID
        assert sample.raw_value == 4.8


class TestAnalogVoltageReaderAFR:
    """Tests for AFR channel validation logic."""

    def test_afr_valid_reading(self):
        """Voltage within [min, max] produces VALID sample."""
        mock_hat = MagicMock()
        mock_hat.a_in_read.return_value = 2.5

        channel = _make_channel("AFR1", ChannelType.AFR, min_v=0.5, max_v=4.5, unit="lambda")
        data_bus = DataBus()
        reader = AnalogVoltageReader(
            address=0, channels=[channel], data_bus=data_bus, hat_device=mock_hat
        )

        sample = reader.read_sample(channel)

        assert sample.validity == SampleValidity.VALID
        assert sample.raw_value == 2.5

    def test_afr_below_min_is_invalid(self):
        """AFR voltage below min_valid_voltage produces INVALID."""
        mock_hat = MagicMock()
        mock_hat.a_in_read.return_value = 0.3

        channel = _make_channel("AFR1", ChannelType.AFR, min_v=0.5, max_v=4.5, unit="lambda")
        data_bus = DataBus()
        reader = AnalogVoltageReader(
            address=0, channels=[channel], data_bus=data_bus, hat_device=mock_hat
        )

        sample = reader.read_sample(channel)

        assert sample.validity == SampleValidity.INVALID

    def test_afr_above_max_is_invalid(self):
        """AFR voltage above max_valid_voltage produces INVALID."""
        mock_hat = MagicMock()
        mock_hat.a_in_read.return_value = 4.8

        channel = _make_channel("AFR1", ChannelType.AFR, min_v=0.5, max_v=4.5, unit="lambda")
        data_bus = DataBus()
        reader = AnalogVoltageReader(
            address=0, channels=[channel], data_bus=data_bus, hat_device=mock_hat
        )

        sample = reader.read_sample(channel)

        assert sample.validity == SampleValidity.INVALID


class TestAnalogVoltageReaderMultiChannel:
    """Tests for multi-channel support on the same HAT."""

    def test_multiple_channel_types_on_same_hat(self):
        """Reader supports pressure, RPM, and AFR channels simultaneously."""
        mock_hat = MagicMock()
        # Return different voltages for different channels
        mock_hat.a_in_read.side_effect = [2.5, 3.0, 1.5]

        channels = [
            _make_channel("OilP", ChannelType.PRESSURE, hat_channel=0, min_v=0.5, max_v=4.5),
            _make_channel("RPM", ChannelType.RPM, hat_channel=1, min_v=0.2, max_v=4.8),
            _make_channel("AFR1", ChannelType.AFR, hat_channel=2, min_v=0.5, max_v=4.5),
        ]
        data_bus = DataBus()
        reader = AnalogVoltageReader(
            address=0, channels=channels, data_bus=data_bus, hat_device=mock_hat
        )

        samples = [reader.read_sample(ch) for ch in channels]

        assert all(s.validity == SampleValidity.VALID for s in samples)
        assert samples[0].channel_id == "OilP"
        assert samples[1].channel_id == "RPM"
        assert samples[2].channel_id == "AFR1"


class TestAnalogVoltageReaderBackgroundThread:
    """Tests for background thread acquisition."""

    def test_start_stop_lifecycle(self):
        """Reader starts and stops cleanly."""
        mock_hat = MagicMock()
        mock_hat.a_in_read.return_value = 2.5

        channel = _make_channel("OilP", ChannelType.PRESSURE, sample_rate_hz=100.0)
        data_bus = DataBus()
        reader = AnalogVoltageReader(
            address=0, channels=[channel], data_bus=data_bus, hat_device=mock_hat
        )

        assert not reader.is_running
        reader.start()
        assert reader.is_running
        time.sleep(0.05)  # Let it run a few cycles
        reader.stop()
        assert not reader.is_running

    def test_publishes_to_data_bus(self):
        """Reader publishes RawSample objects to the DataBus."""
        mock_hat = MagicMock()
        mock_hat.a_in_read.return_value = 2.5

        channel = _make_channel("OilP", ChannelType.PRESSURE, sample_rate_hz=100.0)
        data_bus = DataBus()
        received_samples: list[RawSample] = []
        data_bus.subscribe("OilP", lambda s: received_samples.append(s))

        reader = AnalogVoltageReader(
            address=0, channels=[channel], data_bus=data_bus, hat_device=mock_hat
        )

        reader.start()
        time.sleep(0.15)  # Allow several cycles at 100 Hz
        reader.stop()

        assert len(received_samples) > 0
        assert all(s.channel_id == "OilP" for s in received_samples)
        assert all(s.validity == SampleValidity.VALID for s in received_samples)

    def test_hardware_error_produces_invalid_sample(self):
        """Hardware read exception produces INVALID sample."""
        mock_hat = MagicMock()
        mock_hat.a_in_read.side_effect = IOError("Hardware fault")

        channel = _make_channel("OilP", ChannelType.PRESSURE)
        data_bus = DataBus()
        reader = AnalogVoltageReader(
            address=0, channels=[channel], data_bus=data_bus, hat_device=mock_hat
        )

        sample = reader.read_sample(channel)

        assert sample.validity == SampleValidity.INVALID
        assert sample.raw_value == 0.0

    def test_no_hat_device_raises_runtime_error(self):
        """Reading without a HAT device raises RuntimeError."""
        channel = _make_channel("OilP", ChannelType.PRESSURE)
        data_bus = DataBus()
        reader = AnalogVoltageReader(
            address=0, channels=[channel], data_bus=data_bus, hat_device=None
        )
        # Force _hat to None (bypass the auto-create attempt)
        reader._hat = None

        with pytest.raises(RuntimeError, match="No MCC 118 HAT device available"):
            reader.read_sample(channel)
