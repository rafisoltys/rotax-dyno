"""Core data models for the Rotax Dyno DAQ system."""

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional

from rotax_dyno_daq.core.enums import (
    AlarmSeverity,
    AlarmState,
    CalibrationType,
    ChannelType,
    SampleValidity,
    UploadStatus,
)


# --- Core Sample Models ---


@dataclass
class RawSample:
    """A raw sensor reading before calibration."""

    channel_id: str
    timestamp_ms: float  # milliseconds since run start (or epoch if no run)
    raw_value: float
    validity: SampleValidity = SampleValidity.VALID


@dataclass
class CalibratedSample:
    """A sensor reading after calibration to engineering units."""

    channel_id: str
    timestamp_ms: float
    raw_value: float
    calibrated_value: float
    unit: str
    validity: SampleValidity = SampleValidity.VALID


# --- Calibration Models ---


@dataclass
class LinearCalibrationParams:
    """Parameters for linear calibration: y = slope * x + offset."""

    slope: float
    offset: float


@dataclass
class LookupTableParams:
    """Parameters for lookup table calibration with piecewise linear interpolation."""

    points: list[tuple[float, float]]  # (voltage, engineering_unit) pairs, 2-64 entries


@dataclass
class CalibrationProfile:
    """Complete calibration configuration for a channel."""

    calibration_type: CalibrationType
    unit_label: str
    min_valid_voltage: float
    max_valid_voltage: float
    linear_params: Optional[LinearCalibrationParams] = None
    lookup_params: Optional[LookupTableParams] = None


# --- Channel Configuration ---


@dataclass
class ChannelConfig:
    """Configuration for a single sensor channel."""

    channel_id: str
    channel_type: ChannelType
    hat_address: int
    hat_channel: int
    sample_rate_hz: float
    calibration: CalibrationProfile
    display_name: str = ""
    enabled: bool = True


# --- Alarm Models ---


@dataclass
class AlarmThreshold:
    """Threshold configuration for alarm evaluation."""

    low_warning: Optional[float] = None
    low_critical: Optional[float] = None
    high_warning: Optional[float] = None
    high_critical: Optional[float] = None
    deadband: float = 0.0


@dataclass
class AlarmConfig:
    """Alarm configuration for a channel."""

    channel_id: str
    thresholds: AlarmThreshold
    enabled: bool = True


@dataclass
class ActiveAlarm:
    """An active alarm condition."""

    alarm_id: str
    channel_id: str
    severity: AlarmSeverity
    triggered_at: datetime
    value: float
    threshold_crossed: float
    state: AlarmState = AlarmState.ACTIVE


# --- Run Models ---


@dataclass
class RunInfo:
    """Metadata for starting a new run."""

    name: str  # 1-100 characters
    notes: str = ""  # up to 1000 characters
    tags: list[str] = field(default_factory=list)  # up to 10 tags, each up to 50 chars
    operator: str = ""


@dataclass
class RunSummary:
    """Summary statistics for a completed run."""

    run_id: str
    name: str
    start_time: datetime
    end_time: datetime
    duration_seconds: float
    sample_counts: dict[str, int]  # channel_id -> count
    min_values: dict[str, float]  # channel_id -> min
    max_values: dict[str, float]  # channel_id -> max
    mean_values: dict[str, float]  # channel_id -> mean
    notes: str = ""
    tags: list[str] = field(default_factory=list)
    csv_path: Optional[Path] = None
    upload_status: UploadStatus = UploadStatus.PENDING


# --- Cloud Models ---


@dataclass
class CloudConfig:
    """Configuration for cloud storage upload."""

    endpoint_url: str
    bucket_name: str
    access_key: str
    secret_key: str
    destination_prefix: str = ""
    upload_timeout_seconds: int = 300
    max_retries: int = 10
    retry_interval_seconds: int = 60
    max_queue_size: int = 100


@dataclass
class UploadTask:
    """A file queued for cloud upload."""

    file_path: Path
    run_id: str
    status: UploadStatus = UploadStatus.PENDING
    attempts: int = 0
    last_attempt: Optional[datetime] = None
    error_message: str = ""


# --- Post-Processing Models ---


@dataclass
class PostProcessConfig:
    """Configuration for post-processing a recorded run."""

    source_path: Path
    channels_to_process: list[str]
    low_pass_cutoff_hz: Optional[float] = None  # 0.1 to Nyquist
    moving_average_window: Optional[int] = None  # 3 to 101, odd
    calculate_egt_spread: bool = False
    calculate_rate_of_change: list[str] = field(default_factory=list)


# --- System Configuration ---


@dataclass
class SystemConfig:
    """Top-level system configuration."""

    channels: list[ChannelConfig] = field(default_factory=list)
    alarms: list[AlarmConfig] = field(default_factory=list)
    cloud: Optional[CloudConfig] = None
    csv_directory: Path = Path("/home/pi/dyno_data")
    fallback_csv_directory: Optional[Path] = None
    web_server_port: int = 8080
    max_remote_connections: int = 3
    dashboard_time_window_seconds: int = 60
    disk_space_warning_mb: int = 50
