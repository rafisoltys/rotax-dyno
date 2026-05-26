"""Unit tests for the ConfigurationManager class."""

import threading
import time
from pathlib import Path
from unittest.mock import patch

import pytest
import tomli_w

from rotax_dyno_daq.config.manager import (
    ConfigurationManager,
    ConfigValidationResult,
    _config_to_dict,
    _dict_to_config,
    _get_factory_defaults,
    _validate_config,
)
from rotax_dyno_daq.core.enums import CalibrationType, ChannelType
from rotax_dyno_daq.core.models import (
    AlarmConfig,
    AlarmThreshold,
    CalibrationProfile,
    ChannelConfig,
    CloudConfig,
    LinearCalibrationParams,
    LookupTableParams,
    SystemConfig,
)


@pytest.fixture
def tmp_config_path(tmp_path: Path) -> Path:
    """Return a temporary config file path."""
    return tmp_path / "config.toml"


@pytest.fixture
def manager(tmp_config_path: Path) -> ConfigurationManager:
    """Return a ConfigurationManager with a temporary config path."""
    return ConfigurationManager(config_path=tmp_config_path)


class TestFactoryDefaults:
    """Tests for factory default configuration."""

    def test_factory_defaults_no_channels(self):
        defaults = _get_factory_defaults()
        assert defaults.channels == []

    def test_factory_defaults_no_alarms(self):
        defaults = _get_factory_defaults()
        assert defaults.alarms == []

    def test_factory_defaults_no_cloud(self):
        defaults = _get_factory_defaults()
        assert defaults.cloud is None

    def test_factory_defaults_system_settings(self):
        defaults = _get_factory_defaults()
        assert defaults.web_server_port == 8080
        assert defaults.max_remote_connections == 3
        assert defaults.dashboard_time_window_seconds == 60
        assert defaults.disk_space_warning_mb == 50


class TestLoad:
    """Tests for loading configuration."""

    def test_load_missing_file_returns_defaults(self, manager: ConfigurationManager):
        config = manager.load()
        assert config.channels == []
        assert config.alarms == []
        assert config.cloud is None
        assert manager.load_error is not None
        assert "not found" in manager.load_error

    def test_load_corrupted_file_returns_defaults(
        self, tmp_config_path: Path, manager: ConfigurationManager
    ):
        tmp_config_path.parent.mkdir(parents=True, exist_ok=True)
        tmp_config_path.write_text("this is not valid TOML {{{{")
        config = manager.load()
        assert config.channels == []
        assert manager.load_error is not None
        assert "corrupted" in manager.load_error.lower()

    def test_load_valid_file(self, tmp_config_path: Path):
        # Write a valid config
        config_data = {
            "system": {
                "csv_directory": "/tmp/data",
                "web_server_port": 9090,
                "max_remote_connections": 5,
                "dashboard_time_window_seconds": 120,
                "disk_space_warning_mb": 100,
            },
            "channels": [
                {
                    "channel_id": "egt1",
                    "channel_type": "thermocouple",
                    "hat_address": 0,
                    "hat_channel": 0,
                    "sample_rate_hz": 5.0,
                    "display_name": "EGT 1",
                    "enabled": True,
                    "calibration": {
                        "calibration_type": "linear",
                        "unit_label": "°C",
                        "min_valid_voltage": 0.0,
                        "max_valid_voltage": 5.0,
                        "linear_params": {"slope": 1.0, "offset": 0.0},
                    },
                }
            ],
        }
        tmp_config_path.parent.mkdir(parents=True, exist_ok=True)
        with open(tmp_config_path, "wb") as f:
            tomli_w.dump(config_data, f)

        mgr = ConfigurationManager(config_path=tmp_config_path)
        config = mgr.load()

        assert config.web_server_port == 9090
        assert len(config.channels) == 1
        assert config.channels[0].channel_id == "egt1"
        assert config.channels[0].channel_type == ChannelType.THERMOCOUPLE
        assert mgr.load_error is None

    def test_load_with_cloud_config(self, tmp_config_path: Path):
        config_data = {
            "system": {
                "csv_directory": "/tmp/data",
                "web_server_port": 8080,
                "max_remote_connections": 3,
                "dashboard_time_window_seconds": 60,
                "disk_space_warning_mb": 50,
            },
            "cloud": {
                "endpoint_url": "https://s3.example.com",
                "bucket_name": "dyno-data",
                "access_key": "AKID",
                "secret_key": "SECRET",
                "destination_prefix": "runs/",
                "upload_timeout_seconds": 300,
                "max_retries": 10,
                "retry_interval_seconds": 60,
                "max_queue_size": 100,
            },
        }
        tmp_config_path.parent.mkdir(parents=True, exist_ok=True)
        with open(tmp_config_path, "wb") as f:
            tomli_w.dump(config_data, f)

        mgr = ConfigurationManager(config_path=tmp_config_path)
        config = mgr.load()

        assert config.cloud is not None
        assert config.cloud.endpoint_url == "https://s3.example.com"
        assert config.cloud.bucket_name == "dyno-data"


