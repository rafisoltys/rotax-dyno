"""Core enumerations for the Rotax Dyno DAQ system."""

from enum import Enum


class ChannelType(Enum):
    """Type of sensor channel."""

    THERMOCOUPLE = "thermocouple"
    PRESSURE = "pressure"
    RPM = "rpm"
    AFR = "afr"


class CalibrationType(Enum):
    """Type of calibration applied to a channel."""

    LINEAR = "linear"
    LOOKUP_TABLE = "lookup_table"


class AlarmSeverity(Enum):
    """Severity level of an alarm condition."""

    NORMAL = "normal"
    WARNING = "warning"
    CRITICAL = "critical"


class AlarmState(Enum):
    """Current state of an alarm."""

    INACTIVE = "inactive"
    ACTIVE = "active"
    ACKNOWLEDGED = "acknowledged"


class UploadStatus(Enum):
    """Status of a cloud upload task."""

    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    FAILED = "failed"


class SampleValidity(Enum):
    """Validity state of a sensor sample."""

    VALID = "valid"
    INVALID = "invalid"
    OUT_OF_RANGE = "out_of_range"
    STALE = "stale"
    UNCALIBRATED = "uncalibrated"
