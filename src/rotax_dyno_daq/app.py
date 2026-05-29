"""Main application entry point for the Rotax Dyno DAQ system.

Wires all components together:
- ConfigurationManager (TOML persistence)
- DataBus (pub/sub event bus)
- CalibrationEngine (raw → engineering units)
- AlarmManager (threshold monitoring)
- RunManager (run lifecycle)
- CsvLogger (data recording)
- CloudUploader (S3 upload)
- ThermocoupleReader / AnalogVoltageReader (HAT acquisition)
- DashboardWindow (PyQt6 GUI)
- FastAPI WebSocket server (remote monitoring)

Implements graceful shutdown: stops acquisition, flushes CSV, closes connections.
"""

from __future__ import annotations

import logging
import sys
import threading
from pathlib import Path
from typing import Optional

import uvicorn

from rotax_dyno_daq.acquisition.analog_voltage_reader import AnalogVoltageReader
from rotax_dyno_daq.acquisition.hat_reader import ThermocoupleReader
from rotax_dyno_daq.alarms.manager import AlarmManager
from rotax_dyno_daq.calibration.engine import CalibrationEngine
from rotax_dyno_daq.config.manager import ConfigurationManager
from rotax_dyno_daq.core.data_bus import DataBus
from rotax_dyno_daq.core.enums import ChannelType, SampleValidity
from rotax_dyno_daq.core.models import (
    ChannelConfig,
    CloudConfig,
    RawSample,
    CalibratedSample,
    SystemConfig,
)
from rotax_dyno_daq.storage.cloud_uploader import CloudUploader
from rotax_dyno_daq.storage.csv_logger import CsvLogger
from rotax_dyno_daq.storage.run_manager import RunManager
from rotax_dyno_daq.web.server import (
    app as fastapi_app,
    configure_data_bus,
    configure_server,
    set_alarm_manager,
    set_run_manager,
)

logger = logging.getLogger(__name__)


