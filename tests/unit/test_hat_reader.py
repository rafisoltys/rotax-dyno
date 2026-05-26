"""Unit tests for HatReader base class and ThermocoupleReader."""

import time
from unittest.mock import MagicMock, patch

import pytest

from rotax_dyno_daq.acquisition.hat_reader import (
    HatReader,
    ThermocoupleReader,
    _TC_OPEN_VALUE,
)
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


def _make_tc_channel(channel_id: str, hat_channel: int, rate: float = 5.0) -> ChannelConfig:
    """Create a thermocouple channel config for testing."""
    return ChannelConfig(
        channel_id=channel_id,
        channel_type=ChannelType.THERMOCOUPLE,
        hat_address=0,
        hat_channel=hat_channel,
        sample_rate_hz=rate,
        calibration=CalibrationProfile(
            calibration_type=CalibrationType.LINEAR,
            unit_label="°C",
            min_valid_voltage=0.0,
            max_valid_voltage=5.0,
            linear_params=LinearCalibrationParams(slope=1.0, offset=0.0),
        ),
    )


class ConcreteHatReader(HatReader):
    """Concrete implementation of HatReader for testing the base class."""

    def __init__(self, address, channels, data_bus, read_values=None):
        super().__init__(address, channels, data_bus)
        self.read_values = read_values or {}
        self.read_count = 0

    def read_sample(self, channel: int) -> RawSample:
        self.read_count += 1
        value = self.read_values.get(channel, 25.0)
        channel_id = self._get_channel_id(channel)
        return RawSample(
            channel_id=channel_id,
            timestamp_ms=time.time() * 1000.0,
            raw_value=value,
            validity=SampleValidity.VALID,
        )


class TestHatReaderBase:
    """Tests for the abstract HatReader base class."""

    def test_init_sets_sample_rate_from_channel(self):
        """Sample rate is clamped based on channel type."""
        data_bus = DataBus()
        channels = [_make_tc_channel("EGT1", 0, rate=5.0)]
        reader = ConcreteHatReader(0, channels, data_bus)
        assert reader.sample_rate_hz == 5.0

    def test_init_clamps_rate_to_valid_range(self):
        """Rate above max is clamped to max for thermocouple (10 Hz)."""
        data_bus = DataBus()
        channels = [_make_tc_channel("EGT1", 0, rate=50.0)]
        reader = ConcreteHatReader(0, channels, data_bus)
        assert reader.sample_rate_hz == 10.0

    def test_init_clamps_rate_below_min(self):
        """Rate below min is clamped to min for thermocouple (1 Hz)."""
        data_bus = DataBus()
        channels = [_make_tc_channel("EGT1", 0, rate=0.1)]
        reader = ConcreteHatReader(0, channels, data_bus)
        assert reader.sample_rate_hz == 1.0

    def test_start_stop_lifecycle(self):
        """Reader can be started and stopped cleanly."""
        data_bus = DataBus()
        channels = [_make_tc_channel("EGT1", 0, rate=10.0)]
        reader = ConcreteHatReader(0, channels, data_bus)

        reader.start()
        assert reader.is_running is True
        assert reader._thread is not None
        assert reader._thread.is_alive()

        reader.stop()
        assert reader.is_running is False
        assert reader._thread is None

    def test_start_when_already_running_is_noop(self):
        """Calling start() when already running does not create a new thread."""
        data_bus = DataBus()
        channels = [_make_tc_channel("EGT1", 0, rate=10.0)]
        reader = ConcreteHatReader(0, channels, data_bus)

        reader.start()
        thread1 = reader._thread
        reader.start()  # Should be a no-op
        assert reader._thread is thread1

        reader.stop()

    def test_stop_when_not_running_is_noop(self):
        """Calling stop() when not running does nothing."""
        data_bus = DataBus()
        channels = [_make_tc_channel("EGT1", 0, rate=10.0)]
        reader = ConcreteHatReader(0, channels, data_bus)
        reader.stop()  # Should not raise

    def test_set_sample_rate_updates_rate(self):
        """set_sample_rate() updates the polling interval."""
        data_bus = DataBus()
        channels = [_make_tc_channel("EGT1", 0, rate=5.0)]
        reader = ConcreteHatReader(0, channels, data_bus)

        reader.set_sample_rate(8.0)
        assert reader.sample_rate_hz == 8.0

    def test_set_sample_rate_clamps_to_valid_range(self):
        """set_sample_rate() clamps to the channel type's valid range."""
        data_bus = DataBus()
        channels = [_make_tc_channel("EGT1", 0, rate=5.0)]
        reader = ConcreteHatReader(0, channels, data_bus)

        reader.set_sample_rate(100.0)  # Above max for thermocouple
        assert reader.sample_rate_hz == 10.0

    def test_acquisition_publishes_to_data_bus(self):
        """Background thread publishes samples to the DataBus."""
        data_bus = DataBus()
        channels = [_make_tc_channel("EGT1", 0, rate=10.0)]
        reader = ConcreteHatReader(0, channels, data_bus, read_values={0: 650.0})

        received = []
        data_bus.subscribe("EGT1", lambda s: received.append(s))

        reader.start()
        time.sleep(0.3)  # Allow a few cycles at 10 Hz
        reader.stop()

        assert len(received) > 0
        assert all(isinstance(s, RawSample) for s in received)
        assert all(s.channel_id == "EGT1" for s in received)
        assert all(s.raw_value == 650.0 for s in received)

    def test_acquisition_reads_multiple_channels(self):
        """Background thread reads all configured channels."""
        data_bus = DataBus()
        channels = [
            _make_tc_channel("EGT1", 0, rate=10.0),
            _make_tc_channel("EGT2", 1, rate=10.0),
        ]
        reader = ConcreteHatReader(
            0, channels, data_bus, read_values={0: 600.0, 1: 700.0}
        )

        received_egt1 = []
        received_egt2 = []
        data_bus.subscribe("EGT1", lambda s: received_egt1.append(s))
        data_bus.subscribe("EGT2", lambda s: received_egt2.append(s))

        reader.start()
        time.sleep(0.3)
        reader.stop()

        assert len(received_egt1) > 0
        assert len(received_egt2) > 0
        assert received_egt1[0].raw_value == 600.0
        assert received_egt2[0].raw_value == 700.0

    def test_address_property(self):
        """The address property returns the HAT address."""
        data_bus = DataBus()
        channels = [_make_tc_channel("EGT1", 0)]
        reader = ConcreteHatReader(3, channels, data_bus)
        assert reader.address == 3

    def test_channels_property(self):
        """The channels property returns a copy of channel configs."""
        data_bus = DataBus()
        channels = [_make_tc_channel("EGT1", 0)]
        reader = ConcreteHatReader(0, channels, data_bus)
        assert reader.channels == channels
        # Should be a copy
        assert reader.channels is not channels