class TestSave:
    """Tests for saving configuration."""

    def test_save_creates_file(self, tmp_config_path: Path, manager: ConfigurationManager):
        manager.load()
        manager.save()
        assert tmp_config_path.exists()

    def test_save_creates_parent_directories(self, tmp_path: Path):
        deep_path = tmp_path / "a" / "b" / "c" / "config.toml"
        mgr = ConfigurationManager(config_path=deep_path)
        mgr.load()
        mgr.save()
        assert deep_path.exists()

    def test_save_roundtrip_preserves_config(self, tmp_config_path: Path):
        mgr = ConfigurationManager(config_path=tmp_config_path)
        mgr.load()

        # Modify config via internal state
        mgr._config.web_server_port = 9999
        mgr.save()

        # Load again
        mgr2 = ConfigurationManager(config_path=tmp_config_path)
        config = mgr2.load()
        assert config.web_server_port == 9999


class TestGetSet:
    """Tests for get/set with dotted key paths."""

    def test_get_system_value(self, manager: ConfigurationManager):
        manager.load()
        assert manager.get("system.web_server_port") == 8080

    def test_get_nested_value(self, tmp_config_path: Path):
        config_data = {
            "system": {
                "csv_directory": "/tmp/data",
                "web_server_port": 8080,
                "max_remote_connections": 3,
                "dashboard_time_window_seconds": 60,
                "disk_space_warning_mb": 50,
            },
            "channels": [
                {
                    "channel_id": "egt1",
                    "channel_type": "thermocouple",
                    "hat_address": 0,
                    "hat_channel": 0,
                    "sample_rate_hz": 5.0,
                    "display_name": "EGT 1",
                    "enabled": True,
                    "calibration": {
                        "calibration_type": "linear",
                        "unit_label": "°C",
                        "min_valid_voltage": 0.0,
                        "max_valid_voltage": 5.0,
                        "linear_params": {"slope": 1.0, "offset": 0.0},
                    },
                }
            ],
        }
        tmp_config_path.parent.mkdir(parents=True, exist_ok=True)
        with open(tmp_config_path, "wb") as f:
            tomli_w.dump(config_data, f)

        mgr = ConfigurationManager(config_path=tmp_config_path)
        mgr.load()
        assert mgr.get("channels.0.channel_id") == "egt1"
        assert mgr.get("channels.0.calibration.unit_label") == "°C"

    def test_get_nonexistent_key_raises(self, manager: ConfigurationManager):
        manager.load()
        with pytest.raises(KeyError):
            manager.get("nonexistent.key")

    def test_set_system_value(self, manager: ConfigurationManager):
        manager.load()
        manager.set("system.web_server_port", 9090)
        assert manager.config.web_server_port == 9090
        assert manager.get("system.web_server_port") == 9090

    def test_set_nonexistent_key_raises(self, manager: ConfigurationManager):
        manager.load()
        with pytest.raises(KeyError):
            manager.set("nonexistent.key", "value")

    def test_set_schedules_save(self, tmp_config_path: Path):
        mgr = ConfigurationManager(config_path=tmp_config_path)
        mgr.load()
        mgr.set("system.web_server_port", 7777)

        # Timer should be scheduled
        assert mgr._save_timer is not None
        assert mgr._save_timer.is_alive()

        # Clean up
        mgr.shutdown()

    def test_set_list_index(self, tmp_config_path: Path):
        config_data = {
            "system": {
                "csv_directory": "/tmp/data",
                "web_server_port": 8080,
                "max_remote_connections": 3,
                "dashboard_time_window_seconds": 60,
                "disk_space_warning_mb": 50,
            },
            "channels": [
                {
                    "channel_id": "egt1",
                    "channel_type": "thermocouple",
                    "hat_address": 0,
                    "hat_channel": 0,
                    "sample_rate_hz": 5.0,
                    "display_name": "EGT 1",
                    "enabled": True,
                    "calibration": {
                        "calibration_type": "linear",
                        "unit_label": "°C",
                        "min_valid_voltage": 0.0,
                        "max_valid_voltage": 5.0,
                        "linear_params": {"slope": 1.0, "offset": 0.0},
                    },
                }
            ],
        }
        tmp_config_path.parent.mkdir(parents=True, exist_ok=True)
        with open(tmp_config_path, "wb") as f:
            tomli_w.dump(config_data, f)

        mgr = ConfigurationManager(config_path=tmp_config_path)
        mgr.load()
        mgr.set("channels.0.sample_rate_hz", 10.0)
        assert mgr.config.channels[0].sample_rate_hz == 10.0
        mgr.shutdown()


