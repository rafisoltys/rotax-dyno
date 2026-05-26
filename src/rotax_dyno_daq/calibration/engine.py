"""Calibration engine - applies calibration profiles to raw sensor readings."""

from __future__ import annotations

import bisect
from dataclasses import dataclass
from typing import Optional

from rotax_dyno_daq.core.enums import CalibrationType, SampleValidity
from rotax_dyno_daq.core.models import (
    CalibrationProfile,
    CalibratedSample,
)


@dataclass
class ValidationResult:
    """Result of calibration profile validation."""

    valid: bool
    errors: list[str]


@dataclass
class LinearCalibration:
    """Linear calibration: y = slope * x + offset."""

    slope: float
    offset: float

    def convert(self, raw: float) -> float:
        """Convert a raw value using linear calibration.

        Returns:
            The calibrated value: slope * raw + offset
        """
        return self.slope * raw + self.offset


@dataclass
class LookupTableCalibration:
    """Piecewise linear interpolation from voltage-to-unit pairs.

    Points must be sorted by voltage (ascending). If the raw value is
    outside the table range, the output is clamped to the nearest
    boundary value.
    """

    points: list[tuple[float, float]]  # (voltage, engineering_unit) sorted by voltage

    def __post_init__(self) -> None:
        """Pre-compute sorted voltages for efficient lookup."""
        # Ensure points are sorted by voltage
        self.points = sorted(self.points, key=lambda p: p[0])
        self._voltages = [p[0] for p in self.points]
        self._values = [p[1] for p in self.points]

    def convert(self, raw: float) -> tuple[float, bool]:
        """Convert a raw voltage using piecewise linear interpolation.

        Args:
            raw: The raw voltage value to convert.

        Returns:
            A tuple of (calibrated_value, out_of_range) where out_of_range
            is True if the raw value was clamped to a boundary.
        """
        # Clamp below minimum
        if raw <= self._voltages[0]:
            out_of_range = raw < self._voltages[0]
            return self._values[0], out_of_range

        # Clamp above maximum
        if raw >= self._voltages[-1]:
            out_of_range = raw > self._voltages[-1]
            return self._values[-1], out_of_range

        # Find the bracketing interval using bisect
        idx = bisect.bisect_right(self._voltages, raw)
        # idx is the index of the first voltage > raw
        # So the interval is [idx-1, idx]
        v_low = self._voltages[idx - 1]
        v_high = self._voltages[idx]
        u_low = self._values[idx - 1]
        u_high = self._values[idx]

        # Linear interpolation
        fraction = (raw - v_low) / (v_high - v_low)
        calibrated = u_low + fraction * (u_high - u_low)
        return calibrated, False