class TestThermocoupleReader:
    """Tests for the ThermocoupleReader (MCC 134)."""

    def test_read_sample_valid_temperature(self):
        """Valid temperature reading produces VALID sample."""
        data_bus = DataBus()
        channels = [_make_tc_channel("EGT1", 0)]

        with patch(
            "rotax_dyno_daq.acquisition.hat_reader.DAQHATS_AVAILABLE", False
        ):
            reader = ThermocoupleReader(0, channels, data_bus)

        # Inject a mock HAT
        mock_hat = MagicMock()
        mock_hat.t_in_read.return_value = 650.0
        reader._hat = mock_hat

        sample = reader.read_sample(0)
        assert sample.channel_id == "EGT1"
        assert sample.raw_value == 650.0
        assert sample.validity == SampleValidity.VALID

    def test_read_sample_open_circuit_fault(self):
        """Open-circuit fault (TC_OPEN) produces INVALID sample."""
        data_bus = DataBus()
        channels = [_make_tc_channel("EGT1", 0)]

        with patch(
            "rotax_dyno_daq.acquisition.hat_reader.DAQHATS_AVAILABLE", False
        ):
            reader = ThermocoupleReader(0, channels, data_bus)

        mock_hat = MagicMock()
        mock_hat.t_in_read.return_value = _TC_OPEN_VALUE
        reader._hat = mock_hat

        sample = reader.read_sample(0)
        assert sample.channel_id == "EGT1"
        assert sample.raw_value == _TC_OPEN_VALUE
        assert sample.validity == SampleValidity.INVALID

    def test_read_sample_no_hardware_returns_invalid(self):
        """When daqhats is not available, samples are marked INVALID."""
        data_bus = DataBus()
        channels = [_make_tc_channel("EGT1", 0)]

        with patch(
            "rotax_dyno_daq.acquisition.hat_reader.DAQHATS_AVAILABLE", False
        ):
            reader = ThermocoupleReader(0, channels, data_bus)

        # _hat should be None when daqhats is not available
        assert reader._hat is None
        sample = reader.read_sample(0)
        assert sample.validity == SampleValidity.INVALID

    def test_read_sample_hardware_exception_returns_invalid(self):
        """Hardware read exception produces INVALID sample."""
        data_bus = DataBus()
        channels = [_make_tc_channel("EGT1", 0)]

        with patch(
            "rotax_dyno_daq.acquisition.hat_reader.DAQHATS_AVAILABLE", False
        ):
            reader = ThermocoupleReader(0, channels, data_bus)

        mock_hat = MagicMock()
        mock_hat.t_in_read.side_effect = RuntimeError("Hardware fault")
        reader._hat = mock_hat

        sample = reader.read_sample(0)
        assert sample.validity == SampleValidity.INVALID

    def test_thermocouple_reader_publishes_samples(self):
        """ThermocoupleReader publishes valid samples to the DataBus."""
        data_bus = DataBus()
        channels = [_make_tc_channel("EGT1", 0, rate=10.0)]

        with patch(
            "rotax_dyno_daq.acquisition.hat_reader.DAQHATS_AVAILABLE", False
        ):
            reader = ThermocoupleReader(0, channels, data_bus)

        mock_hat = MagicMock()
        mock_hat.t_in_read.return_value = 500.0
        reader._hat = mock_hat

        received = []
        data_bus.subscribe("EGT1", lambda s: received.append(s))

        reader.start()
        time.sleep(0.3)
        reader.stop()

        assert len(received) > 0
        assert received[0].raw_value == 500.0
        assert received[0].validity == SampleValidity.VALID

    def test_thermocouple_reader_detects_open_circuit_in_loop(self):
        """Open-circuit faults are detected during acquisition loop."""
        data_bus = DataBus()
        channels = [_make_tc_channel("EGT1", 0, rate=10.0)]

        with patch(
            "rotax_dyno_daq.acquisition.hat_reader.DAQHATS_AVAILABLE", False
        ):
            reader = ThermocoupleReader(0, channels, data_bus)

        mock_hat = MagicMock()
        mock_hat.t_in_read.return_value = _TC_OPEN_VALUE
        reader._hat = mock_hat

        received = []
        data_bus.subscribe("EGT1", lambda s: received.append(s))

        reader.start()
        time.sleep(0.3)
        reader.stop()

        assert len(received) > 0
        assert all(s.validity == SampleValidity.INVALID for s in received)

    def test_set_sample_rate_during_acquisition(self):
        """Sample rate can be changed while acquisition is running."""
        data_bus = DataBus()
        channels = [_make_tc_channel("EGT1", 0, rate=5.0)]

        with patch(
            "rotax_dyno_daq.acquisition.hat_reader.DAQHATS_AVAILABLE", False
        ):
            reader = ThermocoupleReader(0, channels, data_bus)

        mock_hat = MagicMock()
        mock_hat.t_in_read.return_value = 400.0
        reader._hat = mock_hat

        reader.start()
        time.sleep(0.15)

        # Change rate while running
        reader.set_sample_rate(10.0)
        assert reader.sample_rate_hz == 10.0

        time.sleep(0.15)
        reader.stop()

    def test_channel_id_lookup_fallback(self):
        """Unknown channel number uses fallback ID format."""
        data_bus = DataBus()
        channels = [_make_tc_channel("EGT1", 0)]

        with patch(
            "rotax_dyno_daq.acquisition.hat_reader.DAQHATS_AVAILABLE", False
        ):
            reader = ThermocoupleReader(0, channels, data_bus)

        mock_hat = MagicMock()
        mock_hat.t_in_read.return_value = 300.0
        reader._hat = mock_hat

        # Read from channel 3 which is not in our config
        sample = reader.read_sample(3)
        assert sample.channel_id == "mcc134_0_ch3"

    def test_thermocouple_reader_timestamp_is_set(self):
        """Samples have a non-zero timestamp."""
        data_bus = DataBus()
        channels = [_make_tc_channel("EGT1", 0)]

        with patch(
            "rotax_dyno_daq.acquisition.hat_reader.DAQHATS_AVAILABLE", False
        ):
            reader = ThermocoupleReader(0, channels, data_bus)

        mock_hat = MagicMock()
        mock_hat.t_in_read.return_value = 100.0
        reader._hat = mock_hat

        sample = reader.read_sample(0)
        assert sample.timestamp_ms > 0
