"""TOML-based configuration persistence for the Rotax Dyno DAQ system.

Handles loading, saving, and accessing system configuration with dotted key paths.
Uses tomllib (stdlib) for reading and tomli_w for writing TOML files.
"""

import logging
import threading
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

import tomli_w

from rotax_dyno_daq.calibration.rate_config import get_rate_range
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

logger = logging.getLogger(__name__)

# Default config file path
DEFAULT_CONFIG_PATH = Path.home() / ".rotax_dyno_daq" / "config.toml"

# Debounce interval for save after set() calls
SAVE_DEBOUNCE_SECONDS = 5.0


@dataclass
class ConfigValidationResult:
    """Result of configuration import validation."""

    valid: bool
    errors: list[str] = field(default_factory=list)


def _validate_config(config: SystemConfig) -> ConfigValidationResult:
    """Validate all value ranges in a SystemConfig.

    Checks:
    - Sample rates within bounds per channel type
    - Deadband values non-negative
    - Calibration profiles valid (≥2 lookup points, no duplicate voltages, min < max voltage)
    - Cloud config fields non-empty if cloud section is present
    - web_server_port in 1-65535
    - max_remote_connections ≥ 1
    - disk_space_warning_mb > 0

    Args:
        config: The SystemConfig to validate.

    Returns:
        A ConfigValidationResult with valid=True if all checks pass,
        or valid=False with a list of error messages.
    """
    errors: list[str] = []

    # Validate system-level settings
    if not (1 <= config.web_server_port <= 65535):
        errors.append(
            f"web_server_port must be between 1 and 65535, got {config.web_server_port}"
        )

    if config.max_remote_connections < 1:
        errors.append(
            f"max_remote_connections must be >= 1, got {config.max_remote_connections}"
        )

    if config.disk_space_warning_mb <= 0:
        errors.append(
            f"disk_space_warning_mb must be > 0, got {config.disk_space_warning_mb}"
        )

    # Validate channels
    for i, ch in enumerate(config.channels):
        # Validate sample rate within bounds for channel type
        min_hz, max_hz = get_rate_range(ch.channel_type)
        if not (min_hz <= ch.sample_rate_hz <= max_hz):
            errors.append(
                f"channels[{i}] ({ch.channel_id}): sample_rate_hz {ch.sample_rate_hz} "
                f"outside valid range [{min_hz}, {max_hz}] for {ch.channel_type.value}"
            )

        # Validate calibration profile
        cal = ch.calibration
        if cal.min_valid_voltage >= cal.max_valid_voltage:
            errors.append(
                f"channels[{i}] ({ch.channel_id}): min_valid_voltage "
                f"({cal.min_valid_voltage}) must be less than max_valid_voltage "
                f"({cal.max_valid_voltage})"
            )

        if cal.calibration_type == CalibrationType.LOOKUP_TABLE:
            if cal.lookup_params is None:
                errors.append(
                    f"channels[{i}] ({ch.channel_id}): lookup_params required "
                    f"for LOOKUP_TABLE calibration type"
                )
            else:
                points = cal.lookup_params.points
                if len(points) < 2:
                    errors.append(
                        f"channels[{i}] ({ch.channel_id}): lookup table must "
                        f"have at least 2 points, got {len(points)}"
                    )
                else:
                    voltages = [p[0] for p in points]
                    if len(set(voltages)) != len(voltages):
                        errors.append(
                            f"channels[{i}] ({ch.channel_id}): lookup table "
                            f"must not contain duplicate voltage entries"
                        )

        elif cal.calibration_type == CalibrationType.LINEAR:
            if cal.linear_params is None:
                errors.append(
                    f"channels[{i}] ({ch.channel_id}): linear_params required "
                    f"for LINEAR calibration type"
                )

    # Validate alarms
    for i, alarm in enumerate(config.alarms):
        if alarm.thresholds.deadband < 0:
            errors.append(
                f"alarms[{i}] ({alarm.channel_id}): deadband must be "
                f"non-negative, got {alarm.thresholds.deadband}"
            )

    # Validate cloud config
    if config.cloud is not None:
        cloud = config.cloud
        if not cloud.endpoint_url:
            errors.append("cloud.endpoint_url must be a non-empty string")
        if not cloud.bucket_name:
            errors.append("cloud.bucket_name must be a non-empty string")
        if not cloud.access_key:
            errors.append("cloud.access_key must be a non-empty string")
        if not cloud.secret_key:
            errors.append("cloud.secret_key must be a non-empty string")

    return ConfigValidationResult(valid=len(errors) == 0, errors=errors)


