"""Sample rate configuration and clamping for each channel type.

Provides functions to validate, clamp, and retrieve default sample rates
based on channel type. Each channel type has a defined valid range and
default rate per the system requirements.
"""

from rotax_dyno_daq.core.enums import ChannelType


# Rate configuration: (min_hz, max_hz, default_hz) per channel type
_RATE_CONFIG: dict[ChannelType, tuple[float, float, float]] = {
    ChannelType.THERMOCOUPLE: (1.0, 10.0, 5.0),
    ChannelType.PRESSURE: (10.0, 100.0, 10.0),
    ChannelType.RPM: (10.0, 100.0, 50.0),
    ChannelType.AFR: (10.0, 50.0, 20.0),
}


def get_rate_range(channel_type: ChannelType) -> tuple[float, float]:
    """Get the valid sample rate range for a channel type.

    Args:
        channel_type: The type of sensor channel.

    Returns:
        A tuple of (min_hz, max_hz) defining the valid rate range.
    """
    min_hz, max_hz, _ = _RATE_CONFIG[channel_type]
    return (min_hz, max_hz)


def get_default_rate(channel_type: ChannelType) -> float:
    """Get the default sample rate for a channel type.

    Args:
        channel_type: The type of sensor channel.

    Returns:
        The default sample rate in Hz.
    """
    _, _, default_hz = _RATE_CONFIG[channel_type]
    return default_hz


def clamp_sample_rate(channel_type: ChannelType, rate: float | None) -> float:
    """Clamp a sample rate to the valid range for a channel type.

    If rate is None, returns the default rate for the channel type.
    If rate is provided, clamps it to the valid [min, max] range.

    Args:
        channel_type: The type of sensor channel.
        rate: The requested sample rate in Hz, or None for default.

    Returns:
        The effective sample rate in Hz, clamped to the valid range.
    """
    min_hz, max_hz, default_hz = _RATE_CONFIG[channel_type]

    if rate is None:
        return default_hz

    return max(min_hz, min(rate, max_hz))
