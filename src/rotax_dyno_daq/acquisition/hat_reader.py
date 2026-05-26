"""HAT reader base class and implementations for MCC DAQ HATs.

Provides the abstract HatReader base class and concrete implementations
for the MCC 134 (thermocouple) and MCC 118 (analog voltage) HATs.
The daqhats library may not be available on dev machines, so the design
uses a mock-friendly approach with conditional imports.
"""

from __future__ import annotations

import logging
import threading
import time
from abc import ABC, abstractmethod
from typing import Any, Protocol

from rotax_dyno_daq.calibration.rate_config import clamp_sample_rate
from rotax_dyno_daq.core.data_bus import DataBus
from rotax_dyno_daq.core.enums import ChannelType, SampleValidity
from rotax_dyno_daq.core.models import ChannelConfig, RawSample

logger = logging.getLogger(__name__)


# --- Conditional daqhats import ---

DAQHATS_AVAILABLE = False
mcc134: Any = None
mcc118: Any = None

try:
    from daqhats import mcc134, mcc118  # type: ignore[import-not-found,no-redef]

    DAQHATS_AVAILABLE = True
except ImportError:
    pass


# --- Hardware abstraction protocols for mock-friendly design ---


class Mcc118Interface(Protocol):
    """Protocol for MCC 118 HAT interface (mock-friendly)."""

    def a_in_read(self, channel: int, options: int = 0) -> float:
        """Read a single analog voltage from the specified channel."""
        ...


class Mcc134Interface(Protocol):
    """Protocol for MCC 134 HAT interface (mock-friendly)."""

    def t_in_read(self, channel: int) -> float:
        """Read a thermocouple temperature from the specified channel."""
        ...


# --- Constants ---

# daqhats open-thermocouple sentinel value
_TC_OPEN_VALUE = -9999.0
TC_OPEN_VALUE = _TC_OPEN_VALUE  # alias for external use

# MCC 118 input voltage range
MCC118_MIN_VOLTAGE = 0.0
MCC118_MAX_VOLTAGE = 5.0


# --- Base class ---


class HatReader(ABC):
    """Base class for HAT acquisition threads.

    Manages a dedicated background thread that polls the hardware at
    the configured sample rate and publishes RawSample objects to the
    DataBus.

    Subclasses must implement `read_sample()` to perform the actual
    hardware read for a given channel.
    """

    def __init__(
        self,
        address: int,
        channels: list[ChannelConfig],
        data_bus: DataBus,
    ) -> None:
        """Initialize the HAT reader.

        Args:
            address: The HAT board address (0-7).
            channels: List of channel configurations for this HAT.
            data_bus: The data bus to publish samples to.
        """
        self._address = address
        self._channels = channels
        self._data_bus = data_bus
        self._running = False
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()
        self._sample_rate_hz: float = self._compute_effective_rate()

    @abstractmethod
    def read_sample(self, channel: int | ChannelConfig) -> RawSample:
        """Read a single raw sample from the hardware.

        Args:
            channel: The channel number or channel configuration to read from.

        Returns:
            A RawSample with the raw reading and validity status.
        """
        ...

    @property
    def sample_rate_hz(self) -> float:
        """The current effective sample rate in Hz."""
        return self._sample_rate_hz

    @sample_rate_hz.setter
    def sample_rate_hz(self, value: float) -> None:
        """Set the sample rate directly (for testing)."""
        self._sample_rate_hz = value

    def start(self) -> None:
        """Start the acquisition loop in a background thread."""
        with self._lock:
            if self._running:
                return
            self._running = True
            self._thread = threading.Thread(
                target=self._acquisition_loop,
                name=f"HatReader-{self._address}",
                daemon=True,
            )
            self._thread.start()
            logger.info(
                "Started HAT reader at address %d, rate=%.1f Hz",
                self._address,
                self._sample_rate_hz,
            )

    def stop(self) -> None:
        """Stop acquisition and release hardware resources."""
        with self._lock:
            if not self._running:
                return
            self._running = False

        if self._thread is not None:
            self._thread.join(timeout=5.0)
            self._thread = None
        logger.info("Stopped HAT reader at address %d", self._address)

    def set_sample_rate(self, rate_hz: float) -> None:
        """Update the sampling rate without stopping acquisition.

        The rate is clamped to the valid range for the channel type.
        The new rate takes effect on the next iteration of the
        acquisition loop.

        Args:
            rate_hz: The new sample rate in Hz.
        """
        if self._channels:
            # Clamp to the valid range for the primary channel type
            channel_type = self._channels[0].channel_type
            clamped = clamp_sample_rate(channel_type, rate_hz)
        else:
            clamped = rate_hz

        with self._lock:
            self._sample_rate_hz = clamped

    @property
    def is_running(self) -> bool:
        """Whether the acquisition loop is currently running."""
        return self._running

    @property
    def address(self) -> int:
        """The HAT board address."""
        return self._address

    @property
    def channels(self) -> list[ChannelConfig]:
        """The channel configurations for this HAT."""
        return list(self._channels)

    def _get_channel_id(self, channel_num: int) -> str:
        """Look up the channel_id for a given channel number.

        Falls back to a generated ID if the channel is not in the config.

        Args:
            channel_num: The hardware channel number.

        Returns:
            The channel_id string.
        """
        for ch_config in self._channels:
            if ch_config.hat_channel == channel_num:
                return ch_config.channel_id
        return f"hat_{self._address}_ch{channel_num}"

    def _compute_effective_rate(self) -> float:
        """Compute the effective sample rate from channel configs.

        Uses the maximum configured rate among all channels, clamped
        to the valid range for the channel type.
        """
        if not self._channels:
            return 10.0  # fallback default

        rates = []
        for ch in self._channels:
            clamped = clamp_sample_rate(ch.channel_type, ch.sample_rate_hz)
            rates.append(clamped)
        return max(rates)

    def _acquisition_loop(self) -> None:
        """Main acquisition loop running in the background thread."""
        while self._running:
            loop_start = time.perf_counter()

            for channel in self._channels:
                if not self._running:
                    break
                if not channel.enabled:
                    continue
                try:
                    sample = self.read_sample(channel.hat_channel)
                    self._data_bus.publish(channel.channel_id, sample)
                except Exception as e:
                    logger.error(
                        "Error reading channel %s: %s",
                        channel.channel_id,
                        e,
                    )

            # Sleep for the remainder of the sample interval
            with self._lock:
                interval = 1.0 / self._sample_rate_hz if self._sample_rate_hz > 0 else 0.1

            elapsed = time.perf_counter() - loop_start
            sleep_time = interval - elapsed
            if sleep_time > 0:
                time.sleep(sleep_time)