def _setup_logging() -> None:
    """Configure logging for the application."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )


def _group_channels_by_type(
    channels: list[ChannelConfig],
) -> tuple[list[ChannelConfig], list[ChannelConfig]]:
    """Group channels into thermocouple and analog voltage categories.

    Args:
        channels: All configured channels.

    Returns:
        A tuple of (thermocouple_channels, analog_voltage_channels).
    """
    thermocouple_channels: list[ChannelConfig] = []
    analog_channels: list[ChannelConfig] = []

    for ch in channels:
        if not ch.enabled:
            continue
        if ch.channel_type == ChannelType.THERMOCOUPLE:
            thermocouple_channels.append(ch)
        else:
            # Pressure, RPM, AFR all use MCC 118
            analog_channels.append(ch)

    return thermocouple_channels, analog_channels


def _start_uvicorn_background(port: int) -> threading.Thread:
    """Start the FastAPI/uvicorn server in a background daemon thread.

    Args:
        port: The port to serve on.

    Returns:
        The background thread running uvicorn.
    """
    config = uvicorn.Config(
        app=fastapi_app,
        host="0.0.0.0",
        port=port,
        log_level="warning",
    )
    server = uvicorn.Server(config)

    thread = threading.Thread(
        target=server.run,
        name="uvicorn-server",
        daemon=True,
    )
    thread.start()
    logger.info("FastAPI server started on port %d", port)
    return thread


def main() -> int:
    """Main application entry point.

    Initializes all subsystems, starts acquisition and GUI, and handles
    graceful shutdown on exit.

    Returns:
        Exit code (0 for success).
    """
    _setup_logging()
    logger.info("Starting Rotax Dyno DAQ system...")

    # --- 1. Configuration ---
    # Prefer a local config.toml in the working directory if it exists;
    # otherwise fall back to the default ~/.rotax_dyno_daq/config.toml.
    local_config_path = Path.cwd() / "config.toml"
    if local_config_path.exists():
        config_manager = ConfigurationManager(config_path=local_config_path)
        logger.info("Using local config file: %s", local_config_path)
    else:
        config_manager = ConfigurationManager()
    system_config: SystemConfig = config_manager.load()

    if config_manager.load_error:
        logger.warning(
            "Configuration issue: %s. Using factory defaults.",
            config_manager.load_error,
        )

    # --- 2. Data Bus ---
    data_bus = DataBus()

    # --- 3. Calibration Engine ---
    calibration_engine = CalibrationEngine()

    # Load calibration profiles from config
    for channel in system_config.channels:
        calibration_engine.update_profile(channel.channel_id, channel.calibration)

    # --- 4. Alarm Manager ---
    alarm_manager = AlarmManager(data_bus=data_bus)

    # Configure alarm thresholds from config
    for alarm_config in system_config.alarms:
        alarm_manager.configure_threshold(alarm_config.channel_id, alarm_config)

    # --- 5. Run Manager ---
    run_log_path = system_config.csv_directory / "run_log.json"

    # --- 6. CSV Logger ---
    csv_logger = CsvLogger(
        csv_directory=system_config.csv_directory,
        fallback_csv_directory=system_config.fallback_csv_directory,
        disk_space_warning_mb=system_config.disk_space_warning_mb,
    )

    # --- 7. Cloud Uploader ---
    cloud_uploader: Optional[CloudUploader] = None
    if system_config.cloud is not None:
        cloud_uploader = CloudUploader(config=system_config.cloud)
        cloud_uploader.start()
        logger.info("Cloud uploader started.")

    # --- 5 (continued). Run Manager with dependencies ---
    run_manager = RunManager(
        run_log_path=run_log_path,
        csv_logger=csv_logger,
        cloud_uploader=cloud_uploader,
    )

    # --- 8. HAT Readers ---
    thermocouple_channels, analog_channels = _group_channels_by_type(
        system_config.channels
    )

    hat_readers: list[ThermocoupleReader | AnalogVoltageReader] = []

    if thermocouple_channels:
        # Group by HAT address
        tc_by_address: dict[int, list[ChannelConfig]] = {}
        for ch in thermocouple_channels:
            tc_by_address.setdefault(ch.hat_address, []).append(ch)

        for address, channels in tc_by_address.items():
            reader = ThermocoupleReader(
                address=address,
                channels=channels,
                data_bus=data_bus,
            )
            hat_readers.append(reader)

    if analog_channels:
        # Group by HAT address
        av_by_address: dict[int, list[ChannelConfig]] = {}
        for ch in analog_channels:
            av_by_address.setdefault(ch.hat_address, []).append(ch)

        for address, channels in av_by_address.items():
            reader = AnalogVoltageReader(
                address=address,
                channels=channels,
                data_bus=data_bus,
            )
            hat_readers.append(reader)

    # --- 9. Configure FastAPI Web Server ---
    set_run_manager(run_manager)
    configure_data_bus(data_bus)
    set_alarm_manager(alarm_manager)
    configure_server(
        max_connections=system_config.max_remote_connections,
        port=system_config.web_server_port,
    )

    # --- 10. Start uvicorn in background thread ---
    _start_uvicorn_background(port=system_config.web_server_port)

    # --- 11. PyQt6 Application and Dashboard ---
    from PyQt6.QtWidgets import QApplication

    from rotax_dyno_daq.dashboard.main_window import DashboardWindow
    from rotax_dyno_daq.dashboard.engine_overlay import EngineOverlayWidget
    from rotax_dyno_daq.dashboard.strip_chart import StripChartPanel
    from rotax_dyno_daq.dashboard.alarm_widget import AlarmIndicatorWidget
    from rotax_dyno_daq.dashboard.alarm_config_panel import AlarmConfigPanel
    from rotax_dyno_daq.dashboard.run_panel import RunPanel
    from rotax_dyno_daq.dashboard.post_processing_panel import PostProcessingPanel
    from rotax_dyno_daq.dashboard.hardware_setup_panel import HardwareSetupPanel

    qt_app = QApplication(sys.argv)
    dashboard = DashboardWindow(data_bus=data_bus, alarm_manager=alarm_manager)

    # --- Replace placeholder tabs with real widgets ---
    tab_widget = dashboard.tab_widget

    # Tab 0: Engine Overlay
    engine_overlay = EngineOverlayWidget(
        data_bus=data_bus,
        alarm_manager=alarm_manager,
    )
    tab_widget.removeTab(0)
    tab_widget.insertTab(0, engine_overlay, "Engine Overlay")

    # Tab 1: Strip Charts
    strip_chart_panel = StripChartPanel(
        data_bus=data_bus,
        time_window_seconds=system_config.dashboard_time_window_seconds,
    )
    # Add a chart for each configured channel
    for ch in system_config.channels:
        if ch.enabled:
            strip_chart_panel.add_channel(
                ch.channel_id, ch.calibration.unit_label, ch.display_name or ch.channel_id
            )
    tab_widget.removeTab(1)
    tab_widget.insertTab(1, strip_chart_panel, "Strip Charts")

    # Tab 2: Alarms (combined: indicator + config)
    from PyQt6.QtWidgets import QVBoxLayout, QWidget, QSplitter
    from PyQt6.QtCore import Qt as QtConst

    alarms_container = QSplitter(QtConst.Orientation.Vertical)
    alarm_indicator = AlarmIndicatorWidget(alarm_manager=alarm_manager)
    channel_ids = [ch.channel_id for ch in system_config.channels]
    alarm_config = AlarmConfigPanel(
        alarm_manager=alarm_manager,
        channel_ids=channel_ids,
    )
    alarms_container.addWidget(alarm_indicator)
    alarms_container.addWidget(alarm_config)
    tab_widget.removeTab(2)
    tab_widget.insertTab(2, alarms_container, "Alarms")

    # Tab 3: Runs
    run_panel = RunPanel(
        run_manager=run_manager,
        csv_logger=csv_logger,
        config_manager=config_manager,
    )
    tab_widget.removeTab(3)
    tab_widget.insertTab(3, run_panel, "Runs")

    # Tab 4: Post-Processing
    post_processing_panel = PostProcessingPanel()
    tab_widget.removeTab(4)
    tab_widget.insertTab(4, post_processing_panel, "Post-Processing")

    # Tab 5: Hardware Setup
    hardware_setup_panel = HardwareSetupPanel(config_manager=config_manager)
    tab_widget.addTab(hardware_setup_panel, "Hardware Setup")

    # --- Wire the hardware config-applied callback ---
    def _on_hardware_config_applied() -> None:
        """Restart HAT readers and update UI after hardware config changes.

        Called by HardwareSetupPanel after Save & Apply succeeds.
        Steps:
        1. Stop existing HAT readers
        2. Reload config from ConfigurationManager
        3. Rebuild calibration profiles
        4. Create new HAT readers with updated channels
        5. Start new readers
        6. Update StripChartPanel with new channels
        7. Update HAT count in status bar
        """
        nonlocal hat_readers

        logger.info("Hardware config applied — restarting readers...")

        # 1. Stop existing HAT readers
        for reader in hat_readers:
            reader.stop()
        hat_readers.clear()
        logger.info("Stopped all existing HAT readers.")

        # 2. Reload config
        reloaded_config = config_manager.load()

        # 3. Rebuild calibration profiles
        for channel in reloaded_config.channels:
            calibration_engine.update_profile(channel.channel_id, channel.calibration)
        logger.info(
            "Rebuilt calibration profiles for %d channel(s).",
            len(reloaded_config.channels),
        )

        # 4. Create new HAT readers
        tc_channels, av_channels = _group_channels_by_type(reloaded_config.channels)

        if tc_channels:
            tc_by_address: dict[int, list[ChannelConfig]] = {}
            for ch in tc_channels:
                tc_by_address.setdefault(ch.hat_address, []).append(ch)
            for address, channels_list in tc_by_address.items():
                reader = ThermocoupleReader(
                    address=address,
                    channels=channels_list,
                    data_bus=data_bus,
                )
                hat_readers.append(reader)

        if av_channels:
            av_by_address: dict[int, list[ChannelConfig]] = {}
            for ch in av_channels:
                av_by_address.setdefault(ch.hat_address, []).append(ch)
            for address, channels_list in av_by_address.items():
                reader = AnalogVoltageReader(
                    address=address,
                    channels=channels_list,
                    data_bus=data_bus,
                )
                hat_readers.append(reader)

        # 5. Start new readers
        for reader in hat_readers:
            reader.start()
        logger.info("Started %d new HAT reader(s).", len(hat_readers))

        # 6. Update StripChartPanel — remove old channels, add new ones
        existing_channel_ids = list(strip_chart_panel._charts.keys())
        for ch_id in existing_channel_ids:
            strip_chart_panel.remove_channel(ch_id)

        for ch in reloaded_config.channels:
            if ch.enabled:
                strip_chart_panel.add_channel(
                    ch.channel_id,
                    ch.calibration.unit_label,
                    ch.display_name or ch.channel_id,
                )
        logger.info("StripChartPanel updated with new channels.")

        # 7. Update HAT count in status bar
        dashboard.update_mcc_status(len(hat_readers))

    hardware_setup_panel.on_config_applied = _on_hardware_config_applied

    # Select first tab
    tab_widget.setCurrentIndex(0)

    # Show notification if config had issues
    if config_manager.load_error:
        dashboard.statusBar().showMessage(
            f"Config: {config_manager.load_error} — using defaults", 10000
        )

    # --- EMA smoothing state ---
    _ema_state: dict[str, float] = {}
    _ema_alpha = [0.3]  # Mutable container so lambda can modify

    # Connect EMA alpha from hardware setup panel
    hardware_setup_panel.on_ema_changed = lambda val: _ema_alpha.__setitem__(0, val)

    # --- Wire new status bar indicators ---

    # HAT count
    dashboard.update_mcc_status(len(hat_readers))

    # Cloud status
    dashboard.update_cloud_status("Connected" if cloud_uploader is not None else "Not configured")

    dashboard.show()

    # --- Status bar periodic updates ---
    from PyQt6.QtCore import QTimer as QtTimer

    def _update_status_bar() -> None:
        """Periodically update the status bar indicators."""
        # HAT count
        dashboard.update_mcc_status(len(hat_readers))

        # Cloud status
        if cloud_uploader is not None:
            pending = cloud_uploader.pending_count
            if pending > 0:
                dashboard.update_cloud_status(f"Uploading ({pending})")
            else:
                dashboard.update_cloud_status("Connected")
        else:
            dashboard.update_cloud_status("Not configured")

        # Alarm status
        active_alarms = alarm_manager.get_active_alarms()
        if active_alarms:
            sources = ", ".join(set(a.channel_id for a in active_alarms[:3]))
            dashboard.update_alarm_status(active=True, source=sources)
        else:
            dashboard.update_alarm_status(active=False)

        # Log status
        dashboard.update_log_status(csv_logger.is_active)

    status_timer = QtTimer()
    status_timer.setInterval(2000)
    status_timer.timeout.connect(_update_status_bar)
    status_timer.start()

    # --- 12. Wire DataBus subscriptions (BEFORE starting readers) ---

    # Debug counter for calibration bridge
    _bridge_count = [0]

    # Calibration bridge: converts RawSample → CalibratedSample with EMA smoothing
    def _calibration_bridge(sample: object) -> None:
        """Convert RawSample to CalibratedSample via CalibrationEngine and republish."""
        if not isinstance(sample, RawSample):
            return  # Already calibrated or not a sample — skip

        _bridge_count[0] += 1
        if _bridge_count[0] <= 5 or _bridge_count[0] % 100 == 0:
            logger.info(
                "Calibration bridge: %s raw=%.3f (count=%d)",
                sample.channel_id, sample.raw_value, _bridge_count[0],
            )

        calibrated = calibration_engine.apply(
            sample.channel_id, sample.raw_value, sample.timestamp_ms
        )

        # Apply EMA smoothing for valid samples
        if calibrated.validity == SampleValidity.VALID:
            channel_id = calibrated.channel_id
            alpha = _ema_alpha[0]
            if channel_id in _ema_state:
                smoothed = alpha * calibrated.calibrated_value + (1 - alpha) * _ema_state[channel_id]
            else:
                smoothed = calibrated.calibrated_value
            _ema_state[channel_id] = smoothed

            calibrated = CalibratedSample(
                channel_id=calibrated.channel_id,
                timestamp_ms=calibrated.timestamp_ms,
                raw_value=calibrated.raw_value,
                calibrated_value=smoothed,
                unit=calibrated.unit,
                validity=calibrated.validity,
            )

        # Publish the calibrated sample on the same channel topic
        data_bus.publish(sample.channel_id, calibrated)

    data_bus.subscribe("*", _calibration_bridge)

    # CsvLogger subscription (writes samples when a run is active)
    def _on_sample_for_csv(sample: object) -> None:
        """Forward calibrated samples to the CSV logger when a run is active."""
        if csv_logger.is_active and hasattr(sample, "calibrated_value"):
            if isinstance(sample, CalibratedSample):
                csv_logger.write_sample(sample)

    data_bus.subscribe("*", _on_sample_for_csv)

    # --- 13. Start HAT readers ---
    for reader in hat_readers:
        reader.start()
    logger.info("Started %d HAT reader(s). Channels: %s", len(hat_readers),
                [ch.channel_id for r in hat_readers for ch in r.channels])

    # --- 14. Run Qt event loop ---
    logger.info("Rotax Dyno DAQ system ready.")
    exit_code = qt_app.exec()

    # --- 15. Graceful shutdown ---
    logger.info("Shutting down Rotax Dyno DAQ system...")

    # Stop HAT readers
    for reader in hat_readers:
        reader.stop()
    logger.info("HAT readers stopped.")

    # Flush CSV logger
    if csv_logger.is_active:
        csv_logger.flush()
        logger.info("CSV logger flushed.")

    # Stop cloud uploader
    if cloud_uploader is not None:
        cloud_uploader.stop()
        logger.info("Cloud uploader stopped.")

    # Shutdown configuration manager (flush pending saves)
    config_manager.shutdown()
    logger.info("Configuration manager shut down.")

    logger.info("Rotax Dyno DAQ system shut down cleanly.")
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
