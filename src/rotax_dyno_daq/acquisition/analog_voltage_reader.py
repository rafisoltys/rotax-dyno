"""MCC 118 analog voltage reader for pressure, RPM, and AFR channels.

Implements the AnalogVoltageReader class that reads analog voltages from
the MCC 118 HAT and applies channel-type-specific validation logic:

- Pressure/AFR channels: voltage outside [min_valid, max_valid] → INVALID
- RPM channels: voltage below min_valid → report zero (not invalid);
                voltage above max_valid → INVALID

The daqhats library may not be available on dev machines, so the reader
accepts an optional HAT instance for dependency injection (mock-friendly).
"""

from __future__ import annotations

import logging
import time
from typing import Any, Optional

from rotax_dyno_daq.acquisition.hat_reader import (
    DAQHATS_AVAILABLE,
    HatReader,
    Mcc118Interface,
    MCC118_MAX_VOLTAGE,
    MCC118_MIN_VOLTAGE,
    mcc118,
)
from rotax_dyno_daq.core.data_bus import DataBus
from rotax_dyno_daq.core.enums import ChannelType, SampleValidity
from rotax_dyno_daq.core.models import ChannelConfig, RawSample

logger = logging.getLogger(__name__)


class AnalogVoltageReader(HatReader):
    """MCC 118 analog voltage reader using daqhats.mcc118.a_in_read().

    Supports multiple channel types on the same HAT:
    - Pressure (OilP, ChargeP): voltage outside calibration range → INVALID
    - RPM: below min_valid_voltage → zero; above max_valid_voltage → INVALID
    - AFR (AFR1-AFR4): voltage outside calibration range → INVALID

    Publishes RawSample objects to the DataBus. For RPM channels that read
    below the minimum threshold, the raw_value is set to 0.0 with VALID
    status (indicating the engine is not spinning, not a sensor fault).

    Args:
        address: The HAT board address (0-7).
        channels: List of channel configurations for this HAT.
        data_bus: The data bus to publish samples to.
        hat_device: Optional pre-created HAT instance for testing/mocking.
            If None, attempts to create a real MCC 118 instance.
    """

    def __init__(
        self,
        address: int,
        channels: list[ChannelConfig],
        data_bus: DataBus,
        hat_device: Optional[Mcc118Interface] = None,
    ) -> None:
        super().__init__(address=address, channels=channels, data_bus=data_bus)
        self._hat: Optional[Any] = hat_device
        if self._hat is None and DAQHATS_AVAILABLE and mcc118 is not None:
            try:
                self._hat = mcc118(address)
            except Exception as e:
                logger.warning(
                    "Could not create MCC 118 at address %d: %s. "
                    "Using mock-friendly mode (inject hat_device manually).",
                    address,
                    e,
                )

    def read_sample(self, channel: int | ChannelConfig) -> RawSample:
        """Read a single analog voltage sample from the MCC 118.

        Applies channel-type-specific validation:
        - RPM: below min_valid → raw_value=0.0, VALID (engine not spinning)
        - RPM: above max_valid → INVALID
        - Pressure/AFR: outside [min_valid, max_valid] → INVALID

        Args:
            channel: The channel number (int) or ChannelConfig to read from.

        Returns:
            A RawSample with the voltage reading and validity status.

        Raises:
            RuntimeError: If no HAT device is available.
        """
        # Resolve channel config
        if isinstance(channel, ChannelConfig):
            ch_config = channel
        else:
            ch_config = self._find_channel_config(channel)
            if ch_config is None:
                timestamp_ms = time.time() * 1000.0
                return RawSample(
                    channel_id=f"mcc118_{self._address}_ch{channel}",
                    timestamp_ms=timestamp_ms,
                    raw_value=0.0,
                    validity=SampleValidity.INVALID,
                )

        if self._hat is None:
            raise RuntimeError(
                f"No MCC 118 HAT device available at address {self._address}. "
                "Provide a hat_device for testing or ensure daqhats is installed."
            )

        timestamp_ms = time.time() * 1000.0

        # Read raw voltage from the HAT
        try:
            voltage = self._hat.a_in_read(ch_config.hat_channel)
        except Exception as e:
            logger.error(
                "Hardware read error on channel %s (hat_channel=%d): %s",
                ch_config.channel_id,
                ch_config.hat_channel,
                e,
            )
            return RawSample(
                channel_id=ch_config.channel_id,
                timestamp_ms=timestamp_ms,
                raw_value=0.0,
                validity=SampleValidity.INVALID,
            )

        # Validate voltage against calibration profile thresholds
        calibration = ch_config.calibration
        min_valid = calibration.min_valid_voltage
        max_valid = calibration.max_valid_voltage

        if ch_config.channel_type == ChannelType.RPM:
            return self._validate_rpm(
                ch_config.channel_id, timestamp_ms, voltage, min_valid, max_valid
            )
        else:
            # Pressure and AFR channels: outside range → INVALID
            return self._validate_pressure_afr(
                ch_config.channel_id, timestamp_ms, voltage, min_valid, max_valid
            )

    def _validate_rpm(
        self,
        channel_id: str,
        timestamp_ms: float,
        voltage: float,
        min_valid: float,
        max_valid: float,
    ) -> RawSample:
        """Validate an RPM channel voltage reading.

        RPM-specific logic:
        - Below min_valid_voltage: report as zero (engine not spinning)
        - Above max_valid_voltage: flag as INVALID
        - Within range: report the actual voltage

        Args:
            channel_id: The channel identifier.
            timestamp_ms: Timestamp in milliseconds.
            voltage: The raw voltage reading.
            min_valid: Minimum valid voltage from calibration profile.
            max_valid: Maximum valid voltage from calibration profile.

        Returns:
            A RawSample with appropriate value and validity.
        """
        if voltage < min_valid:
            # Below minimum → report zero (not invalid, engine not spinning)
            return RawSample(
                channel_id=channel_id,
                timestamp_ms=timestamp_ms,
                raw_value=0.0,
                validity=SampleValidity.VALID,
            )
        elif voltage > max_valid:
            # Above maximum → flag as invalid
            return RawSample(
                channel_id=channel_id,
                timestamp_ms=timestamp_ms,
                raw_value=voltage,
                validity=SampleValidity.INVALID,
            )
        else:
            # Within valid range
            return RawSample(
                channel_id=channel_id,
                timestamp_ms=timestamp_ms,
                raw_value=voltage,
                validity=SampleValidity.VALID,
            )

    def _validate_pressure_afr(
        self,
        channel_id: str,
        timestamp_ms: float,
        voltage: float,
        min_valid: float,
        max_valid: float,
    ) -> RawSample:
        """Validate a pressure or AFR channel voltage reading.

        Pressure/AFR logic:
        - Outside [min_valid, max_valid] → INVALID
        - Within range → VALID

        Args:
            channel_id: The channel identifier.
            timestamp_ms: Timestamp in milliseconds.
            voltage: The raw voltage reading.
            min_valid: Minimum valid voltage from calibration profile.
            max_valid: Maximum valid voltage from calibration profile.

        Returns:
            A RawSample with appropriate value and validity.
        """
        if voltage < min_valid or voltage > max_valid:
            return RawSample(
                channel_id=channel_id,
                timestamp_ms=timestamp_ms,
                raw_value=voltage,
                validity=SampleValidity.INVALID,
            )
        else:
            return RawSample(
                channel_id=channel_id,
                timestamp_ms=timestamp_ms,
                raw_value=voltage,
                validity=SampleValidity.VALID,
            )

    def _find_channel_config(self, channel_num: int) -> Optional[ChannelConfig]:
        """Find the ChannelConfig for a given hardware channel number.

        Args:
            channel_num: The hardware channel number.

        Returns:
            The matching ChannelConfig, or None if not found.
        """
        for ch_config in self._channels:
            if ch_config.hat_channel == channel_num:
                return ch_config
        return None
