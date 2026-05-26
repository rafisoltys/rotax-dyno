"""Calibration engine - converts raw sensor readings to engineering units."""

from rotax_dyno_daq.calibration.rate_config import (
    clamp_sample_rate,
    get_default_rate,
    get_rate_range,
)

__all__ = [
    "clamp_sample_rate",
    "get_default_rate",
    "get_rate_range",
]
