"""Unit tests for core data models and enumerations."""

from datetime import datetime
from pathlib import Path

from rotax_dyno_daq.core.enums import (
    AlarmSeverity,
    AlarmState,
    CalibrationType,
    ChannelType,
    SampleValidity,
    UploadStatus,
)
from rotax_dyno_daq.core.models import (
    ActiveAlarm,
    AlarmConfig,
    AlarmThreshold,
    CalibrationProfile,
    CalibratedSample,
    ChannelConfig,
    CloudConfig,
    LinearCalibrationParams,
    LookupTableParams,
    PostProcessConfig,
    RawSample,
    RunInfo,
    RunSummary,
    SystemConfig,
    UploadTask,
)


class TestEnumerations:
    """Tests for all enumeration types."""

    def test_channel_type_values(self):
        assert ChannelType.THERMOCOUPLE.value == "thermocouple"
        assert ChannelType.PRESSURE.value == "pressure"
        assert ChannelType.RPM.value == "rpm"
        assert ChannelType.AFR.value == "afr"

    def test_calibration_type_values(self):
        assert CalibrationType.LINEAR.value == "linear"
        assert CalibrationType.LOOKUP_TABLE.value == "lookup_table"

    def test_alarm_severity_values(self):
        assert AlarmSeverity.NORMAL.value == "normal"
        assert AlarmSeverity.WARNING.value == "warning"
        assert AlarmSeverity.CRITICAL.value == "critical"

    def test_alarm_state_values(self):
        assert AlarmState.INACTIVE.value == "inactive"
        assert AlarmState.ACTIVE.value == "active"
        assert AlarmState.ACKNOWLEDGED.value == "acknowledged"

    def test_upload_status_values(self):
        assert UploadStatus.PENDING.value == "pending"
        assert UploadStatus.IN_PROGRESS.value == "in_progress"
        assert UploadStatus.COMPLETED.value == "completed"
        assert UploadStatus.FAILED.value == "failed"

    def test_sample_validity_values(self):
        assert SampleValidity.VALID.value == "valid"
        assert SampleValidity.INVALID.value == "invalid"
        assert SampleValidity.OUT_OF_RANGE.value == "out_of_range"
        assert SampleValidity.STALE.value == "stale"
        assert SampleValidity.UNCALIBRATED.value == "uncalibrated"


class TestRawSample:
    """Tests for RawSample dataclass."""

    def test_creation_with_defaults(self):
        sample = RawSample(channel_id="EGT1", timestamp_ms=1000.0, raw_value=2.5)
        assert sample.channel_id == "EGT1"
        assert sample.timestamp_ms == 1000.0
        assert sample.raw_value == 2.5
        assert sample.validity == SampleValidity.VALID

    def test_creation_with_explicit_validity(self):
        sample = RawSample(
            channel_id="OilP",
            timestamp_ms=500.0,
            raw_value=-0.1,
            validity=SampleValidity.INVALID,
        )
        assert sample.validity == SampleValidity.INVALID


class TestCalibratedSample:
    """Tests for CalibratedSample dataclass."""

    def test_creation_with_defaults(self):
        sample = CalibratedSample(
            channel_id="EGT1",
            timestamp_ms=1000.0,
            raw_value=2.5,
            calibrated_value=650.0,
            unit="°C",
        )
        assert sample.calibrated_value == 650.0
        assert sample.unit == "°C"
        assert sample.validity == SampleValidity.VALID

    def test_creation_with_out_of_range(self):
        sample = CalibratedSample(
            channel_id="OilP",
            timestamp_ms=200.0,
            raw_value=5.1,
            calibrated_value=10.0,
            unit="bar",
            validity=SampleValidity.OUT_OF_RANGE,
        )
        assert sample.validity == SampleValidity.OUT_OF_RANGE


class TestCalibrationModels:
    """Tests for calibration-related models."""

    def test_linear_calibration_params(self):
        params = LinearCalibrationParams(slope=2.0, offset=-1.0)
        assert params.slope == 2.0
        assert params.offset == -1.0

    def test_lookup_table_params(self):
        points = [(0.0, 0.0), (2.5, 50.0), (5.0, 100.0)]
        params = LookupTableParams(points=points)
        assert len(params.points) == 3
        assert params.points[1] == (2.5, 50.0)

    def test_calibration_profile_linear(self):
        profile = CalibrationProfile(
            calibration_type=CalibrationType.LINEAR,
            unit_label="bar",
            min_valid_voltage=0.5,
            max_valid_voltage=4.5,
            linear_params=LinearCalibrationParams(slope=2.5, offset=-1.25),
        )
        assert profile.calibration_type == CalibrationType.LINEAR
        assert profile.linear_params is not None
        assert profile.lookup_params is None

    def test_calibration_profile_lookup_table(self):
        profile = CalibrationProfile(
            calibration_type=CalibrationType.LOOKUP_TABLE,
            unit_label="kPa",
            min_valid_voltage=0.0,
            max_valid_voltage=5.0,
            lookup_params=LookupTableParams(points=[(0.0, 0.0), (5.0, 500.0)]),
        )
        assert profile.calibration_type == CalibrationType.LOOKUP_TABLE
        assert profile.lookup_params is not None
        assert profile.linear_params is None