class TestDebouncedSave:
    """Tests for debounced save behavior."""

    def test_debounced_save_fires(self, tmp_config_path: Path):
        mgr = ConfigurationManager(config_path=tmp_config_path)
        mgr.load()

        # Use a very short debounce for testing
        with patch("rotax_dyno_daq.config.manager.SAVE_DEBOUNCE_SECONDS", 0.1):
            mgr.set("system.web_server_port", 1234)
            # Re-schedule with short timeout
            mgr._save_timer.cancel()
            mgr._save_timer = threading.Timer(0.1, mgr._debounced_save)
            mgr._save_timer.daemon = True
            mgr._save_timer.start()

            time.sleep(0.3)

        assert tmp_config_path.exists()
        mgr2 = ConfigurationManager(config_path=tmp_config_path)
        config = mgr2.load()
        assert config.web_server_port == 1234


class TestSerializationRoundTrip:
    """Tests for config serialization/deserialization round-trip."""

    def test_empty_config_roundtrip(self):
        config = _get_factory_defaults()
        data = _config_to_dict(config)
        restored = _dict_to_config(data)
        assert restored.web_server_port == config.web_server_port
        assert restored.channels == config.channels
        assert restored.alarms == config.alarms
        assert restored.cloud == config.cloud

    def test_full_config_roundtrip(self):
        config = SystemConfig(
            channels=[
                ChannelConfig(
                    channel_id="egt1",
                    channel_type=ChannelType.THERMOCOUPLE,
                    hat_address=0,
                    hat_channel=0,
                    sample_rate_hz=5.0,
                    calibration=CalibrationProfile(
                        calibration_type=CalibrationType.LINEAR,
                        unit_label="°C",
                        min_valid_voltage=0.0,
                        max_valid_voltage=5.0,
                        linear_params=LinearCalibrationParams(slope=200.0, offset=-50.0),
                    ),
                    display_name="EGT 1",
                    enabled=True,
                ),
                ChannelConfig(
                    channel_id="oilp",
                    channel_type=ChannelType.PRESSURE,
                    hat_address=1,
                    hat_channel=0,
                    sample_rate_hz=10.0,
                    calibration=CalibrationProfile(
                        calibration_type=CalibrationType.LOOKUP_TABLE,
                        unit_label="bar",
                        min_valid_voltage=0.5,
                        max_valid_voltage=4.5,
                        lookup_params=LookupTableParams(
                            points=[(0.5, 0.0), (2.5, 5.0), (4.5, 10.0)]
                        ),
                    ),
                    display_name="Oil Pressure",
                    enabled=True,
                ),
            ],
            alarms=[
                AlarmConfig(
                    channel_id="egt1",
                    thresholds=AlarmThreshold(
                        high_warning=800.0,
                        high_critical=900.0,
                        deadband=10.0,
                    ),
                    enabled=True,
                ),
            ],
            cloud=CloudConfig(
                endpoint_url="https://s3.example.com",
                bucket_name="dyno-data",
                access_key="AKID",
                secret_key="SECRET",
                destination_prefix="runs/",
            ),
            csv_directory=Path("/data/runs"),
            fallback_csv_directory=Path("/tmp/fallback"),
            web_server_port=9090,
            max_remote_connections=5,
            dashboard_time_window_seconds=120,
            disk_space_warning_mb=100,
        )

        data = _config_to_dict(config)
        restored = _dict_to_config(data)

        assert restored.web_server_port == 9090
        assert restored.max_remote_connections == 5
        assert restored.csv_directory == Path("/data/runs")
        assert restored.fallback_csv_directory == Path("/tmp/fallback")
        assert len(restored.channels) == 2
        assert restored.channels[0].channel_id == "egt1"
        assert restored.channels[0].calibration.linear_params.slope == 200.0
        assert restored.channels[1].calibration.lookup_params is not None
        assert len(restored.channels[1].calibration.lookup_params.points) == 3
        assert len(restored.alarms) == 1
        assert restored.alarms[0].thresholds.high_warning == 800.0
        assert restored.cloud is not None
        assert restored.cloud.endpoint_url == "https://s3.example.com"

    def test_toml_file_roundtrip(self, tmp_config_path: Path):
        """Test full save/load cycle through TOML file."""
        config = SystemConfig(
            channels=[
                ChannelConfig(
                    channel_id="rpm",
                    channel_type=ChannelType.RPM,
                    hat_address=1,
                    hat_channel=2,
                    sample_rate_hz=50.0,
                    calibration=CalibrationProfile(
                        calibration_type=CalibrationType.LINEAR,
                        unit_label="RPM",
                        min_valid_voltage=0.2,
                        max_valid_voltage=4.8,
                        linear_params=LinearCalibrationParams(slope=2000.0, offset=0.0),
                    ),
                    display_name="Engine RPM",
                ),
            ],
            web_server_port=7070,
        )

        mgr = ConfigurationManager(config_path=tmp_config_path)
        mgr._config = config
        mgr.save()

        mgr2 = ConfigurationManager(config_path=tmp_config_path)
        loaded = mgr2.load()

        assert loaded.web_server_port == 7070
        assert len(loaded.channels) == 1
        assert loaded.channels[0].channel_id == "rpm"
        assert loaded.channels[0].sample_rate_hz == 50.0
        assert loaded.channels[0].calibration.linear_params.slope == 2000.0