def _get_factory_defaults() -> SystemConfig:
    """Return factory default configuration.

    Factory defaults use mid-range sampling rates, no alarm thresholds active,
    no cloud settings, and default calibration profiles with unity scaling
    (slope=1, offset=0).
    """
    default_calibration = CalibrationProfile(
        calibration_type=CalibrationType.LINEAR,
        unit_label="V",
        min_valid_voltage=0.0,
        max_valid_voltage=5.0,
        linear_params=LinearCalibrationParams(slope=1.0, offset=0.0),
    )

    return SystemConfig(
        channels=[],
        alarms=[],
        cloud=None,
        csv_directory=Path("/home/pi/dyno_data"),
        fallback_csv_directory=None,
        web_server_port=8080,
        max_remote_connections=3,
        dashboard_time_window_seconds=60,
        disk_space_warning_mb=50,
    )


def _config_to_dict(config: SystemConfig) -> dict[str, Any]:
    """Serialize a SystemConfig to a TOML-compatible dictionary."""
    result: dict[str, Any] = {}

    # System-level settings
    result["system"] = {
        "csv_directory": str(config.csv_directory),
        "web_server_port": config.web_server_port,
        "max_remote_connections": config.max_remote_connections,
        "dashboard_time_window_seconds": config.dashboard_time_window_seconds,
        "disk_space_warning_mb": config.disk_space_warning_mb,
    }
    if config.fallback_csv_directory is not None:
        result["system"]["fallback_csv_directory"] = str(config.fallback_csv_directory)

    # Channels
    channels_list = []
    for ch in config.channels:
        ch_dict: dict[str, Any] = {
            "channel_id": ch.channel_id,
            "channel_type": ch.channel_type.value,
            "hat_address": ch.hat_address,
            "hat_channel": ch.hat_channel,
            "sample_rate_hz": ch.sample_rate_hz,
            "display_name": ch.display_name,
            "enabled": ch.enabled,
        }
        # Calibration
        cal = ch.calibration
        cal_dict: dict[str, Any] = {
            "calibration_type": cal.calibration_type.value,
            "unit_label": cal.unit_label,
            "min_valid_voltage": cal.min_valid_voltage,
            "max_valid_voltage": cal.max_valid_voltage,
        }
        if cal.linear_params is not None:
            cal_dict["linear_params"] = {
                "slope": cal.linear_params.slope,
                "offset": cal.linear_params.offset,
            }
        if cal.lookup_params is not None:
            # Store as array of [voltage, unit] pairs
            cal_dict["lookup_points"] = [
                list(point) for point in cal.lookup_params.points
            ]
        ch_dict["calibration"] = cal_dict
        channels_list.append(ch_dict)
    if channels_list:
        result["channels"] = channels_list

    # Alarms
    alarms_list = []
    for alarm in config.alarms:
        alarm_dict: dict[str, Any] = {
            "channel_id": alarm.channel_id,
            "enabled": alarm.enabled,
        }
        th = alarm.thresholds
        th_dict: dict[str, Any] = {"deadband": th.deadband}
        if th.low_warning is not None:
            th_dict["low_warning"] = th.low_warning
        if th.low_critical is not None:
            th_dict["low_critical"] = th.low_critical
        if th.high_warning is not None:
            th_dict["high_warning"] = th.high_warning
        if th.high_critical is not None:
            th_dict["high_critical"] = th.high_critical
        alarm_dict["thresholds"] = th_dict
        alarms_list.append(alarm_dict)
    if alarms_list:
        result["alarms"] = alarms_list

    # Cloud
    if config.cloud is not None:
        result["cloud"] = {
            "endpoint_url": config.cloud.endpoint_url,
            "bucket_name": config.cloud.bucket_name,
            "access_key": config.cloud.access_key,
            "secret_key": config.cloud.secret_key,
            "destination_prefix": config.cloud.destination_prefix,
            "upload_timeout_seconds": config.cloud.upload_timeout_seconds,
            "max_retries": config.cloud.max_retries,
            "retry_interval_seconds": config.cloud.retry_interval_seconds,
            "max_queue_size": config.cloud.max_queue_size,
        }

    return result


