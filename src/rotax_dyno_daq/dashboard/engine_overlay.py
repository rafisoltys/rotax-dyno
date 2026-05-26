"""Engine Overlay Widget - renders sensor values at physical locations on engine diagram.

Displays a Rotax 912 ULS engine background image with sensor readings positioned
at their physical measurement locations. Readings are color-coded based on alarm
severity and show stale-data indicators when channels haven't been updated within
3 seconds.

Requirements: 5.1, 5.2, 5.3, 5.6, 10.5
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from PyQt6.QtCore import QRectF, Qt, QTimer
from PyQt6.QtGui import QColor, QFont, QPainter, QPen, QPixmap
from PyQt6.QtWidgets import QWidget

from rotax_dyno_daq.core.data_bus import DataBus, Sample, SubscriptionId
from rotax_dyno_daq.core.enums import AlarmSeverity


# --- Constants ---

#: Time in seconds after which a channel is considered stale.
STALE_THRESHOLD_SECONDS: float = 3.0

#: Refresh interval in milliseconds (10 Hz minimum).
REFRESH_INTERVAL_MS: int = 100

#: Color mapping for alarm severity levels.
SEVERITY_COLORS: dict[AlarmSeverity, QColor] = {
    AlarmSeverity.NORMAL: QColor(0, 180, 0),       # Green
    AlarmSeverity.WARNING: QColor(255, 191, 0),    # Amber
    AlarmSeverity.CRITICAL: QColor(220, 20, 20),   # Red
}

#: Color for stale data indicator.
STALE_COLOR: QColor = QColor(128, 128, 128)  # Gray

#: Default background color when no image is available.
DEFAULT_BACKGROUND_COLOR: QColor = QColor(30, 30, 40)


# --- Data Structures ---


@dataclass
class SensorReading:
    """Holds the latest reading for a sensor channel."""

    channel_id: str
    value: float = 0.0
    unit: str = ""
    severity: AlarmSeverity = AlarmSeverity.NORMAL
    last_update_time: float = 0.0  # time.monotonic() timestamp
    is_stale: bool = False


@dataclass
class SensorPosition:
    """Position and display configuration for a sensor on the overlay."""

    channel_id: str
    x: int
    y: int
    display_name: str = ""


# --- Default Sensor Positions for Rotax 912 ULS ---

DEFAULT_SENSOR_POSITIONS: dict[str, tuple[int, int]] = {
    "EGT1": (120, 80),
    "EGT2": (280, 80),
    "EGT3": (120, 180),
    "EGT4": (280, 180),
    "CLT": (200, 300),
    "OilTemp": (350, 300),
    "IAT": (60, 300),
    "OilP": (350, 380),
    "ChargeP": (60, 380),
    "RPM": (200, 420),
    "AFR1": (100, 480),
    "AFR2": (200, 480),
    "AFR3": (300, 480),
    "AFR4": (400, 480),
}


class EngineOverlayWidget(QWidget):
    """Renders sensor values at physical locations on a Rotax 912 ULS engine diagram.

    Features:
    - Background image rendering (engine diagram) or plain background fallback
    - Sensor value labels positioned at predefined coordinates
    - Color-coded backgrounds based on AlarmSeverity (green/amber/red)
    - Stale-data indicator (gray with strikethrough) for channels not updated in 3+ seconds
    - 10 Hz refresh rate via QTimer
    - DataBus subscription for live data updates

    Args:
        data_bus: The DataBus instance to subscribe to for live sensor data.
        alarm_manager: Optional AlarmManager for querying alarm severity.
        sensor_positions: Optional dict mapping channel_id to (x, y) coordinates.
            Defaults to standard Rotax 912 ULS positions.
        background_image_path: Optional path to the engine diagram image file.
        parent: Optional parent QWidget.
    """

    def __init__(
        self,
        data_bus: Optional[DataBus] = None,
        alarm_manager=None,
        sensor_positions: Optional[dict[str, tuple[int, int]]] = None,
        background_image_path: Optional[Path] = None,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)

        self._data_bus = data_bus
        self._alarm_manager = alarm_manager
        self._sensor_positions: dict[str, tuple[int, int]] = (
            sensor_positions if sensor_positions is not None else DEFAULT_SENSOR_POSITIONS.copy()
        )
        self._background_pixmap: Optional[QPixmap] = None
        self._readings: dict[str, SensorReading] = {}
        self._subscription_ids: list[SubscriptionId] = []

        # Initialize readings for all configured sensor positions
        for channel_id in self._sensor_positions:
            self._readings[channel_id] = SensorReading(channel_id=channel_id)

        # Load background image if provided
        if background_image_path is not None:
            self._load_background_image(background_image_path)

        # Subscribe to DataBus for live updates
        if self._data_bus is not None:
            self._subscribe_to_data_bus()

        # Set up refresh timer at 10 Hz (100ms interval)
        self._refresh_timer = QTimer(self)
        self._refresh_timer.setInterval(REFRESH_INTERVAL_MS)
        self._refresh_timer.timeout.connect(self._on_refresh_tick)
        self._refresh_timer.start()

        # Widget configuration
        self.setMinimumSize(500, 550)

    def _load_background_image(self, path: Path) -> None:
        """Load the engine diagram background image.

        Args:
            path: Path to the image file.
        """
        pixmap = QPixmap(str(path))
        if not pixmap.isNull():
            self._background_pixmap = pixmap

    def _subscribe_to_data_bus(self) -> None:
        """Subscribe to the DataBus wildcard topic for all channel updates."""
        if self._data_bus is not None:
            sub_id = self._data_bus.subscribe("*", self._on_sample_received)
            self._subscription_ids.append(sub_id)

    def _on_sample_received(self, sample: Sample) -> None:
        """Handle incoming sample from the DataBus.

        Updates the internal reading state for the corresponding channel.

        Args:
            sample: A CalibratedSample or similar object with channel_id,
                calibrated_value, and unit attributes.
        """
        channel_id = getattr(sample, "channel_id", None)
        calibrated_value = getattr(sample, "calibrated_value", None)
        unit = getattr(sample, "unit", "")

        if channel_id is None or calibrated_value is None:
            return

        if channel_id in self._readings:
            reading = self._readings[channel_id]
            reading.value = calibrated_value
            reading.unit = unit
            reading.last_update_time = time.monotonic()
            reading.is_stale = False

    def _on_refresh_tick(self) -> None:
        """Called at 10 Hz to update stale status and trigger repaint."""
        current_time = time.monotonic()

        for reading in self._readings.values():
            if reading.last_update_time > 0:
                elapsed = current_time - reading.last_update_time
                reading.is_stale = elapsed >= STALE_THRESHOLD_SECONDS
            else:
                # Never received data - consider stale
                reading.is_stale = True

        # Update alarm severities from AlarmManager if available
        self._update_alarm_severities()

        # Trigger repaint
        self.update()

    def _update_alarm_severities(self) -> None:
        """Query the AlarmManager for current alarm states and update readings."""
        if self._alarm_manager is None:
            return

        # Get all active alarms
        active_alarms = self._alarm_manager.get_active_alarms()
        active_alarm_channels: dict[str, AlarmSeverity] = {}
        for alarm in active_alarms:
            channel_id = alarm.channel_id
            # If multiple alarms for same channel, use highest severity
            if channel_id in active_alarm_channels:
                if alarm.severity == AlarmSeverity.CRITICAL:
                    active_alarm_channels[channel_id] = AlarmSeverity.CRITICAL
            else:
                active_alarm_channels[channel_id] = alarm.severity

        # Update readings
        for channel_id, reading in self._readings.items():
            if channel_id in active_alarm_channels:
                reading.severity = active_alarm_channels[channel_id]
            else:
                reading.severity = AlarmSeverity.NORMAL

    def paintEvent(self, event) -> None:
        """Render the engine overlay with background and sensor labels.

        Args:
            event: The QPaintEvent (unused but required by Qt).
        """
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        # Draw background
        self._draw_background(painter)

        # Draw sensor labels at their positions
        for channel_id, (x, y) in self._sensor_positions.items():
            reading = self._readings.get(channel_id)
            if reading is not None:
                self._draw_sensor_label(painter, x, y, channel_id, reading)

        painter.end()

    def _draw_background(self, painter: QPainter) -> None:
        """Draw the background image or a plain colored background.

        Args:
            painter: The QPainter to draw with.
        """
        if self._background_pixmap is not None and not self._background_pixmap.isNull():
            # Scale image to fit widget while maintaining aspect ratio
            scaled = self._background_pixmap.scaled(
                self.size(),
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
            # Center the image
            x_offset = (self.width() - scaled.width()) // 2
            y_offset = (self.height() - scaled.height()) // 2
            painter.drawPixmap(x_offset, y_offset, scaled)
        else:
            # Plain background fallback
            painter.fillRect(self.rect(), DEFAULT_BACKGROUND_COLOR)

    def _draw_sensor_label(
        self,
        painter: QPainter,
        x: int,
        y: int,
        channel_id: str,
        reading: SensorReading,
    ) -> None:
        """Draw a single sensor label at the specified position.

        Each label shows:
        - Channel name (top line)
        - Current value with unit (bottom line)
        - Color-coded background based on severity
        - Stale indicator (gray background + strikethrough) if data is stale

        Args:
            painter: The QPainter to draw with.
            x: X coordinate for the label center.
            y: Y coordinate for the label top.
            channel_id: The channel identifier string.
            reading: The current SensorReading data.
        """
        # Determine colors
        if reading.is_stale:
            bg_color = STALE_COLOR
            text_color = QColor(200, 200, 200)
        else:
            bg_color = SEVERITY_COLORS.get(reading.severity, SEVERITY_COLORS[AlarmSeverity.NORMAL])
            text_color = QColor(255, 255, 255)

        # Label dimensions
        label_width = 90
        label_height = 40
        label_rect = QRectF(
            x - label_width / 2,
            y,
            label_width,
            label_height,
        )

        # Draw background rectangle with rounded corners
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(bg_color)
        painter.drawRoundedRect(label_rect, 4, 4)

        # Draw border
        border_pen = QPen(QColor(60, 60, 60))
        border_pen.setWidth(1)
        painter.setPen(border_pen)
        painter.drawRoundedRect(label_rect, 4, 4)

        # Set up fonts
        name_font = QFont("Sans", 8)
        name_font.setBold(True)
        value_font = QFont("Sans", 9)

        # Draw channel name
        painter.setPen(text_color)
        painter.setFont(name_font)
        name_rect = QRectF(
            label_rect.x(),
            label_rect.y() + 2,
            label_rect.width(),
            label_height / 2,
        )
        painter.drawText(name_rect, Qt.AlignmentFlag.AlignCenter, channel_id)

        # Draw value with unit
        painter.setFont(value_font)
        if reading.last_update_time > 0:
            value_text = f"{reading.value:.1f} {reading.unit}"
        else:
            value_text = "---"

        value_rect = QRectF(
            label_rect.x(),
            label_rect.y() + label_height / 2,
            label_rect.width(),
            label_height / 2,
        )
        painter.drawText(value_rect, Qt.AlignmentFlag.AlignCenter, value_text)

        # Draw strikethrough for stale data
        if reading.is_stale and reading.last_update_time > 0:
            stale_pen = QPen(QColor(255, 80, 80))
            stale_pen.setWidth(2)
            painter.setPen(stale_pen)
            mid_y = int(label_rect.y() + label_height / 2)
            painter.drawLine(
                int(label_rect.x() + 5),
                mid_y,
                int(label_rect.x() + label_rect.width() - 5),
                mid_y,
            )

    def set_sensor_positions(self, positions: dict[str, tuple[int, int]]) -> None:
        """Update the sensor position mapping.

        Args:
            positions: Dict mapping channel_id to (x, y) coordinates.
        """
        self._sensor_positions = positions
        # Ensure readings exist for all positions
        for channel_id in positions:
            if channel_id not in self._readings:
                self._readings[channel_id] = SensorReading(channel_id=channel_id)
        self.update()

    def set_background_image(self, path: Path) -> None:
        """Set or change the background image.

        Args:
            path: Path to the new background image file.
        """
        self._load_background_image(path)
        self.update()

    def update_reading(
        self,
        channel_id: str,
        value: float,
        unit: str = "",
        severity: AlarmSeverity = AlarmSeverity.NORMAL,
    ) -> None:
        """Manually update a sensor reading (alternative to DataBus subscription).

        Args:
            channel_id: The channel identifier.
            value: The current calibrated value.
            unit: The engineering unit string.
            severity: The alarm severity for color coding.
        """
        if channel_id not in self._readings:
            self._readings[channel_id] = SensorReading(channel_id=channel_id)

        reading = self._readings[channel_id]
        reading.value = value
        reading.unit = unit
        reading.severity = severity
        reading.last_update_time = time.monotonic()
        reading.is_stale = False

    def get_reading(self, channel_id: str) -> Optional[SensorReading]:
        """Get the current reading for a channel.

        Args:
            channel_id: The channel identifier.

        Returns:
            The SensorReading or None if channel not found.
        """
        return self._readings.get(channel_id)

    def is_channel_stale(self, channel_id: str) -> bool:
        """Check if a channel's data is stale (not updated within 3 seconds).

        Args:
            channel_id: The channel identifier.

        Returns:
            True if the channel data is stale, False otherwise.
        """
        reading = self._readings.get(channel_id)
        if reading is None:
            return True
        if reading.last_update_time == 0:
            return True
        elapsed = time.monotonic() - reading.last_update_time
        return elapsed >= STALE_THRESHOLD_SECONDS

    def cleanup(self) -> None:
        """Stop the refresh timer and unsubscribe from the DataBus."""
        self._refresh_timer.stop()
        if self._data_bus is not None:
            for sub_id in self._subscription_ids:
                self._data_bus.unsubscribe(sub_id)
            self._subscription_ids.clear()

    def closeEvent(self, event) -> None:
        """Handle widget close by cleaning up resources.

        Args:
            event: The QCloseEvent.
        """
        self.cleanup()
        super().closeEvent(event)