class TestShutdown:
    """Tests for shutdown behavior."""

    def test_shutdown_cancels_timer_and_saves(self, tmp_config_path: Path):
        mgr = ConfigurationManager(config_path=tmp_config_path)
        mgr.load()
        mgr.set("system.web_server_port", 5555)
        mgr.shutdown()

        # Timer should be cancelled
        assert mgr._save_timer is None

        # File should be saved
        assert tmp_config_path.exists()
        mgr2 = ConfigurationManager(config_path=tmp_config_path)
        config = mgr2.load()
        assert config.web_server_port == 5555


class TestExportConfig:
    """Tests for export_config method."""

    def test_export_creates_file(self, tmp_path: Path, manager: ConfigurationManager):
        manager.load()
        export_path = tmp_path / "exported.toml"
        manager.export_config(export_path)
        assert export_path.exists()

    def test_export_creates_parent_directories(
        self, tmp_path: Path, manager: ConfigurationManager
    ):
        manager.load()
        export_path = tmp_path / "deep" / "nested" / "exported.toml"
        manager.export_config(export_path)
        assert export_path.exists()

    def test_export_contains_current_config(
        self, tmp_path: Path, manager: ConfigurationManager
    ):
        manager.load()
        manager._config.web_server_port = 1234
        export_path = tmp_path / "exported.toml"
        manager.export_config(export_path)

        # Load the exported file and verify
        mgr2 = ConfigurationManager(config_path=export_path)
        config = mgr2.load()
        assert config.web_server_port == 1234

    def test_export_with_channels(self, tmp_path: Path, manager: ConfigurationManager):
        manager.load()
        manager._config.channels = [
            ChannelConfig(
                channel_id="egt1",
                channel_type=ChannelType.THERMOCOUPLE,
                hat_address=0,
                hat_channel=0,
                sample_rate_hz=5.0,
                calibration=CalibrationProfile(
                    calibration_type=CalibrationType.LINEAR,
                    unit_label="°C",
                    min_valid_voltage=0.0,
                    max_valid_voltage=5.0,
                    linear_params=LinearCalibrationParams(slope=200.0, offset=-50.0),
                ),
                display_name="EGT 1",
            ),
        ]
        export_path = tmp_path / "exported.toml"
        manager.export_config(export_path)

        mgr2 = ConfigurationManager(config_path=export_path)
        config = mgr2.load()
        assert len(config.channels) == 1
        assert config.channels[0].channel_id == "egt1"
        assert config.channels[0].calibration.linear_params.slope == 200.0


