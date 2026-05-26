"""Alarm Indicator Widget - visual and audible alarm display with acknowledge button.

Displays active alarms with severity-specific colors and patterns, provides
audible alerts with distinguishable tones for warning vs critical, and allows
operators to acknowledge alarms to silence the audible while maintaining visual.

Subscribes to AlarmManager events via polling at 10 Hz to ensure alarms appear
within 500ms of threshold crossing.
"""

from __future__ import annotations

import struct
import wave
import io
import tempfile
from typing import Optional

from PyQt6.QtCore import QTimer, Qt, QPropertyAnimation, QEasingCurve, pyqtProperty
from PyQt6.QtGui import QColor, QPainter, QFont, QPalette
from PyQt6.QtMultimedia import QSoundEffect
from PyQt6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QFrame,
    QSizePolicy,
)

from rotax_dyno_daq.alarms.manager import AlarmManager
from rotax_dyno_daq.core.enums import AlarmSeverity, AlarmState
from rotax_dyno_daq.core.models import ActiveAlarm


# --- Colors ---
COLOR_WARNING = QColor(255, 191, 0)  # Amber
COLOR_CRITICAL = QColor(220, 20, 60)  # Red/Crimson
COLOR_ACKNOWLEDGED = QColor(100, 100, 100)  # Dimmed gray
COLOR_NORMAL_BG = QColor(40, 40, 40)  # Dark background


class AlarmItemWidget(QFrame):
    """A single alarm entry in the alarm panel.

    Displays channel name, current value, threshold crossed, severity indicator,
    and an acknowledge button. Flashes/pulses for active (unacknowledged) alarms.
    """

    def __init__(self, alarm: ActiveAlarm, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._alarm = alarm
        self._flash_opacity: float = 1.0

        self.setFrameStyle(QFrame.Shape.Box | QFrame.Shadow.Raised)
        self.setLineWidth(2)
        self.setMinimumHeight(60)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

        self._setup_ui()
        self._setup_animation()
        self._update_style()

    def _setup_ui(self) -> None:
        """Set up the widget layout with alarm info and acknowledge button."""
        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 4, 8, 4)

        # Severity indicator dot
        self._severity_label = QLabel()
        self._severity_label.setFixedSize(16, 16)
        self._severity_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self._severity_label)

        # Info section
        info_layout = QVBoxLayout()
        info_layout.setSpacing(2)

        self._channel_label = QLabel(self._alarm.channel_id)
        self._channel_label.setFont(QFont("", 11, QFont.Weight.Bold))
        info_layout.addWidget(self._channel_label)

        value_text = (
            f"Value: {self._alarm.value:.2f} | "
            f"Threshold: {self._alarm.threshold_crossed:.2f}"
        )
        self._value_label = QLabel(value_text)
        self._value_label.setFont(QFont("", 9))
        info_layout.addWidget(self._value_label)

        severity_text = f"Severity: {self._alarm.severity.value.upper()}"
        self._severity_text_label = QLabel(severity_text)
        self._severity_text_label.setFont(QFont("", 9))
        info_layout.addWidget(self._severity_text_label)

        layout.addLayout(info_layout, stretch=1)

        # Acknowledge button (minimum 12mm touch target ≈ 45px at 96 DPI)
        self._ack_button = QPushButton("ACK")
        self._ack_button.setMinimumSize(48, 48)
        self._ack_button.setToolTip("Acknowledge alarm (silence audible)")
        self._ack_button.setEnabled(self._alarm.state == AlarmState.ACTIVE)
        layout.addWidget(self._ack_button)

    def _setup_animation(self) -> None:
        """Set up flashing animation for active alarms."""
        self._flash_timer = QTimer(self)
        self._flash_timer.setInterval(500)  # Flash every 500ms
        self._flash_timer.timeout.connect(self._toggle_flash)
        self._flash_visible = True

        if self._alarm.state == AlarmState.ACTIVE:
            self._flash_timer.start()

    def _toggle_flash(self) -> None:
        """Toggle the flash state for visual pulsing effect."""
        self._flash_visible = not self._flash_visible
        self._update_style()

    def _update_style(self) -> None:
        """Update the widget style based on alarm severity and flash state."""
        if self._alarm.state == AlarmState.ACKNOWLEDGED:
            border_color = COLOR_ACKNOWLEDGED.name()
            bg_color = COLOR_NORMAL_BG.name()
        elif self._alarm.severity == AlarmSeverity.CRITICAL:
            if self._flash_visible:
                border_color = COLOR_CRITICAL.name()
                bg_color = COLOR_CRITICAL.darker(300).name()
            else:
                border_color = COLOR_CRITICAL.darker(200).name()
                bg_color = COLOR_NORMAL_BG.name()
        else:  # WARNING
            if self._flash_visible:
                border_color = COLOR_WARNING.name()
                bg_color = COLOR_WARNING.darker(300).name()
            else:
                border_color = COLOR_WARNING.darker(200).name()
                bg_color = COLOR_NORMAL_BG.name()

        self.setStyleSheet(
            f"AlarmItemWidget {{ "
            f"border: 2px solid {border_color}; "
            f"background-color: {bg_color}; "
            f"border-radius: 4px; "
            f"}}"
        )

        # Update severity dot
        if self._alarm.severity == AlarmSeverity.CRITICAL:
            dot_color = COLOR_CRITICAL.name()
        else:
            dot_color = COLOR_WARNING.name()
        self._severity_label.setStyleSheet(
            f"background-color: {dot_color}; border-radius: 8px;"
        )

    def update_alarm(self, alarm: ActiveAlarm) -> None:
        """Update the displayed alarm data."""
        self._alarm = alarm
        value_text = (
            f"Value: {alarm.value:.2f} | "
            f"Threshold: {alarm.threshold_crossed:.2f}"
        )
        self._value_label.setText(value_text)
        self._severity_text_label.setText(
            f"Severity: {alarm.severity.value.upper()}"
        )
        self._ack_button.setEnabled(alarm.state == AlarmState.ACTIVE)

        if alarm.state == AlarmState.ACTIVE and not self._flash_timer.isActive():
            self._flash_timer.start()
        elif alarm.state == AlarmState.ACKNOWLEDGED and self._flash_timer.isActive():
            self._flash_timer.stop()
            self._flash_visible = True

        self._update_style()

    @property
    def alarm(self) -> ActiveAlarm:
        """Return the current alarm data."""
        return self._alarm

    @property
    def ack_button(self) -> QPushButton:
        """Return the acknowledge button for external signal connection."""
        return self._ack_button

    def stop_animation(self) -> None:
        """Stop the flash animation (cleanup)."""
        self._flash_timer.stop()