class TestChannelConfig:
    """Tests for ChannelConfig dataclass."""

    def test_creation_with_defaults(self):
        profile = CalibrationProfile(
            calibration_type=CalibrationType.LINEAR,
            unit_label="°C",
            min_valid_voltage=0.0,
            max_valid_voltage=5.0,
            linear_params=LinearCalibrationParams(slope=200.0, offset=0.0),
        )
        config = ChannelConfig(
            channel_id="EGT1",
            channel_type=ChannelType.THERMOCOUPLE,
            hat_address=0,
            hat_channel=0,
            sample_rate_hz=5.0,
            calibration=profile,
        )
        assert config.display_name == ""
        assert config.enabled is True

    def test_creation_with_all_fields(self):
        profile = CalibrationProfile(
            calibration_type=CalibrationType.LINEAR,
            unit_label="bar",
            min_valid_voltage=0.5,
            max_valid_voltage=4.5,
            linear_params=LinearCalibrationParams(slope=2.5, offset=-1.25),
        )
        config = ChannelConfig(
            channel_id="OilP",
            channel_type=ChannelType.PRESSURE,
            hat_address=1,
            hat_channel=2,
            sample_rate_hz=10.0,
            calibration=profile,
            display_name="Oil Pressure",
            enabled=False,
        )
        assert config.display_name == "Oil Pressure"
        assert config.enabled is False


class TestAlarmModels:
    """Tests for alarm-related models."""

    def test_alarm_threshold_defaults(self):
        threshold = AlarmThreshold()
        assert threshold.low_warning is None
        assert threshold.low_critical is None
        assert threshold.high_warning is None
        assert threshold.high_critical is None
        assert threshold.deadband == 0.0

    def test_alarm_threshold_with_values(self):
        threshold = AlarmThreshold(
            low_warning=2.0,
            low_critical=1.0,
            high_warning=8.0,
            high_critical=9.0,
            deadband=0.5,
        )
        assert threshold.high_critical == 9.0
        assert threshold.deadband == 0.5

    def test_alarm_config(self):
        config = AlarmConfig(
            channel_id="OilP",
            thresholds=AlarmThreshold(low_critical=1.0, high_critical=9.0),
        )
        assert config.enabled is True

    def test_active_alarm(self):
        now = datetime.now()
        alarm = ActiveAlarm(
            alarm_id="alarm-001",
            channel_id="EGT1",
            severity=AlarmSeverity.CRITICAL,
            triggered_at=now,
            value=900.0,
            threshold_crossed=850.0,
        )
        assert alarm.state == AlarmState.ACTIVE
        assert alarm.severity == AlarmSeverity.CRITICAL


class TestRunModels:
    """Tests for run-related models."""

    def test_run_info_defaults(self):
        info = RunInfo(name="Test Run 1")
        assert info.notes == ""
        assert info.tags == []
        assert info.operator == ""

    def test_run_info_with_all_fields(self):
        info = RunInfo(
            name="Full Power Test",
            notes="Engine at full throttle",
            tags=["full-power", "baseline"],
            operator="John",
        )
        assert len(info.tags) == 2

    def test_run_summary(self):
        now = datetime.now()
        summary = RunSummary(
            run_id="run-001",
            name="Test Run",
            start_time=now,
            end_time=now,
            duration_seconds=120.0,
            sample_counts={"EGT1": 600, "OilP": 1200},
            min_values={"EGT1": 400.0, "OilP": 2.0},
            max_values={"EGT1": 850.0, "OilP": 6.5},
            mean_values={"EGT1": 625.0, "OilP": 4.2},
        )
        assert summary.upload_status == UploadStatus.PENDING
        assert summary.csv_path is None


class TestCloudModels:
    """Tests for cloud-related models."""

    def test_cloud_config_defaults(self):
        config = CloudConfig(
            endpoint_url="https://s3.example.com",
            bucket_name="dyno-data",
            access_key="AKID",
            secret_key="SECRET",
        )
        assert config.destination_prefix == ""
        assert config.upload_timeout_seconds == 300
        assert config.max_retries == 10
        assert config.retry_interval_seconds == 60
        assert config.max_queue_size == 100

    def test_upload_task_defaults(self):
        task = UploadTask(
            file_path=Path("/data/run1.csv"),
            run_id="run-001",
        )
        assert task.status == UploadStatus.PENDING
        assert task.attempts == 0
        assert task.last_attempt is None
        assert task.error_message == ""


class TestPostProcessConfig:
    """Tests for PostProcessConfig dataclass."""

    def test_defaults(self):
        config = PostProcessConfig(
            source_path=Path("/data/run1.csv"),
            channels_to_process=["EGT1", "EGT2"],
        )
        assert config.low_pass_cutoff_hz is None
        assert config.moving_average_window is None
        assert config.calculate_egt_spread is False
        assert config.calculate_rate_of_change == []

    def test_with_all_options(self):
        config = PostProcessConfig(
            source_path=Path("/data/run1.csv"),
            channels_to_process=["EGT1", "EGT2", "EGT3", "EGT4"],
            low_pass_cutoff_hz=2.0,
            moving_average_window=5,
            calculate_egt_spread=True,
            calculate_rate_of_change=["EGT1", "OilP"],
        )
        assert config.calculate_egt_spread is True
        assert len(config.calculate_rate_of_change) == 2


class TestSystemConfig:
    """Tests for SystemConfig dataclass."""

    def test_defaults(self):
        config = SystemConfig()
        assert config.channels == []
        assert config.alarms == []
        assert config.cloud is None
        assert config.csv_directory == Path("/home/pi/dyno_data")
        assert config.fallback_csv_directory is None
        assert config.web_server_port == 8080
        assert config.max_remote_connections == 3
        assert config.dashboard_time_window_seconds == 60
        assert config.disk_space_warning_mb == 50

    def test_mutable_defaults_are_independent(self):
        config1 = SystemConfig()
        config2 = SystemConfig()
        config1.channels.append(None)  # type: ignore
        assert len(config2.channels) == 0