def _dict_to_config(data: dict[str, Any]) -> SystemConfig:
    """Deserialize a TOML dictionary to a SystemConfig."""
    # System-level settings
    sys_data = data.get("system", {})
    csv_directory = Path(sys_data.get("csv_directory", "/home/pi/dyno_data"))
    fallback_csv_directory = (
        Path(sys_data["fallback_csv_directory"])
        if "fallback_csv_directory" in sys_data
        else None
    )
    web_server_port = sys_data.get("web_server_port", 8080)
    max_remote_connections = sys_data.get("max_remote_connections", 3)
    dashboard_time_window_seconds = sys_data.get("dashboard_time_window_seconds", 60)
    disk_space_warning_mb = sys_data.get("disk_space_warning_mb", 50)

    # Channels
    channels: list[ChannelConfig] = []
    for ch_data in data.get("channels", []):
        cal_data = ch_data.get("calibration", {})
        linear_params = None
        if "linear_params" in cal_data:
            lp = cal_data["linear_params"]
            linear_params = LinearCalibrationParams(
                slope=lp["slope"], offset=lp["offset"]
            )
        lookup_params = None
        if "lookup_points" in cal_data:
            points = [tuple(p) for p in cal_data["lookup_points"]]
            lookup_params = LookupTableParams(points=points)

        calibration = CalibrationProfile(
            calibration_type=CalibrationType(cal_data.get("calibration_type", "linear")),
            unit_label=cal_data.get("unit_label", "V"),
            min_valid_voltage=cal_data.get("min_valid_voltage", 0.0),
            max_valid_voltage=cal_data.get("max_valid_voltage", 5.0),
            linear_params=linear_params,
            lookup_params=lookup_params,
        )

        channel = ChannelConfig(
            channel_id=ch_data["channel_id"],
            channel_type=ChannelType(ch_data["channel_type"]),
            hat_address=ch_data["hat_address"],
            hat_channel=ch_data["hat_channel"],
            sample_rate_hz=ch_data["sample_rate_hz"],
            calibration=calibration,
            display_name=ch_data.get("display_name", ""),
            enabled=ch_data.get("enabled", True),
        )
        channels.append(channel)

    # Alarms
    alarms: list[AlarmConfig] = []
    for alarm_data in data.get("alarms", []):
        th_data = alarm_data.get("thresholds", {})
        thresholds = AlarmThreshold(
            low_warning=th_data.get("low_warning"),
            low_critical=th_data.get("low_critical"),
            high_warning=th_data.get("high_warning"),
            high_critical=th_data.get("high_critical"),
            deadband=th_data.get("deadband", 0.0),
        )
        alarm = AlarmConfig(
            channel_id=alarm_data["channel_id"],
            thresholds=thresholds,
            enabled=alarm_data.get("enabled", True),
        )
        alarms.append(alarm)

    # Cloud
    cloud: Optional[CloudConfig] = None
    if "cloud" in data:
        cd = data["cloud"]
        cloud = CloudConfig(
            endpoint_url=cd["endpoint_url"],
            bucket_name=cd["bucket_name"],
            access_key=cd["access_key"],
            secret_key=cd["secret_key"],
            destination_prefix=cd.get("destination_prefix", ""),
            upload_timeout_seconds=cd.get("upload_timeout_seconds", 300),
            max_retries=cd.get("max_retries", 10),
            retry_interval_seconds=cd.get("retry_interval_seconds", 60),
            max_queue_size=cd.get("max_queue_size", 100),
        )

    return SystemConfig(
        channels=channels,
        alarms=alarms,
        cloud=cloud,
        csv_directory=csv_directory,
        fallback_csv_directory=fallback_csv_directory,
        web_server_port=web_server_port,
        max_remote_connections=max_remote_connections,
        dashboard_time_window_seconds=dashboard_time_window_seconds,
        disk_space_warning_mb=disk_space_warning_mb,
    )