class TestImportConfig:
    """Tests for import_config method."""

    def test_import_valid_config(self, tmp_path: Path, manager: ConfigurationManager):
        manager.load()

        # Create a valid config file to import
        config_data = {
            "system": {
                "csv_directory": "/tmp/data",
                "web_server_port": 9090,
                "max_remote_connections": 5,
                "dashboard_time_window_seconds": 120,
                "disk_space_warning_mb": 100,
            },
        }
        import_path = tmp_path / "import.toml"
        with open(import_path, "wb") as f:
            tomli_w.dump(config_data, f)

        result = manager.import_config(import_path)
        assert result.valid is True
        assert result.errors == []
        assert manager.config.web_server_port == 9090

    def test_import_nonexistent_file(self, tmp_path: Path, manager: ConfigurationManager):
        manager.load()
        original_port = manager.config.web_server_port

        result = manager.import_config(tmp_path / "nonexistent.toml")
        assert result.valid is False
        assert any("not found" in e for e in result.errors)
        # Config should be unchanged
        assert manager.config.web_server_port == original_port

    def test_import_invalid_toml(self, tmp_path: Path, manager: ConfigurationManager):
        manager.load()
        original_port = manager.config.web_server_port

        import_path = tmp_path / "bad.toml"
        import_path.write_text("this is not valid TOML {{{{")

        result = manager.import_config(import_path)
        assert result.valid is False
        assert any("TOML" in e for e in result.errors)
        assert manager.config.web_server_port == original_port

    def test_import_invalid_port_rejected(
        self, tmp_path: Path, manager: ConfigurationManager
    ):
        manager.load()
        original_port = manager.config.web_server_port

        config_data = {
            "system": {
                "csv_directory": "/tmp/data",
                "web_server_port": 0,  # Invalid: must be 1-65535
                "max_remote_connections": 3,
                "dashboard_time_window_seconds": 60,
                "disk_space_warning_mb": 50,
            },
        }
        import_path = tmp_path / "import.toml"
        with open(import_path, "wb") as f:
            tomli_w.dump(config_data, f)

        result = manager.import_config(import_path)
        assert result.valid is False
        assert any("web_server_port" in e for e in result.errors)
        assert manager.config.web_server_port == original_port

    def test_import_invalid_port_too_high(
        self, tmp_path: Path, manager: ConfigurationManager
    ):
        manager.load()

        config_data = {
            "system": {
                "csv_directory": "/tmp/data",
                "web_server_port": 70000,  # Invalid: > 65535
                "max_remote_connections": 3,
                "dashboard_time_window_seconds": 60,
                "disk_space_warning_mb": 50,
            },
        }
        import_path = tmp_path / "import.toml"
        with open(import_path, "wb") as f:
            tomli_w.dump(config_data, f)

        result = manager.import_config(import_path)
        assert result.valid is False
        assert any("web_server_port" in e for e in result.errors)

    def test_import_invalid_max_connections(
        self, tmp_path: Path, manager: ConfigurationManager
    ):
        manager.load()

        config_data = {
            "system": {
                "csv_directory": "/tmp/data",
                "web_server_port": 8080,
                "max_remote_connections": 0,  # Invalid: must be >= 1
                "dashboard_time_window_seconds": 60,
                "disk_space_warning_mb": 50,
            },
        }
        import_path = tmp_path / "import.toml"
        with open(import_path, "wb") as f:
            tomli_w.dump(config_data, f)

        result = manager.import_config(import_path)
        assert result.valid is False
        assert any("max_remote_connections" in e for e in result.errors)

    def test_import_invalid_disk_space_warning(
        self, tmp_path: Path, manager: ConfigurationManager
    ):
        manager.load()

        config_data = {
            "system": {
                "csv_directory": "/tmp/data",
                "web_server_port": 8080,
                "max_remote_connections": 3,
                "dashboard_time_window_seconds": 60,
                "disk_space_warning_mb": 0,  # Invalid: must be > 0
            },
        }
        import_path = tmp_path / "import.toml"
        with open(import_path, "wb") as f:
            tomli_w.dump(config_data, f)

        result = manager.import_config(import_path)
        assert result.valid is False
        assert any("disk_space_warning_mb" in e for e in result.errors)

    def test_import_invalid_sample_rate(
        self, tmp_path: Path, manager: ConfigurationManager
    ):
        manager.load()

        config_data = {
            "system": {
                "csv_directory": "/tmp/data",
                "web_server_port": 8080,
                "max_remote_connections": 3,
                "dashboard_time_window_seconds": 60,
                "disk_space_warning_mb": 50,
            },
            "channels": [
                {
                    "channel_id": "egt1",
                    "channel_type": "thermocouple",
                    "hat_address": 0,
                    "hat_channel": 0,
                    "sample_rate_hz": 50.0,  # Invalid: thermocouple max is 10 Hz
                    "display_name": "EGT 1",
                    "enabled": True,
                    "calibration": {
                        "calibration_type": "linear",
                        "unit_label": "°C",
                        "min_valid_voltage": 0.0,
                        "max_valid_voltage": 5.0,
                        "linear_params": {"slope": 1.0, "offset": 0.0},
                    },
                }
            ],
        }
        import_path = tmp_path / "import.toml"
        with open(import_path, "wb") as f:
            tomli_w.dump(config_data, f)

        result = manager.import_config(import_path)
        assert result.valid is False
        assert any("sample_rate_hz" in e for e in result.errors)

    def test_import_negative_deadband_rejected(
        self, tmp_path: Path, manager: ConfigurationManager
    ):
        manager.load()

        config_data = {
            "system": {
                "csv_directory": "/tmp/data",
                "web_server_port": 8080,
                "max_remote_connections": 3,
                "dashboard_time_window_seconds": 60,
                "disk_space_warning_mb": 50,
            },
            "alarms": [
                {
                    "channel_id": "egt1",
                    "enabled": True,
                    "thresholds": {
                        "high_warning": 800.0,
                        "deadband": -5.0,  # Invalid: must be non-negative
                    },
                }
            ],
        }
        import_path = tmp_path / "import.toml"
        with open(import_path, "wb") as f:
            tomli_w.dump(config_data, f)

        result = manager.import_config(import_path)
        assert result.valid is False
        assert any("deadband" in e for e in result.errors)

    def test_import_invalid_lookup_table_too_few_points(
        self, tmp_path: Path, manager: ConfigurationManager
    ):
        manager.load()

        config_data = {
            "system": {
                "csv_directory": "/tmp/data",
                "web_server_port": 8080,
                "max_remote_connections": 3,
                "dashboard_time_window_seconds": 60,
                "disk_space_warning_mb": 50,
            },
            "channels": [
                {
                    "channel_id": "oilp",
                    "channel_type": "pressure",
                    "hat_address": 1,
                    "hat_channel": 0,
                    "sample_rate_hz": 10.0,
                    "display_name": "Oil Pressure",
                    "enabled": True,
                    "calibration": {
                        "calibration_type": "lookup_table",
                        "unit_label": "bar",
                        "min_valid_voltage": 0.5,
                        "max_valid_voltage": 4.5,
                        "lookup_points": [[1.0, 0.0]],  # Invalid: < 2 points
                    },
                }
            ],
        }
        import_path = tmp_path / "import.toml"
        with open(import_path, "wb") as f:
            tomli_w.dump(config_data, f)

        result = manager.import_config(import_path)
        assert result.valid is False
        assert any("at least 2 points" in e for e in result.errors)

    def test_import_invalid_lookup_table_duplicate_voltages(
        self, tmp_path: Path, manager: ConfigurationManager
    ):
        manager.load()

        config_data = {
            "system": {
                "csv_directory": "/tmp/data",
                "web_server_port": 8080,
                "max_remote_connections": 3,
                "dashboard_time_window_seconds": 60,
                "disk_space_warning_mb": 50,
            },
            "channels": [
                {
                    "channel_id": "oilp",
                    "channel_type": "pressure",
                    "hat_address": 1,
                    "hat_channel": 0,
                    "sample_rate_hz": 10.0,
                    "display_name": "Oil Pressure",
                    "enabled": True,
                    "calibration": {
                        "calibration_type": "lookup_table",
                        "unit_label": "bar",
                        "min_valid_voltage": 0.5,
                        "max_valid_voltage": 4.5,
                        "lookup_points": [
                            [1.0, 0.0],
                            [1.0, 5.0],  # Duplicate voltage
                            [4.0, 10.0],
                        ],
                    },
                }
            ],
        }
        import_path = tmp_path / "import.toml"
        with open(import_path, "wb") as f:
            tomli_w.dump(config_data, f)

        result = manager.import_config(import_path)
        assert result.valid is False
        assert any("duplicate voltage" in e for e in result.errors)

    def test_import_invalid_min_max_voltage(
        self, tmp_path: Path, manager: ConfigurationManager
    ):
        manager.load()

        config_data = {
            "system": {
                "csv_directory": "/tmp/data",
                "web_server_port": 8080,
                "max_remote_connections": 3,
                "dashboard_time_window_seconds": 60,
                "disk_space_warning_mb": 50,
            },
            "channels": [
                {
                    "channel_id": "egt1",
                    "channel_type": "thermocouple",
                    "hat_address": 0,
                    "hat_channel": 0,
                    "sample_rate_hz": 5.0,
                    "display_name": "EGT 1",
                    "enabled": True,
                    "calibration": {
                        "calibration_type": "linear",
                        "unit_label": "°C",
                        "min_valid_voltage": 5.0,  # Invalid: min >= max
                        "max_valid_voltage": 5.0,
                        "linear_params": {"slope": 1.0, "offset": 0.0},
                    },
                }
            ],
        }
        import_path = tmp_path / "import.toml"
        with open(import_path, "wb") as f:
            tomli_w.dump(config_data, f)

        result = manager.import_config(import_path)
        assert result.valid is False
        assert any("min_valid_voltage" in e for e in result.errors)

    def test_import_empty_cloud_fields_rejected(
        self, tmp_path: Path, manager: ConfigurationManager
    ):
        manager.load()

        config_data = {
            "system": {
                "csv_directory": "/tmp/data",
                "web_server_port": 8080,
                "max_remote_connections": 3,
                "dashboard_time_window_seconds": 60,
                "disk_space_warning_mb": 50,
            },
            "cloud": {
                "endpoint_url": "",  # Invalid: must be non-empty
                "bucket_name": "dyno-data",
                "access_key": "AKID",
                "secret_key": "SECRET",
            },
        }
        import_path = tmp_path / "import.toml"
        with open(import_path, "wb") as f:
            tomli_w.dump(config_data, f)

        result = manager.import_config(import_path)
        assert result.valid is False
        assert any("endpoint_url" in e for e in result.errors)

    def test_import_retains_current_config_on_failure(
        self, tmp_path: Path, manager: ConfigurationManager
    ):
        manager.load()
        manager._config.web_server_port = 5555

        # Try to import invalid config
        config_data = {
            "system": {
                "csv_directory": "/tmp/data",
                "web_server_port": 0,  # Invalid
                "max_remote_connections": 3,
                "dashboard_time_window_seconds": 60,
                "disk_space_warning_mb": 50,
            },
        }
        import_path = tmp_path / "import.toml"
        with open(import_path, "wb") as f:
            tomli_w.dump(config_data, f)

        result = manager.import_config(import_path)
        assert result.valid is False
        # Current config should be unchanged
        assert manager.config.web_server_port == 5555

    def test_import_export_roundtrip(
        self, tmp_path: Path, manager: ConfigurationManager
    ):
        """Test that export followed by import produces equivalent config."""
        manager.load()
        manager._config = SystemConfig(
            channels=[
                ChannelConfig(
                    channel_id="egt1",
                    channel_type=ChannelType.THERMOCOUPLE,
                    hat_address=0,
                    hat_channel=0,
                    sample_rate_hz=5.0,
                    calibration=CalibrationProfile(
                        calibration_type=CalibrationType.LINEAR,
                        unit_label="°C",
                        min_valid_voltage=0.0,
                        max_valid_voltage=5.0,
                        linear_params=LinearCalibrationParams(slope=200.0, offset=-50.0),
                    ),
                    display_name="EGT 1",
                ),
            ],
            alarms=[
                AlarmConfig(
                    channel_id="egt1",
                    thresholds=AlarmThreshold(
                        high_warning=800.0,
                        high_critical=900.0,
                        deadband=10.0,
                    ),
                    enabled=True,
                ),
            ],
            cloud=CloudConfig(
                endpoint_url="https://s3.example.com",
                bucket_name="dyno-data",
                access_key="AKID",
                secret_key="SECRET",
            ),
            web_server_port=9090,
            max_remote_connections=5,
            disk_space_warning_mb=100,
        )

        export_path = tmp_path / "roundtrip.toml"
        manager.export_config(export_path)

        # Import into a fresh manager
        mgr2 = ConfigurationManager(config_path=tmp_path / "other.toml")
        mgr2.load()
        result = mgr2.import_config(export_path)

        assert result.valid is True
        assert mgr2.config.web_server_port == 9090
        assert mgr2.config.max_remote_connections == 5
        assert len(mgr2.config.channels) == 1
        assert mgr2.config.channels[0].channel_id == "egt1"
        assert mgr2.config.channels[0].calibration.linear_params.slope == 200.0
        assert len(mgr2.config.alarms) == 1
        assert mgr2.config.alarms[0].thresholds.high_warning == 800.0
        assert mgr2.config.cloud is not None
        assert mgr2.config.cloud.endpoint_url == "https://s3.example.com"

    def test_import_valid_config_with_all_channel_types(
        self, tmp_path: Path, manager: ConfigurationManager
    ):
        """Test importing config with channels at valid rate boundaries."""
        manager.load()

        config_data = {
            "system": {
                "csv_directory": "/tmp/data",
                "web_server_port": 8080,
                "max_remote_connections": 3,
                "dashboard_time_window_seconds": 60,
                "disk_space_warning_mb": 50,
            },
            "channels": [
                {
                    "channel_id": "egt1",
                    "channel_type": "thermocouple",
                    "hat_address": 0,
                    "hat_channel": 0,
                    "sample_rate_hz": 10.0,  # Max valid for thermocouple
                    "display_name": "EGT 1",
                    "enabled": True,
                    "calibration": {
                        "calibration_type": "linear",
                        "unit_label": "°C",
                        "min_valid_voltage": 0.0,
                        "max_valid_voltage": 5.0,
                        "linear_params": {"slope": 1.0, "offset": 0.0},
                    },
                },
                {
                    "channel_id": "oilp",
                    "channel_type": "pressure",
                    "hat_address": 1,
                    "hat_channel": 0,
                    "sample_rate_hz": 100.0,  # Max valid for pressure
                    "display_name": "Oil Pressure",
                    "enabled": True,
                    "calibration": {
                        "calibration_type": "lookup_table",
                        "unit_label": "bar",
                        "min_valid_voltage": 0.5,
                        "max_valid_voltage": 4.5,
                        "lookup_points": [[0.5, 0.0], [2.5, 5.0], [4.5, 10.0]],
                    },
                },
                {
                    "channel_id": "rpm",
                    "channel_type": "rpm",
                    "hat_address": 1,
                    "hat_channel": 1,
                    "sample_rate_hz": 50.0,  # Valid for RPM
                    "display_name": "RPM",
                    "enabled": True,
                    "calibration": {
                        "calibration_type": "linear",
                        "unit_label": "RPM",
                        "min_valid_voltage": 0.2,
                        "max_valid_voltage": 4.8,
                        "linear_params": {"slope": 2000.0, "offset": 0.0},
                    },
                },
                {
                    "channel_id": "afr1",
                    "channel_type": "afr",
                    "hat_address": 1,
                    "hat_channel": 2,
                    "sample_rate_hz": 20.0,  # Valid for AFR
                    "display_name": "AFR 1",
                    "enabled": True,
                    "calibration": {
                        "calibration_type": "linear",
                        "unit_label": "lambda",
                        "min_valid_voltage": 0.0,
                        "max_valid_voltage": 5.0,
                        "linear_params": {"slope": 0.4, "offset": 0.5},
                    },
                },
            ],
        }
        import_path = tmp_path / "import.toml"
        with open(import_path, "wb") as f:
            tomli_w.dump(config_data, f)

        result = manager.import_config(import_path)
        assert result.valid is True
        assert len(manager.config.channels) == 4


class TestValidateConfig:
    """Tests for the _validate_config helper function."""

    def test_valid_default_config(self):
        config = _get_factory_defaults()
        result = _validate_config(config)
        assert result.valid is True

    def test_multiple_errors_reported(self):
        config = SystemConfig(
            web_server_port=0,
            max_remote_connections=0,
            disk_space_warning_mb=0,
        )
        result = _validate_config(config)
        assert result.valid is False
        assert len(result.errors) == 3
