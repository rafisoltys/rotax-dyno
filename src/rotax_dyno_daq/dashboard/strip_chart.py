"""Real-time scrolling strip chart widgets using PyQtGraph.

Provides StripChartWidget for individual channel time-series display
and StripChartPanel as a container for multiple strip charts.
"""

from __future__ import annotations

from collections import deque
from typing import Optional

import pyqtgraph as pg
from PyQt6.QtCore import QTimer, Qt
from PyQt6.QtWidgets import (
    QGridLayout,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from rotax_dyno_daq.core.data_bus import DataBus, Sample, SubscriptionId


# Minimum and maximum configurable time window in seconds
MIN_TIME_WINDOW_SECONDS = 30
MAX_TIME_WINDOW_SECONDS = 600
DEFAULT_TIME_WINDOW_SECONDS = 60

# Refresh interval in milliseconds (10 Hz = 100 ms)
REFRESH_INTERVAL_MS = 100


class StripChartWidget(pg.PlotWidget):
    """Real-time scrolling time-series chart for a single channel.

    Displays calibrated sensor values over time with auto-scrolling X-axis.
    Subscribes to the DataBus for live data updates and refreshes at 10 Hz.

    Attributes:
        channel_id: The channel identifier this chart displays.
        unit: Engineering unit label for the Y-axis.
        time_window_seconds: Visible time window (30-600 seconds).
    """

    def __init__(
        self,
        channel_id: str,
        unit: str,
        data_bus: DataBus,
        time_window_seconds: int = DEFAULT_TIME_WINDOW_SECONDS,
        display_name: str = "",
        parent: Optional[QWidget] = None,
    ) -> None:
        """Initialize the strip chart widget.

        Args:
            channel_id: The channel topic to subscribe to on the DataBus.
            unit: Engineering unit label (e.g. "°C", "bar", "RPM").
            data_bus: The DataBus instance to subscribe to for live data.
            time_window_seconds: Visible time window in seconds (30-600).
            display_name: Human-readable channel name for the title.
            parent: Optional parent widget.
        """
        super().__init__(parent=parent)

        self.channel_id = channel_id
        self.unit = unit
        self._data_bus = data_bus
        self._display_name = display_name or channel_id

        # Clamp time window to valid range
        self.time_window_seconds = max(
            MIN_TIME_WINDOW_SECONDS,
            min(MAX_TIME_WINDOW_SECONDS, time_window_seconds),
        )

        # Ring buffer for time-series data (timestamps and values)
        # Pre-allocate enough capacity for the time window at high sample rates
        max_points = self.time_window_seconds * 100  # Support up to 100 Hz
        self._timestamps: deque[float] = deque(maxlen=max_points)
        self._values: deque[float] = deque(maxlen=max_points)

        # Alarm threshold lines
        self._threshold_lines: list[pg.InfiniteLine] = []

        # Configure plot appearance
        self._setup_plot()

        # Create the data curve
        self._curve = self.plot([], [], pen=pg.mkPen(color="c", width=2))

        # Subscribe to DataBus for this channel's data
        self._subscription_id: SubscriptionId = self._data_bus.subscribe(
            self.channel_id, self._on_sample
        )

        # Refresh timer at 10 Hz (100 ms interval)
        self._refresh_timer = QTimer(self)
        self._refresh_timer.setInterval(REFRESH_INTERVAL_MS)
        self._refresh_timer.timeout.connect(self._update_plot)
        self._refresh_timer.start()

    def _setup_plot(self) -> None:
        """Configure plot title, labels, and appearance."""
        title = f"{self._display_name} ({self.unit})"
        self.setTitle(title, color="w", size="10pt")
        self.setLabel("bottom", "Time", units="s")
        self.setLabel("left", self.unit)
        self.showGrid(x=True, y=True, alpha=0.3)
        self.setBackground("k")
        # Disable mouse interaction for touchscreen friendliness
        self.setMouseEnabled(x=False, y=False)
        self.enableAutoRange(axis="y")

    def _on_sample(self, sample: Sample) -> None:
        """Callback invoked by DataBus when a new sample arrives.

        Stores the timestamp and calibrated value in the ring buffer.
        This is called from the publisher's thread, so we only append
        to the deque (thread-safe for single-producer patterns).

        Args:
            sample: A CalibratedSample (or any object with timestamp_ms
                    and calibrated_value attributes).
        """
        try:
            timestamp_s = sample.timestamp_ms / 1000.0
            value = sample.calibrated_value
            self._timestamps.append(timestamp_s)
            self._values.append(value)
        except AttributeError:
            # Ignore samples that don't have the expected attributes
            pass

    def _update_plot(self) -> None:
        """Update the plot display (called at 10 Hz by QTimer).

        Scrolls the X-axis to show the most recent time_window_seconds
        of data and updates the curve with current buffer contents.
        """
        if not self._timestamps:
            return

        # Convert deque to lists for plotting
        times = list(self._timestamps)
        values = list(self._values)

        # Update curve data
        self._curve.setData(times, values)

        # Auto-scroll X-axis to show the most recent time window
        latest_time = times[-1]
        x_min = latest_time - self.time_window_seconds
        self.setXRange(x_min, latest_time, padding=0)

    def set_time_window(self, seconds: int) -> None:
        """Update the visible time window.

        Args:
            seconds: New time window in seconds (clamped to 30-600).
        """
        self.time_window_seconds = max(
            MIN_TIME_WINDOW_SECONDS,
            min(MAX_TIME_WINDOW_SECONDS, seconds),
        )
        # Resize the deque capacity for the new window
        max_points = self.time_window_seconds * 100
        # Preserve existing data by creating new deques with updated maxlen
        self._timestamps = deque(self._timestamps, maxlen=max_points)
        self._values = deque(self._values, maxlen=max_points)

    def set_alarm_thresholds(
        self,
        warning_high: Optional[float] = None,
        warning_low: Optional[float] = None,
        critical_high: Optional[float] = None,
        critical_low: Optional[float] = None,
    ) -> None:
        """Set horizontal alarm threshold lines on the chart.

        Removes any existing threshold lines and adds new ones.

        Args:
            warning_high: High warning threshold value.
            warning_low: Low warning threshold value.
            critical_high: High critical threshold value.
            critical_low: Low critical threshold value.
        """
        # Remove existing threshold lines
        for line in self._threshold_lines:
            self.removeItem(line)
        self._threshold_lines.clear()

        # Add new threshold lines
        warning_pen = pg.mkPen(color="y", width=1, style=Qt.PenStyle.DashLine)
        critical_pen = pg.mkPen(color="r", width=1, style=Qt.PenStyle.DashLine)

        if warning_high is not None:
            line = pg.InfiniteLine(
                pos=warning_high, angle=0, pen=warning_pen, label="W-Hi"
            )
            self.addItem(line)
            self._threshold_lines.append(line)

        if warning_low is not None:
            line = pg.InfiniteLine(
                pos=warning_low, angle=0, pen=warning_pen, label="W-Lo"
            )
            self.addItem(line)
            self._threshold_lines.append(line)

        if critical_high is not None:
            line = pg.InfiniteLine(
                pos=critical_high, angle=0, pen=critical_pen, label="C-Hi"
            )
            self.addItem(line)
            self._threshold_lines.append(line)

        if critical_low is not None:
            line = pg.InfiniteLine(
                pos=critical_low, angle=0, pen=critical_pen, label="C-Lo"
            )
            self.addItem(line)
            self._threshold_lines.append(line)

    def cleanup(self) -> None:
        """Unsubscribe from DataBus and stop the refresh timer.

        Call this before the widget is destroyed to prevent dangling
        subscriptions and timer callbacks.
        """
        self._refresh_timer.stop()
        self._data_bus.unsubscribe(self._subscription_id)

    def closeEvent(self, event) -> None:  # type: ignore[override]
        """Handle widget close by cleaning up resources."""
        self.cleanup()
        super().closeEvent(event)


class StripChartPanel(QWidget):
    """Container widget holding multiple StripChartWidget instances.

    Arranges strip charts in a scrollable grid layout, one per active
    channel. Supports dynamic addition and removal of charts.
    """

    # Number of columns in the grid layout
    GRID_COLUMNS = 2

    def __init__(
        self,
        data_bus: DataBus,
        time_window_seconds: int = DEFAULT_TIME_WINDOW_SECONDS,
        parent: Optional[QWidget] = None,
    ) -> None:
        """Initialize the strip chart panel.

        Args:
            data_bus: The DataBus instance for chart subscriptions.
            time_window_seconds: Default visible time window for all charts.
            parent: Optional parent widget.
        """
        super().__init__(parent=parent)

        self._data_bus = data_bus
        self._time_window_seconds = time_window_seconds
        self._charts: dict[str, StripChartWidget] = {}

        # Set up scrollable grid layout
        self._scroll_area = QScrollArea(self)
        self._scroll_area.setWidgetResizable(True)
        self._scroll_area.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )

        self._grid_container = QWidget()
        self._grid_layout = QGridLayout(self._grid_container)
        self._grid_layout.setSpacing(4)
        self._scroll_area.setWidget(self._grid_container)

        # Main layout
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.addWidget(self._scroll_area)

    def add_channel(
        self,
        channel_id: str,
        unit: str,
        display_name: str = "",
    ) -> StripChartWidget:
        """Add a strip chart for a channel.

        If a chart for the channel already exists, returns the existing one.

        Args:
            channel_id: The channel identifier / DataBus topic.
            unit: Engineering unit label.
            display_name: Human-readable channel name.

        Returns:
            The StripChartWidget instance for the channel.
        """
        if channel_id in self._charts:
            return self._charts[channel_id]

        chart = StripChartWidget(
            channel_id=channel_id,
            unit=unit,
            data_bus=self._data_bus,
            time_window_seconds=self._time_window_seconds,
            display_name=display_name,
            parent=self._grid_container,
        )

        self._charts[channel_id] = chart
        self._relayout_charts()
        return chart

    def remove_channel(self, channel_id: str) -> None:
        """Remove a strip chart for a channel.

        Args:
            channel_id: The channel identifier to remove.
        """
        if channel_id not in self._charts:
            return

        chart = self._charts.pop(channel_id)
        chart.cleanup()
        self._grid_layout.removeWidget(chart)
        chart.deleteLater()
        self._relayout_charts()

    def set_time_window(self, seconds: int) -> None:
        """Update the visible time window for all charts.

        Args:
            seconds: New time window in seconds (clamped to 30-600).
        """
        self._time_window_seconds = max(
            MIN_TIME_WINDOW_SECONDS,
            min(MAX_TIME_WINDOW_SECONDS, seconds),
        )
        for chart in self._charts.values():
            chart.set_time_window(self._time_window_seconds)

    def get_chart(self, channel_id: str) -> Optional[StripChartWidget]:
        """Get the strip chart widget for a specific channel.

        Args:
            channel_id: The channel identifier.

        Returns:
            The StripChartWidget or None if not found.
        """
        return self._charts.get(channel_id)

    def _relayout_charts(self) -> None:
        """Rearrange all charts in the grid layout."""
        # Remove all items from grid
        while self._grid_layout.count():
            item = self._grid_layout.takeAt(0)
            if item.widget():
                item.widget().setParent(None)  # type: ignore[call-overload]

        # Re-add charts in grid order
        for idx, (channel_id, chart) in enumerate(self._charts.items()):
            row = idx // self.GRID_COLUMNS
            col = idx % self.GRID_COLUMNS
            self._grid_layout.addWidget(chart, row, col)

    def cleanup(self) -> None:
        """Clean up all charts (unsubscribe and stop timers)."""
        for chart in self._charts.values():
            chart.cleanup()
        self._charts.clear()

    def closeEvent(self, event) -> None:  # type: ignore[override]
        """Handle widget close by cleaning up all charts."""
        self.cleanup()
        super().closeEvent(event)