def _generate_tone_wav(frequency_hz: float, duration_ms: int, sample_rate: int = 44100) -> bytes:
    """Generate a simple sine wave tone as WAV bytes.

    Args:
        frequency_hz: Tone frequency in Hz.
        duration_ms: Duration in milliseconds.
        sample_rate: Audio sample rate (default 44100).

    Returns:
        WAV file content as bytes.
    """
    import math

    num_samples = int(sample_rate * duration_ms / 1000)
    amplitude = 16000  # ~50% of int16 max

    samples = []
    for i in range(num_samples):
        t = i / sample_rate
        value = int(amplitude * math.sin(2 * math.pi * frequency_hz * t))
        samples.append(struct.pack("<h", value))

    raw_data = b"".join(samples)

    # Build WAV in memory
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(raw_data)

    return buf.getvalue()


class AlarmIndicatorWidget(QWidget):
    """Visual and audible alarm display panel with acknowledge buttons.

    Displays a scrollable list of active alarms with:
    - Channel name, current value, and threshold crossed
    - Severity indicator (amber for WARNING, red for CRITICAL)
    - Flashing/pulsing animation for active alarms
    - Acknowledge button per alarm to silence audible

    Audible alerts:
    - Warning: 800 Hz intermittent beep pattern
    - Critical: 1200 Hz rapid/continuous tone
    - Acknowledged alarms: audible silenced, visual remains

    Polls AlarmManager.get_active_alarms() at 10 Hz (100ms interval) to ensure
    alarms appear within 500ms of threshold crossing.
    """

    def __init__(
        self,
        alarm_manager: AlarmManager,
        parent: Optional[QWidget] = None,
    ) -> None:
        """Initialize the alarm indicator widget.

        Args:
            alarm_manager: The AlarmManager instance to poll for active alarms.
            parent: Optional parent widget.
        """
        super().__init__(parent)
        self._alarm_manager = alarm_manager
        self._alarm_widgets: dict[str, AlarmItemWidget] = {}  # alarm_id -> widget
        self._audible_enabled = True
        self._acknowledged_ids: set[str] = set()

        # Audio state
        self._warning_sound: Optional[QSoundEffect] = None
        self._critical_sound: Optional[QSoundEffect] = None
        self._warning_tone_file: Optional[str] = None
        self._critical_tone_file: Optional[str] = None

        self._setup_ui()
        self._setup_audio()
        self._setup_polling()

    def _setup_ui(self) -> None:
        """Set up the widget layout."""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(4)

        # Header
        header_layout = QHBoxLayout()
        title = QLabel("ALARMS")
        title.setFont(QFont("", 12, QFont.Weight.Bold))
        title.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        header_layout.addWidget(title)

        # Status label showing count
        self._status_label = QLabel("No active alarms")
        self._status_label.setAlignment(
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
        )
        header_layout.addWidget(self._status_label)

        layout.addLayout(header_layout)

        # Scrollable alarm list
        self._scroll_area = QScrollArea()
        self._scroll_area.setWidgetResizable(True)
        self._scroll_area.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )

        self._alarm_container = QWidget()
        self._alarm_layout = QVBoxLayout(self._alarm_container)
        self._alarm_layout.setContentsMargins(0, 0, 0, 0)
        self._alarm_layout.setSpacing(4)
        self._alarm_layout.addStretch()

        self._scroll_area.setWidget(self._alarm_container)
        layout.addWidget(self._scroll_area)

    def _setup_audio(self) -> None:
        """Set up audio tone generation for warning and critical alerts.

        Generates WAV files for two distinct tones:
        - Warning: 800 Hz intermittent beep (200ms on)
        - Critical: 1200 Hz rapid tone (400ms on)
        """
        try:
            # Generate warning tone: 800 Hz, 200ms
            warning_wav = _generate_tone_wav(800, 200)
            self._warning_tone_file = tempfile.NamedTemporaryFile(
                suffix=".wav", delete=False
            )
            self._warning_tone_file.write(warning_wav)
            self._warning_tone_file.flush()
            warning_path = self._warning_tone_file.name

            # Generate critical tone: 1200 Hz, 400ms
            critical_wav = _generate_tone_wav(1200, 400)
            self._critical_tone_file = tempfile.NamedTemporaryFile(
                suffix=".wav", delete=False
            )
            self._critical_tone_file.write(critical_wav)
            self._critical_tone_file.flush()
            critical_path = self._critical_tone_file.name

            # Create QSoundEffect instances
            from PyQt6.QtCore import QUrl

            self._warning_sound = QSoundEffect(self)
            self._warning_sound.setSource(QUrl.fromLocalFile(warning_path))
            self._warning_sound.setLoopCount(QSoundEffect.Loop.Infinite)
            self._warning_sound.setVolume(0.7)

            self._critical_sound = QSoundEffect(self)
            self._critical_sound.setSource(QUrl.fromLocalFile(critical_path))
            self._critical_sound.setLoopCount(QSoundEffect.Loop.Infinite)
            self._critical_sound.setVolume(1.0)
        except Exception:
            # Audio may not be available (CI, headless). Degrade gracefully.
            self._warning_sound = None
            self._critical_sound = None

    def _setup_polling(self) -> None:
        """Set up 10 Hz polling of AlarmManager for active alarms."""
        self._poll_timer = QTimer(self)
        self._poll_timer.setInterval(100)  # 100ms = 10 Hz
        self._poll_timer.timeout.connect(self._poll_alarms)
        self._poll_timer.start()

    def _poll_alarms(self) -> None:
        """Poll the AlarmManager and update the display."""
        active_alarms = self._alarm_manager.get_active_alarms()
        active_ids = {alarm.alarm_id for alarm in active_alarms}

        # Remove widgets for alarms that are no longer active
        removed_ids = set(self._alarm_widgets.keys()) - active_ids
        for alarm_id in removed_ids:
            widget = self._alarm_widgets.pop(alarm_id)
            widget.stop_animation()
            self._alarm_layout.removeWidget(widget)
            widget.deleteLater()
            self._acknowledged_ids.discard(alarm_id)

        # Add or update widgets for active alarms
        for alarm in active_alarms:
            if alarm.alarm_id in self._alarm_widgets:
                # Update existing widget
                self._alarm_widgets[alarm.alarm_id].update_alarm(alarm)
            else:
                # Create new widget
                item = AlarmItemWidget(alarm)
                item.ack_button.clicked.connect(
                    lambda checked, aid=alarm.alarm_id: self._on_acknowledge(aid)
                )
                # Insert before the stretch
                insert_pos = self._alarm_layout.count() - 1
                self._alarm_layout.insertWidget(insert_pos, item)
                self._alarm_widgets[alarm.alarm_id] = item

            # Track acknowledged state
            if alarm.state == AlarmState.ACKNOWLEDGED:
                self._acknowledged_ids.add(alarm.alarm_id)

        # Update status label
        count = len(active_alarms)
        if count == 0:
            self._status_label.setText("No active alarms")
        else:
            self._status_label.setText(f"{count} active alarm{'s' if count != 1 else ''}")

        # Update audible alerts
        self._update_audible(active_alarms)

    def _update_audible(self, alarms: list[ActiveAlarm]) -> None:
        """Update audible alert state based on active unacknowledged alarms.

        - If any unacknowledged CRITICAL alarm exists: play critical tone
        - Else if any unacknowledged WARNING alarm exists: play warning tone
        - Otherwise: silence all
        """
        if not self._audible_enabled:
            self._stop_all_sounds()
            return

        # Find highest unacknowledged severity
        has_unacked_critical = any(
            a.severity == AlarmSeverity.CRITICAL and a.state == AlarmState.ACTIVE
            for a in alarms
        )
        has_unacked_warning = any(
            a.severity == AlarmSeverity.WARNING and a.state == AlarmState.ACTIVE
            for a in alarms
        )

        if has_unacked_critical:
            self._play_critical()
        elif has_unacked_warning:
            self._play_warning()
        else:
            self._stop_all_sounds()

    def _play_warning(self) -> None:
        """Play the warning tone (800 Hz intermittent)."""
        if self._critical_sound and self._critical_sound.isPlaying():
            self._critical_sound.stop()
        if self._warning_sound and not self._warning_sound.isPlaying():
            self._warning_sound.play()

    def _play_critical(self) -> None:
        """Play the critical tone (1200 Hz rapid)."""
        if self._warning_sound and self._warning_sound.isPlaying():
            self._warning_sound.stop()
        if self._critical_sound and not self._critical_sound.isPlaying():
            self._critical_sound.play()

    def _stop_all_sounds(self) -> None:
        """Stop all audible alerts."""
        if self._warning_sound and self._warning_sound.isPlaying():
            self._warning_sound.stop()
        if self._critical_sound and self._critical_sound.isPlaying():
            self._critical_sound.stop()

    def _on_acknowledge(self, alarm_id: str) -> None:
        """Handle acknowledge button click.

        Calls AlarmManager.acknowledge() to transition the alarm to
        ACKNOWLEDGED state, which silences the audible while maintaining
        the visual indicator.
        """
        try:
            self._alarm_manager.acknowledge(alarm_id)
            self._acknowledged_ids.add(alarm_id)
        except (KeyError, ValueError):
            # Alarm may have already cleared or been acknowledged
            pass

    def set_audible_enabled(self, enabled: bool) -> None:
        """Enable or disable audible alerts globally.

        Args:
            enabled: True to enable audible alerts, False to silence all.
        """
        self._audible_enabled = enabled
        if not enabled:
            self._stop_all_sounds()

    @property
    def audible_enabled(self) -> bool:
        """Whether audible alerts are currently enabled."""
        return self._audible_enabled

    @property
    def alarm_manager(self) -> AlarmManager:
        """Return the associated AlarmManager."""
        return self._alarm_manager

    def get_alarm_widget(self, alarm_id: str) -> Optional[AlarmItemWidget]:
        """Get the widget for a specific alarm by ID (for testing)."""
        return self._alarm_widgets.get(alarm_id)

    def get_active_alarm_count(self) -> int:
        """Return the number of currently displayed alarm widgets."""
        return len(self._alarm_widgets)

    def cleanup(self) -> None:
        """Clean up resources (timers, audio files)."""
        self._poll_timer.stop()
        self._stop_all_sounds()
        for widget in self._alarm_widgets.values():
            widget.stop_animation()

        # Clean up temp audio files
        import os

        if self._warning_tone_file:
            try:
                os.unlink(self._warning_tone_file.name)
            except OSError:
                pass
        if self._critical_tone_file:
            try:
                os.unlink(self._critical_tone_file.name)
            except OSError:
                pass