class ConfigurationManager:
    """Manages system configuration persistence and access.

    Loads configuration from a TOML file at startup, provides dotted key path
    access for getting/setting values, and debounces saves to persist changes
    within 5 seconds of modification.
    """

    def __init__(self, config_path: Optional[Path] = None) -> None:
        """Initialize the ConfigurationManager.

        Args:
            config_path: Path to the TOML config file. Defaults to
                         ~/.rotax_dyno_daq/config.toml
        """
        self._config_path = config_path or DEFAULT_CONFIG_PATH
        self._config: SystemConfig = _get_factory_defaults()
        self._config_dict: dict[str, Any] = _config_to_dict(self._config)
        self._lock = threading.Lock()
        self._save_timer: Optional[threading.Timer] = None
        self._load_error: Optional[str] = None

    @property
    def config_path(self) -> Path:
        """The path to the configuration file."""
        return self._config_path

    @property
    def load_error(self) -> Optional[str]:
        """Error message if config could not be loaded, None otherwise."""
        return self._load_error

    @property
    def config(self) -> SystemConfig:
        """The current system configuration."""
        return self._config

    def load(self) -> SystemConfig:
        """Load configuration from TOML file, or return factory defaults.

        If the config file is missing or corrupted, starts with factory defaults
        and sets a flag indicating which config could not be loaded.

        Returns:
            The loaded (or default) SystemConfig.
        """
        self._load_error = None

        if not self._config_path.exists():
            logger.info(
                "Config file not found at %s, using factory defaults.",
                self._config_path,
            )
            self._load_error = f"Config file not found: {self._config_path}"
            self._config = _get_factory_defaults()
            self._config_dict = _config_to_dict(self._config)
            return self._config

        try:
            with open(self._config_path, "rb") as f:
                data = tomllib.load(f)
            self._config = _dict_to_config(data)
            self._config_dict = data
            logger.info("Configuration loaded from %s", self._config_path)
        except (tomllib.TOMLDecodeError, KeyError, TypeError, ValueError) as e:
            logger.warning(
                "Config file corrupted at %s: %s. Using factory defaults.",
                self._config_path,
                e,
            )
            self._load_error = f"Config file corrupted: {e}"
            self._config = _get_factory_defaults()
            self._config_dict = _config_to_dict(self._config)

        return self._config

    def save(self) -> None:
        """Persist current configuration to TOML file.

        Creates parent directories if they don't exist.
        If save fails, logs the error (caller can check and retry).
        """
        with self._lock:
            # Cancel any pending debounce timer
            if self._save_timer is not None:
                self._save_timer.cancel()
                self._save_timer = None

        try:
            self._config_path.parent.mkdir(parents=True, exist_ok=True)
            config_dict = _config_to_dict(self._config)
            with open(self._config_path, "wb") as f:
                tomli_w.dump(config_dict, f)
            self._config_dict = config_dict
            logger.info("Configuration saved to %s", self._config_path)
        except OSError as e:
            logger.error("Failed to save configuration to %s: %s", self._config_path, e)
            raise

    def get(self, key: str) -> Any:
        """Get a configuration value by dotted key path.

        Supports dotted paths like 'system.web_server_port' or
        'channels.0.channel_id' (numeric indices for list access).

        Args:
            key: Dotted key path to the configuration value.

        Returns:
            The value at the specified path.

        Raises:
            KeyError: If the key path does not exist.
        """
        config_dict = _config_to_dict(self._config)
        parts = key.split(".")
        current: Any = config_dict

        for part in parts:
            if isinstance(current, dict):
                if part not in current:
                    raise KeyError(f"Configuration key not found: {key}")
                current = current[part]
            elif isinstance(current, list):
                try:
                    index = int(part)
                    current = current[index]
                except (ValueError, IndexError):
                    raise KeyError(f"Configuration key not found: {key}")
            else:
                raise KeyError(f"Configuration key not found: {key}")

        return current

    def set(self, key: str, value: Any) -> None:
        """Set a configuration value and schedule persistence.

        Supports dotted paths like 'system.web_server_port'.
        Schedules a save within 5 seconds (debounced).

        Args:
            key: Dotted key path to the configuration value.
            value: The new value to set.

        Raises:
            KeyError: If the key path does not exist (cannot create new keys).
        """
        config_dict = _config_to_dict(self._config)
        parts = key.split(".")
        current: Any = config_dict

        # Navigate to the parent of the target key
        for part in parts[:-1]:
            if isinstance(current, dict):
                if part not in current:
                    raise KeyError(f"Configuration key not found: {key}")
                current = current[part]
            elif isinstance(current, list):
                try:
                    index = int(part)
                    current = current[index]
                except (ValueError, IndexError):
                    raise KeyError(f"Configuration key not found: {key}")
            else:
                raise KeyError(f"Configuration key not found: {key}")

        # Set the value
        last_part = parts[-1]
        if isinstance(current, dict):
            if last_part not in current:
                raise KeyError(f"Configuration key not found: {key}")
            current[last_part] = value
        elif isinstance(current, list):
            try:
                index = int(last_part)
                current[index] = value
            except (ValueError, IndexError):
                raise KeyError(f"Configuration key not found: {key}")
        else:
            raise KeyError(f"Configuration key not found: {key}")

        # Rebuild the config from the modified dict
        self._config = _dict_to_config(config_dict)
        self._config_dict = config_dict

        # Schedule debounced save
        self._schedule_save()

    def export_config(self, path: Path) -> None:
        """Export the current configuration to a specified TOML file.

        Writes the current SystemConfig to the given path using the same
        serialization format as the internal save() method.

        Args:
            path: The file path to write the exported configuration to.

        Raises:
            OSError: If the file cannot be written.
        """
        path.parent.mkdir(parents=True, exist_ok=True)
        config_dict = _config_to_dict(self._config)
        with open(path, "wb") as f:
            tomli_w.dump(config_dict, f)
        logger.info("Configuration exported to %s", path)

    def import_config(self, path: Path) -> ConfigValidationResult:
        """Validate and import configuration from a TOML file.

        Reads the specified file, parses it as TOML, converts to a
        SystemConfig, and validates all value ranges. If valid, applies
        the imported config as the current config. If invalid, retains
        the current config and returns validation errors.

        Args:
            path: The file path to import configuration from.

        Returns:
            A ConfigValidationResult indicating success or listing errors.
        """
        # Read and parse the file
        try:
            with open(path, "rb") as f:
                data = tomllib.load(f)
        except FileNotFoundError:
            return ConfigValidationResult(
                valid=False,
                errors=[f"Configuration file not found: {path}"],
            )
        except tomllib.TOMLDecodeError as e:
            return ConfigValidationResult(
                valid=False,
                errors=[f"Invalid TOML format: {e}"],
            )

        # Convert to SystemConfig
        try:
            imported_config = _dict_to_config(data)
        except (KeyError, TypeError, ValueError) as e:
            return ConfigValidationResult(
                valid=False,
                errors=[f"Invalid configuration structure: {e}"],
            )

        # Validate all value ranges
        validation = _validate_config(imported_config)
        if not validation.valid:
            return validation

        # Apply the imported config
        self._config = imported_config
        self._config_dict = _config_to_dict(imported_config)
        logger.info("Configuration imported from %s", path)
        return ConfigValidationResult(valid=True)

    def _schedule_save(self) -> None:
        """Schedule a save operation with debouncing.

        If a save is already scheduled, cancel it and reschedule.
        The save will occur SAVE_DEBOUNCE_SECONDS after the last set() call.
        """
        with self._lock:
            if self._save_timer is not None:
                self._save_timer.cancel()
            self._save_timer = threading.Timer(SAVE_DEBOUNCE_SECONDS, self._debounced_save)
            self._save_timer.daemon = True
            self._save_timer.start()

    def _debounced_save(self) -> None:
        """Execute the debounced save operation."""
        try:
            self.save()
        except OSError:
            # Error already logged in save()
            pass

    def shutdown(self) -> None:
        """Shutdown the configuration manager, flushing any pending saves."""
        with self._lock:
            if self._save_timer is not None:
                self._save_timer.cancel()
                self._save_timer = None
        # Perform a final save
        try:
            self.save()
        except OSError:
            pass