class CalibrationEngine:
    """Applies calibration profiles to raw sensor readings.

    Manages per-channel calibration profiles and converts raw voltage
    readings to engineering units using the appropriate calibration method.
    """

    def __init__(self) -> None:
        """Initialize the calibration engine with no profiles."""
        self._profiles: dict[str, CalibrationProfile] = {}
        self._linear_cals: dict[str, LinearCalibration] = {}
        self._lookup_cals: dict[str, LookupTableCalibration] = {}

    def apply(self, channel_id: str, raw_value: float, timestamp_ms: float = 0.0) -> CalibratedSample:
        """Apply the channel's calibration profile to a raw value.

        Args:
            channel_id: The channel identifier.
            raw_value: The raw voltage/reading from the sensor.
            timestamp_ms: Timestamp in milliseconds.

        Returns:
            A CalibratedSample with the converted value and validity status.

        Raises:
            KeyError: If no calibration profile is configured for the channel.
        """
        profile = self._profiles.get(channel_id)
        if profile is None:
            return CalibratedSample(
                channel_id=channel_id,
                timestamp_ms=timestamp_ms,
                raw_value=raw_value,
                calibrated_value=raw_value,
                unit="V",
                validity=SampleValidity.UNCALIBRATED,
            )

        # Check if raw value is within valid voltage range
        if raw_value < profile.min_valid_voltage or raw_value > profile.max_valid_voltage:
            return CalibratedSample(
                channel_id=channel_id,
                timestamp_ms=timestamp_ms,
                raw_value=raw_value,
                calibrated_value=0.0,
                unit=profile.unit_label,
                validity=SampleValidity.INVALID,
            )

        # Apply calibration based on type
        if profile.calibration_type == CalibrationType.LINEAR:
            linear_cal = self._linear_cals.get(channel_id)
            if linear_cal is None:
                # Should not happen if profile was properly set up
                return CalibratedSample(
                    channel_id=channel_id,
                    timestamp_ms=timestamp_ms,
                    raw_value=raw_value,
                    calibrated_value=raw_value,
                    unit=profile.unit_label,
                    validity=SampleValidity.UNCALIBRATED,
                )
            calibrated_value = linear_cal.convert(raw_value)
            return CalibratedSample(
                channel_id=channel_id,
                timestamp_ms=timestamp_ms,
                raw_value=raw_value,
                calibrated_value=calibrated_value,
                unit=profile.unit_label,
                validity=SampleValidity.VALID,
            )

        elif profile.calibration_type == CalibrationType.LOOKUP_TABLE:
            lookup_cal = self._lookup_cals.get(channel_id)
            if lookup_cal is None:
                return CalibratedSample(
                    channel_id=channel_id,
                    timestamp_ms=timestamp_ms,
                    raw_value=raw_value,
                    calibrated_value=raw_value,
                    unit=profile.unit_label,
                    validity=SampleValidity.UNCALIBRATED,
                )
            calibrated_value, out_of_range = lookup_cal.convert(raw_value)
            validity = SampleValidity.OUT_OF_RANGE if out_of_range else SampleValidity.VALID
            return CalibratedSample(
                channel_id=channel_id,
                timestamp_ms=timestamp_ms,
                raw_value=raw_value,
                calibrated_value=calibrated_value,
                unit=profile.unit_label,
                validity=validity,
            )

        # Unknown calibration type
        return CalibratedSample(
            channel_id=channel_id,
            timestamp_ms=timestamp_ms,
            raw_value=raw_value,
            calibrated_value=raw_value,
            unit=profile.unit_label,
            validity=SampleValidity.UNCALIBRATED,
        )

    def update_profile(self, channel_id: str, profile: CalibrationProfile) -> None:
        """Hot-swap a calibration profile (takes effect on next sample).

        Args:
            channel_id: The channel identifier.
            profile: The new calibration profile to apply.
        """
        self._profiles[channel_id] = profile
        self._build_calibration(channel_id, profile)

    def validate_profile(self, profile: CalibrationProfile) -> ValidationResult:
        """Validate a calibration profile before applying.

        Args:
            profile: The calibration profile to validate.

        Returns:
            A ValidationResult indicating whether the profile is valid.
        """
        errors: list[str] = []

        if profile.min_valid_voltage >= profile.max_valid_voltage:
            errors.append(
                "min_valid_voltage must be less than max_valid_voltage"
            )

        if profile.calibration_type == CalibrationType.LINEAR:
            if profile.linear_params is None:
                errors.append(
                    "linear_params must be provided for LINEAR calibration type"
                )

        elif profile.calibration_type == CalibrationType.LOOKUP_TABLE:
            if profile.lookup_params is None:
                errors.append(
                    "lookup_params must be provided for LOOKUP_TABLE calibration type"
                )
            else:
                points = profile.lookup_params.points
                if len(points) < 2:
                    errors.append(
                        "Lookup table must have at least 2 points"
                    )
                elif len(points) > 64:
                    errors.append(
                        "Lookup table must have at most 64 points"
                    )
                else:
                    # Check for duplicate voltages
                    voltages = [p[0] for p in points]
                    if len(set(voltages)) != len(voltages):
                        errors.append(
                            "Lookup table must not contain duplicate voltage entries"
                        )

        return ValidationResult(valid=len(errors) == 0, errors=errors)

    def _build_calibration(self, channel_id: str, profile: CalibrationProfile) -> None:
        """Build the internal calibration object from a profile.

        Args:
            channel_id: The channel identifier.
            profile: The calibration profile to build from.
        """
        # Clear any existing calibration for this channel
        self._linear_cals.pop(channel_id, None)
        self._lookup_cals.pop(channel_id, None)

        if profile.calibration_type == CalibrationType.LINEAR:
            if profile.linear_params is not None:
                self._linear_cals[channel_id] = LinearCalibration(
                    slope=profile.linear_params.slope,
                    offset=profile.linear_params.offset,
                )

        elif profile.calibration_type == CalibrationType.LOOKUP_TABLE:
            if profile.lookup_params is not None:
                self._lookup_cals[channel_id] = LookupTableCalibration(
                    points=list(profile.lookup_params.points),
                )
