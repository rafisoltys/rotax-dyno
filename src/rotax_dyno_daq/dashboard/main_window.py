"""Main Dashboard window with tabbed navigation and status indicators.

Implements Requirements 5.4 (touch target size) and 5.5 (recording indicator
and elapsed run time).

The DashboardWindow provides:
- Tabbed navigation between Engine Overlay, Strip Charts, Alarms, Runs,
  and Post-Processing views
- Minimum touch target size of 12mm × 12mm (~45×45 px at 96 DPI)
- Status bar with ALARM, Cloud, MCC, Log, and CPU Temp indicators
- Recording indicator (red "REC" label) when a run is active
- Elapsed run time display (HH:MM:SS) updated every second via QTimer
"""

from __future__ import annotations

from typing import Optional

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import (
    QLabel,
    QMainWindow,
    QStatusBar,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from rotax_dyno_daq.alarms.manager import AlarmManager
from rotax_dyno_daq.core.data_bus import DataBus

# Minimum touch target size in pixels (12mm × 12mm at 96 DPI ≈ 45×45 px)
MIN_TOUCH_TARGET_PX = 45


class DashboardWindow(QMainWindow):
    """Main application window with tabbed views for the Rotax Dyno DAQ system.

    Provides tabbed navigation between:
    - Engine Overlay: sensor readings at physical locations
    - Strip Charts: real-time time-series plots
    - Alarms: alarm threshold configuration
    - Runs: run management (start/stop, log, export)
    - Post-Processing: filtering and derived channels

    The status bar displays:
    - ALARM indicator (red when active, green when OK)
    - Cloud status (connected/disconnected/uploading)
    - MCC boards count
    - Log status (recording/idle)
    - CPU temperature
    - Recording indicator and elapsed time (backward compat)
    """

    def __init__(
        self,
        data_bus: DataBus,
        alarm_manager: AlarmManager,
        parent: Optional[QWidget] = None,
    ) -> None:
        """Initialize the Dashboard window.

        Args:
            data_bus: The pub/sub data bus for sensor data.
            alarm_manager: The alarm manager for threshold evaluation.
            parent: Optional parent widget.
        """
        super().__init__(parent)

        self._data_bus = data_bus
        self._alarm_manager = alarm_manager

        # Recording state
        self._is_recording = False
        self._elapsed_seconds = 0

        # Set up the window
        self.setWindowTitle("Rotax Dyno DAQ")
        self.setMinimumSize(800, 600)

        # Build UI components
        self._setup_tabs()
        self._setup_status_bar()
        self._setup_timer()

    def _setup_tabs(self) -> None:
        """Create the tabbed navigation widget with placeholder views."""
        self._tab_widget = QTabWidget()
        self._tab_widget.setTabPosition(QTabWidget.TabPosition.North)

        # Ensure tab bar has minimum touch target size
        tab_bar = self._tab_widget.tabBar()
        if tab_bar is not None:
            tab_bar.setMinimumHeight(MIN_TOUCH_TARGET_PX)
            font = tab_bar.font()
            font.setPointSize(12)
            tab_bar.setFont(font)
            tab_bar.setStyleSheet(
                f"QTabBar::tab {{ min-width: {MIN_TOUCH_TARGET_PX}px; "
                f"min-height: {MIN_TOUCH_TARGET_PX}px; padding: 8px 16px; }}"
            )

        # Create placeholder widgets for each tab (to be replaced in tasks 11.2-11.6)
        self._engine_overlay_tab = self._create_placeholder("Engine Overlay")
        self._strip_charts_tab = self._create_placeholder("Strip Charts")
        self._alarms_tab = self._create_placeholder("Alarms")
        self._runs_tab = self._create_placeholder("Runs")
        self._post_processing_tab = self._create_placeholder("Post-Processing")

        # Add tabs with specified names
        self._tab_widget.addTab(self._engine_overlay_tab, "Engine Overlay")
        self._tab_widget.addTab(self._strip_charts_tab, "Strip Charts")
        self._tab_widget.addTab(self._alarms_tab, "Alarms")
        self._tab_widget.addTab(self._runs_tab, "Runs")
        self._tab_widget.addTab(self._post_processing_tab, "Post-Processing")

        self.setCentralWidget(self._tab_widget)

    def _create_placeholder(self, name: str) -> QWidget:
        """Create a placeholder widget for a tab that will be implemented later.

        Args:
            name: The display name for the placeholder.

        Returns:
            A QWidget with a centered label indicating placeholder status.
        """
        widget = QWidget()
        layout = QVBoxLayout(widget)
        label = QLabel(f"{name}\n(Coming soon)")
        label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        font = label.font()
        font.setPointSize(14)
        label.setFont(font)
        layout.addWidget(label)
        return widget

    def _setup_status_bar(self) -> None:
        """Set up the status bar with ALARM, Cloud, MCC, Log, and CPU Temp indicators."""
        status_bar = QStatusBar()
        self.setStatusBar(status_bar)

        # ALARM indicator
        self._alarm_status = QLabel("ALARM: OK")
        self._alarm_status.setStyleSheet("QLabel { color: green; font-weight: bold; padding: 4px 8px; }")
        status_bar.addWidget(self._alarm_status)

        # Cloud status
        self._cloud_status = QLabel("Cloud: --")
        self._cloud_status.setStyleSheet("QLabel { padding: 4px 8px; }")
        status_bar.addWidget(self._cloud_status)

        # MCC boards
        self._mcc_status = QLabel("MCC: 0 boards")
        self._mcc_status.setStyleSheet("QLabel { padding: 4px 8px; }")
        status_bar.addWidget(self._mcc_status)

        # Log status
        self._log_status = QLabel("Log: Idle")
        self._log_status.setStyleSheet("QLabel { padding: 4px 8px; }")
        status_bar.addWidget(self._log_status)

        # CPU Usage
        self._cpu_status = QLabel("CPU: --%")
        self._cpu_status.setStyleSheet("QLabel { padding: 4px 8px; }")
        status_bar.addPermanentWidget(self._cpu_status)

        # --- Backward-compatible widgets (kept for existing tests) ---

        # Recording indicator (red "REC" label, hidden by default)
        self._rec_indicator = QLabel("\u25cf REC")
        self._rec_indicator.setStyleSheet(
            "QLabel { color: red; font-weight: bold; padding: 4px 8px; }"
        )
        self._rec_indicator.setMinimumSize(MIN_TOUCH_TARGET_PX, MIN_TOUCH_TARGET_PX)
        self._rec_indicator.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._rec_indicator.setVisible(False)
        status_bar.addWidget(self._rec_indicator)

        # Elapsed run time display (HH:MM:SS)
        self._elapsed_label = QLabel("00:00:00")
        self._elapsed_label.setStyleSheet("QLabel { padding: 4px 8px; }")
        self._elapsed_label.setMinimumSize(MIN_TOUCH_TARGET_PX, MIN_TOUCH_TARGET_PX)
        self._elapsed_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._elapsed_label.setVisible(False)
        status_bar.addWidget(self._elapsed_label)

        # Connection status (kept for backward compatibility with tests)
        self._connection_label = QLabel("Remote: Disconnected")
        self._connection_label.setStyleSheet("QLabel { padding: 4px 8px; }")
        self._connection_label.setMinimumSize(MIN_TOUCH_TARGET_PX, MIN_TOUCH_TARGET_PX)
        self._connection_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._connection_label.setVisible(False)
        status_bar.addWidget(self._connection_label)

    def _setup_timer(self) -> None:
        """Set up timers for elapsed run time and CPU usage."""
        # 1-second timer for elapsed run time
        self._timer = QTimer(self)
        self._timer.setInterval(1000)  # 1 second
        self._timer.timeout.connect(self._update_elapsed_time)

        # 2-second timer for CPU usage
        self._cpu_timer = QTimer(self)
        self._cpu_timer.setInterval(2000)
        self._cpu_timer.timeout.connect(self.update_cpu_usage)
        self._cpu_timer.start()

    def _update_elapsed_time(self) -> None:
        """Increment elapsed time and update the display label."""
        self._elapsed_seconds += 1
        self._elapsed_label.setText(self._format_elapsed_time(self._elapsed_seconds))

    # --- New status bar update methods ---

    def update_alarm_status(self, active: bool, source: str = "") -> None:
        """Update the ALARM indicator in the status bar.

        Args:
            active: Whether any alarm is currently active.
            source: The source channel(s) triggering the alarm.
        """
        if active:
            self._alarm_status.setText(f"\u26a0 ALARM: {source}")
            self._alarm_status.setStyleSheet("QLabel { color: red; font-weight: bold; padding: 4px 8px; }")
        else:
            self._alarm_status.setText("ALARM: OK")
            self._alarm_status.setStyleSheet("QLabel { color: green; font-weight: bold; padding: 4px 8px; }")

    def update_cloud_status(self, status: str) -> None:
        """Update the Cloud status indicator.

        Args:
            status: Status text (e.g. "Connected", "Uploading (3)", "Not configured").
        """
        self._cloud_status.setText(f"Cloud: {status}")

    def update_mcc_status(self, board_count: int) -> None:
        """Update the MCC boards count indicator.

        Args:
            board_count: Number of active MCC HAT boards.
        """
        self._mcc_status.setText(f"MCC: {board_count} board{'s' if board_count != 1 else ''}")

    def update_log_status(self, recording: bool, filename: str = "") -> None:
        """Update the Log status indicator.

        Args:
            recording: Whether data is currently being recorded.
            filename: Optional filename being recorded to.
        """
        if recording:
            self._log_status.setText(f"Log: REC {filename}")
            self._log_status.setStyleSheet("QLabel { color: red; font-weight: bold; padding: 4px 8px; }")
        else:
            self._log_status.setText("Log: Idle")
            self._log_status.setStyleSheet("QLabel { padding: 4px 8px; }")

    def update_cpu_usage(self) -> None:
        """Read and display CPU usage percentage."""
        try:
            # Read from /proc/stat (Linux) — calculate usage from idle time
            with open("/proc/loadavg", "r") as f:
                load_1min = f.read().split()[0]
                self._cpu_status.setText(f"CPU: {load_1min}")
        except (FileNotFoundError, ValueError, OSError):
            try:
                import psutil  # type: ignore[import-not-found]
                usage = psutil.cpu_percent(interval=None)
                self._cpu_status.setText(f"CPU: {usage:.0f}%")
            except (ImportError, Exception):
                self._cpu_status.setText("CPU: N/A")

    # --- Recording methods (backward compatible) ---

    def start_recording(self) -> None:
        """Start the recording indicator and elapsed time counter.

        Called when a run is started to show the recording state on the dashboard.
        """
        self._is_recording = True
        self._elapsed_seconds = 0
        self._elapsed_label.setText("00:00:00")
        self._rec_indicator.setVisible(True)
        self._elapsed_label.setVisible(True)
        self.update_log_status(recording=True)
        self._timer.start()

    def stop_recording(self) -> None:
        """Stop the recording indicator and elapsed time counter.

        Called when a run is stopped to hide the recording state.
        """
        self._is_recording = False
        self._timer.stop()
        self._rec_indicator.setVisible(False)
        self._elapsed_label.setVisible(False)
        self.update_log_status(recording=False)

    @property
    def is_recording(self) -> bool:
        """Whether a recording run is currently active."""
        return self._is_recording

    @property
    def elapsed_seconds(self) -> int:
        """The number of elapsed seconds since recording started."""
        return self._elapsed_seconds

    def set_connection_status(self, connected: bool, client_count: int = 0) -> None:
        """Update the remote monitoring connection status display.

        Kept for backward compatibility with existing tests.

        Args:
            connected: Whether the remote monitoring server is active.
            client_count: Number of currently connected remote clients.
        """
        if connected:
            self._connection_label.setText(f"Remote: {client_count} client(s)")
            self._connection_label.setStyleSheet(
                "QLabel { color: green; padding: 4px 8px; }"
            )
        else:
            self._connection_label.setText("Remote: Disconnected")
            self._connection_label.setStyleSheet(
                "QLabel { padding: 4px 8px; }"
            )

    @property
    def tab_widget(self) -> QTabWidget:
        """Access the tab widget for replacing placeholder tabs with real implementations."""
        return self._tab_widget

    @staticmethod
    def _format_elapsed_time(seconds: int) -> str:
        """Format elapsed seconds as HH:MM:SS.

        Args:
            seconds: Total elapsed seconds.

        Returns:
            Formatted time string in HH:MM:SS format.
        """
        hours = seconds // 3600
        minutes = (seconds % 3600) // 60
        secs = seconds % 60
        return f"{hours:02d}:{minutes:02d}:{secs:02d}"