# --- ThermocoupleReader ---


class ThermocoupleReader(HatReader):
    """MCC 134 thermocouple reader using daqhats.mcc134.t_in_read().

    Reads thermocouple temperatures with cold junction compensation.
    Detects open-circuit faults (TC_OPEN status) and marks samples as INVALID.

    Args:
        address: The HAT board address (0-7).
        channels: List of channel configurations for this HAT.
        data_bus: The data bus to publish samples to.
    """

    def __init__(
        self,
        address: int,
        channels: list[ChannelConfig],
        data_bus: DataBus,
    ) -> None:
        super().__init__(address=address, channels=channels, data_bus=data_bus)
        self._hat: Any = None

        if DAQHATS_AVAILABLE and mcc134 is not None:
            try:
                self._hat = mcc134(address)
            except Exception as e:
                logger.warning(
                    "Could not create MCC 134 at address %d: %s",
                    address,
                    e,
                )

    def read_sample(self, channel: int | ChannelConfig) -> RawSample:
        """Read a thermocouple temperature from the MCC 134.

        Detects open-circuit faults and marks them as INVALID.

        Args:
            channel: The channel number (0-3).

        Returns:
            A RawSample with the temperature reading and validity status.
        """
        if isinstance(channel, ChannelConfig):
            channel_num = channel.hat_channel
        else:
            channel_num = channel

        timestamp_ms = time.time() * 1000.0
        channel_id = self._get_channel_id(channel_num)

        if self._hat is None:
            return RawSample(
                channel_id=channel_id,
                timestamp_ms=timestamp_ms,
                raw_value=0.0,
                validity=SampleValidity.INVALID,
            )

        try:
            temperature = self._hat.t_in_read(channel_num)
        except Exception as e:
            logger.error(
                "Hardware read error on channel %s (hat_channel=%d): %s",
                channel_id,
                channel_num,
                e,
            )
            return RawSample(
                channel_id=channel_id,
                timestamp_ms=timestamp_ms,
                raw_value=0.0,
                validity=SampleValidity.INVALID,
            )

        # Check for open-circuit fault
        if temperature == _TC_OPEN_VALUE:
            return RawSample(
                channel_id=channel_id,
                timestamp_ms=timestamp_ms,
                raw_value=temperature,
                validity=SampleValidity.INVALID,
            )

        return RawSample(
            channel_id=channel_id,
            timestamp_ms=timestamp_ms,
            raw_value=temperature,
            validity=SampleValidity.VALID,
        )

    def _get_channel_id(self, channel_num: int) -> str:
        """Look up the channel_id for a given channel number.

        Falls back to a generated ID if the channel is not in the config.

        Args:
            channel_num: The hardware channel number.

        Returns:
            The channel_id string.
        """
        for ch_config in self._channels:
            if ch_config.hat_channel == channel_num:
                return ch_config.channel_id
        return f"mcc134_{self._address}_ch{channel_num}"
