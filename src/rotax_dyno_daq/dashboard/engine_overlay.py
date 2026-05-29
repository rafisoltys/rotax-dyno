"""Engine Overlay Widget - instrument-style dashboard for Rotax 912 ULS.

Displays cylinder EGT/AFR panels, oil temperature/pressure gauges, RPM readout,
power/throttle circular gauges, and IAT/CLT displays in a grid layout matching
the instrument panel design.

Requirements: 5.1, 5.2, 5.3, 5.6, 10.5
"""

from __future__ import annotations

import math
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from PyQt6.QtCore import QRectF, Qt, QTimer
from PyQt6.QtGui import QColor, QFont, QPainter, QPen, QBrush
from PyQt6.QtWidgets import (
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from rotax_dyno_daq.core.data_bus import DataBus, Sample, SubscriptionId
from rotax_dyno_daq.core.enums import AlarmSeverity


# --- Constants ---

STALE_THRESHOLD_SECONDS: float = 3.0
REFRESH_INTERVAL_MS: int = 100
MAX_RPM: float = 5800.0

# Default sensor positions (channel_id -> (x, y) for layout reference)
DEFAULT_SENSOR_POSITIONS: dict[str, tuple[int, int]] = {
    "EGT1": (0, 0), "AFR1": (0, 1),
    "EGT2": (1, 0), "AFR2": (1, 1),
    "EGT3": (0, 2), "AFR3": (0, 3),
    "EGT4": (1, 2), "AFR4": (1, 3),
    "OilTemp": (0, 4), "OilP": (0, 5),
    "RPM": (2, 1),
    "ChargeP": (2, 0),
    "IAT": (3, 0), "CLT": (3, 1),
}

# Colors
COLOR_BG = QColor(26, 26, 46)
COLOR_PANEL = QColor(34, 34, 58)
COLOR_BORDER = QColor(58, 58, 90)
COLOR_TEXT = QColor(232, 232, 240)
COLOR_TEXT_DIM = QColor(136, 136, 136)
COLOR_RED = QColor(229, 57, 53)
COLOR_BLUE = QColor(21, 101, 192)
COLOR_OLIVE = QColor(158, 157, 36)
COLOR_LIGHTBLUE = QColor(79, 195, 247)
COLOR_GREEN = QColor(67, 160, 71)
COLOR_STALE = QColor(102, 102, 102)
COLOR_BAR_BG = QColor(51, 51, 51)

# Gauge Ranges
RANGES: dict[str, tuple[float, float]] = {
    "EGT1": (0, 900), "EGT2": (0, 900), "EGT3": (0, 900), "EGT4": (0, 900),
    "AFR1": (10, 18), "AFR2": (10, 18), "AFR3": (10, 18), "AFR4": (10, 18),
    "OilTemp": (0, 150), "OilP": (0, 6),
    "RPM": (0, 6000),
    "ChargeP": (0, 100),
    "IAT": (0, 60), "CLT": (0, 120),
}


@dataclass
class SensorReading:
    """Holds the latest state for a channel."""

    channel_id: str
    value: float = 0.0
    unit: str = ""
    severity: AlarmSeverity = AlarmSeverity.NORMAL
    last_update_time: float = 0.0

    @property
    def is_stale(self) -> bool:
        if self.last_update_time == 0.0:
            return True
        return (time.monotonic() - self.last_update_time) >= STALE_THRESHOLD_SECONDS


def _pct(value: float, channel: str) -> float:
    """Return 0-1 fraction of value within channel range."""
    lo, hi = RANGES.get(channel, (0, 100))
    if hi == lo:
        return 0.0
    return max(0.0, min(1.0, (value - lo) / (hi - lo)))


# --- Custom Widgets ---


class CylinderPanel(QWidget):
    """Panel showing EGT and AFR for one cylinder with horizontal bar gauges."""

    def __init__(self, cyl_number: int, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._cyl_number = cyl_number
        self._egt_fraction = 0.0
        self._afr_fraction = 0.0
        self._egt_text = "---"
        self._afr_text = "---"
        self._stale = True
        self.setMinimumSize(160, 70)

    def set_egt(self, fraction: float, text: str) -> None:
        self._egt_fraction = max(0.0, min(1.0, fraction))
        self._egt_text = text

    def set_afr(self, fraction: float, text: str) -> None:
        self._afr_fraction = max(0.0, min(1.0, fraction))
        self._afr_text = text

    def set_stale(self, stale: bool) -> None:
        self._stale = stale

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()

        # Panel background
        painter.setPen(QPen(COLOR_BORDER, 1))
        painter.setBrush(COLOR_PANEL)
        painter.drawRoundedRect(QRectF(1, 1, w - 2, h - 2), 6, 6)

        # Title
        painter.setPen(COLOR_TEXT_DIM if not self._stale else COLOR_STALE)
        painter.setFont(QFont("Sans", 8, QFont.Weight.Bold))
        painter.drawText(
            QRectF(0, 4, w, 16), Qt.AlignmentFlag.AlignCenter,
            f"Cyl. {self._cyl_number}"
        )

        # EGT row
        self._draw_bar_row(painter, 24, "EGT", self._egt_fraction,
                           self._egt_text, COLOR_RED, w)
        # AFR row
        self._draw_bar_row(painter, 46, "AFR", self._afr_fraction,
                           self._afr_text, COLOR_BLUE, w)
        painter.end()

    def _draw_bar_row(
        self, painter: QPainter, y: int, label: str,
        fraction: float, value_text: str, color: QColor, w: int
    ) -> None:
        margin = 8
        label_w = 30
        value_w = 44
        bar_h = 10
        bar_x = margin + label_w + 4
        bar_w = w - bar_x - value_w - margin - 4

        text_color = COLOR_TEXT if not self._stale else COLOR_STALE
        painter.setPen(text_color)
        painter.setFont(QFont("Sans", 8, QFont.Weight.Bold))
        painter.drawText(QRectF(margin, y, label_w, bar_h + 4),
                         Qt.AlignmentFlag.AlignVCenter, label)

        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(COLOR_BAR_BG)
        painter.drawRoundedRect(QRectF(bar_x, y + 2, bar_w, bar_h), 3, 3)

        fill_color = color if not self._stale else COLOR_STALE
        painter.setBrush(fill_color)
        fill_w = bar_w * fraction
        painter.drawRoundedRect(QRectF(bar_x, y + 2, fill_w, bar_h), 3, 3)

        painter.setPen(text_color)
        painter.setFont(QFont("Sans", 9, QFont.Weight.Bold))
        painter.drawText(
            QRectF(bar_x + bar_w + 4, y, value_w, bar_h + 4),
            Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight,
            value_text,
        )


class BarGauge(QWidget):
    """Vertical bar gauge with value label."""

    def __init__(
        self, label: str, unit: str, color: QColor,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self._label = label
        self._unit = unit
        self._color = color
        self._fraction = 0.0
        self._value_text = "---"
        self._stale = True
        self.setMinimumSize(40, 100)
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Expanding)

    def set_value(self, fraction: float, text: str, stale: bool = False) -> None:
        self._fraction = max(0.0, min(1.0, fraction))
        self._value_text = text
        self._stale = stale

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()

        label_h = 16
        value_h = 18
        bar_x = (w - 20) // 2
        bar_w = 20
        bar_top = label_h + 4
        bar_h = h - bar_top - value_h - 20

        # Label
        painter.setPen(COLOR_TEXT_DIM if not self._stale else COLOR_STALE)
        painter.setFont(QFont("Sans", 8))
        painter.drawText(QRectF(0, 0, w, label_h), Qt.AlignmentFlag.AlignCenter, self._label)

        # Bar track
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(COLOR_BAR_BG)
        painter.drawRoundedRect(QRectF(bar_x, bar_top, bar_w, bar_h), 3, 3)

        # Bar fill
        fill_h = bar_h * self._fraction
        color = self._color if not self._stale else COLOR_STALE
        painter.setBrush(color)
        painter.drawRoundedRect(
            QRectF(bar_x, bar_top + bar_h - fill_h, bar_w, fill_h), 3, 3
        )

        # Value text
        painter.setPen(COLOR_TEXT if not self._stale else COLOR_STALE)
        painter.setFont(QFont("Sans", 9, QFont.Weight.Bold))
        painter.drawText(
            QRectF(0, bar_top + bar_h + 4, w, value_h),
            Qt.AlignmentFlag.AlignCenter, self._value_text,
        )

        # Unit
        painter.setPen(COLOR_TEXT_DIM)
        painter.setFont(QFont("Sans", 7))
        painter.drawText(
            QRectF(0, bar_top + bar_h + 20, w, 14),
            Qt.AlignmentFlag.AlignCenter, self._unit,
        )
        painter.end()


class CircularGauge(QWidget):
    """Circular arc gauge for percentage values (Power, Throttle)."""

    def __init__(
        self, label: str, color: QColor = COLOR_BLUE,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self._label = label
        self._color = color
        self._fraction = 0.0
        self._value_text = "---%"
        self._stale = True
        self.setMinimumSize(120, 140)
        self.setMaximumSize(140, 160)

    def set_value(self, fraction: float, text: str, stale: bool = False) -> None:
        self._fraction = max(0.0, min(1.0, fraction))
        self._value_text = text
        self._stale = stale

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()
        cx, cy = w // 2, (h - 20) // 2
        radius = min(cx, cy) - 10

        # Background arc (240 degrees, from 150 to -90 in Qt angles)
        pen = QPen(COLOR_BAR_BG, 8)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setPen(pen)
        rect = QRectF(cx - radius, cy - radius, radius * 2, radius * 2)
        # Draw 240 degree arc starting from bottom-left
        painter.drawArc(rect, -30 * 16, -240 * 16)

        # Value arc
        color = self._color if not self._stale else COLOR_STALE
        pen = QPen(color, 8)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setPen(pen)
        span = int(self._fraction * 240 * 16)
        painter.drawArc(rect, -30 * 16, -span)

        # Center text
        painter.setPen(COLOR_TEXT if not self._stale else COLOR_STALE)
        painter.setFont(QFont("Sans", 12, QFont.Weight.Bold))
        painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, self._value_text)

        # Label below
        painter.setPen(COLOR_TEXT_DIM)
        painter.setFont(QFont("Sans", 8, QFont.Weight.Bold))
        painter.drawText(
            QRectF(0, h - 18, w, 18),
            Qt.AlignmentFlag.AlignCenter, self._label,
        )
        painter.end()


class OilPanel(QWidget):
    """Panel with two vertical bar gauges for oil temp and pressure."""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 4, 8, 4)

        title = QLabel("OIL")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        title.setStyleSheet("color: #888; font-size: 9px; font-weight: bold;")
        layout.addWidget(title)

        gauges_layout = QHBoxLayout()
        gauges_layout.setSpacing(16)

        self.temp_gauge = BarGauge("\u00B0C", "\u00B0C", COLOR_RED)
        self.press_gauge = BarGauge("bar", "bar", COLOR_OLIVE)

        gauges_layout.addWidget(self.temp_gauge)
        gauges_layout.addWidget(self.press_gauge)
        layout.addLayout(gauges_layout)

        self.setMinimumSize(120, 120)


class RpmDisplay(QWidget):
    """Large RPM number display."""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._value_text = "---"
        self._stale = True
        self.setMinimumSize(180, 80)

    def set_value(self, text: str, stale: bool = False) -> None:
        self._value_text = text
        self._stale = stale

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()

        color = COLOR_RED if not self._stale else COLOR_STALE
        painter.setPen(color)
        font = QFont("Sans", 36, QFont.Weight.Black)
        painter.setFont(font)
        painter.drawText(QRectF(0, 0, w, h - 16), Qt.AlignmentFlag.AlignCenter,
                         self._value_text)

        painter.setPen(COLOR_TEXT_DIM)
        painter.setFont(QFont("Sans", 10, QFont.Weight.Bold))
        painter.drawText(QRectF(0, h - 20, w, 20), Qt.AlignmentFlag.AlignCenter, "RPM")
        painter.end()


class EnvDisplay(QWidget):
    """Horizontal bar display for IAT and CLT values."""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._iat_fraction = 0.0
        self._clt_fraction = 0.0
        self._iat_text = "---\u00B0C"
        self._clt_text = "---\u00B0C"
        self._iat_stale = True
        self._clt_stale = True
        self.setMinimumSize(300, 40)

    def set_iat(self, fraction: float, text: str, stale: bool = False) -> None:
        self._iat_fraction = max(0.0, min(1.0, fraction))
        self._iat_text = text
        self._iat_stale = stale

    def set_clt(self, fraction: float, text: str, stale: bool = False) -> None:
        self._clt_fraction = max(0.0, min(1.0, fraction))
        self._clt_text = text
        self._clt_stale = stale

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()

        # Panel background
        painter.setPen(QPen(COLOR_BORDER, 1))
        painter.setBrush(COLOR_PANEL)
        painter.drawRoundedRect(QRectF(1, 1, w - 2, h - 2), 6, 6)

        half_w = w // 2
        # IAT (left half)
        self._draw_inline_gauge(painter, 12, h // 2 - 6, half_w - 24,
                                "IAT", self._iat_fraction, self._iat_text,
                                COLOR_OLIVE, self._iat_stale)
        # CLT (right half)
        self._draw_inline_gauge(painter, half_w + 12, h // 2 - 6, half_w - 24,
                                "CLT", self._clt_fraction, self._clt_text,
                                COLOR_LIGHTBLUE, self._clt_stale)
        painter.end()

    def _draw_inline_gauge(
        self, painter: QPainter, x: int, y: int, available_w: int,
        label: str, fraction: float, value_text: str, color: QColor, stale: bool
    ) -> None:
        label_w = 30
        value_w = 50
        bar_h = 10
        bar_w = available_w - label_w - value_w - 8

        text_color = COLOR_TEXT if not stale else COLOR_STALE
        painter.setPen(text_color)
        painter.setFont(QFont("Sans", 8, QFont.Weight.Bold))
        painter.drawText(QRectF(x, y, label_w, bar_h + 4),
                         Qt.AlignmentFlag.AlignVCenter, label)

        bar_x = x + label_w + 4
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(COLOR_BAR_BG)
        painter.drawRoundedRect(QRectF(bar_x, y + 2, bar_w, bar_h), 3, 3)

        fill_color = color if not stale else COLOR_STALE
        painter.setBrush(fill_color)
        fill_w = bar_w * fraction
        painter.drawRoundedRect(QRectF(bar_x, y + 2, fill_w, bar_h), 3, 3)

        painter.setPen(text_color)
        painter.setFont(QFont("Sans", 9, QFont.Weight.Bold))
        painter.drawText(
            QRectF(bar_x + bar_w + 4, y, value_w, bar_h + 4),
            Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight,
            value_text,
        )


# --- Constants for stale detection ---
STALE_THRESHOLD_SECONDS: float = 3.0
DEFAULT_SENSOR_POSITIONS: dict[str, tuple[int, int]] = {}  # Not used in new layout but kept for compat


class EngineOverlayWidget(QWidget):
    """Instrument-style engine overlay dashboard.

    Combines CylinderPanels, OilPanel, RpmDisplay, CircularGauges, and EnvDisplay
    into a grid layout matching the instrument panel design.

    Subscribes to DataBus for live data updates and refreshes at 10 Hz.
    """

    def __init__(
        self,
        data_bus: Optional["DataBus"] = None,
        alarm_manager=None,
        sensor_positions: Optional[dict[str, tuple[int, int]]] = None,
        background_image_path: Optional[Path] = None,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        from rotax_dyno_daq.core.data_bus import DataBus as _DataBus

        self._data_bus = data_bus
        self._alarm_manager = alarm_manager
        self._readings: dict[str, SensorReading] = {}
        self._subscription_ids: list[int] = []

        self.setStyleSheet("background-color: #f0f0f0;")
        self.setMinimumSize(600, 500)

        # Create sub-widgets
        layout = QGridLayout(self)
        layout.setSpacing(8)
        layout.setContentsMargins(10, 10, 10, 10)

        # Cylinder panels
        self._cyl1 = CylinderPanel(1)
        self._cyl2 = CylinderPanel(2)
        self._cyl3 = CylinderPanel(3)
        self._cyl4 = CylinderPanel(4)

        # Oil panel
        self._oil_panel = OilPanel()

        # RPM display
        self._rpm_display = RpmDisplay()

        # Circular gauges
        self._power_gauge = CircularGauge("Power")
        self._throttle_gauge = CircularGauge("Throttle")

        # Environment display
        self._env_display = EnvDisplay()

        # Layout: row 0-1 = cylinders + oil
        layout.addWidget(self._cyl1, 0, 0)
        layout.addWidget(self._oil_panel, 0, 1, 2, 1)
        layout.addWidget(self._cyl3, 0, 2)
        layout.addWidget(self._cyl2, 1, 0)
        layout.addWidget(self._cyl4, 1, 2)

        # Row 2 = Power + RPM + Throttle
        layout.addWidget(self._power_gauge, 2, 0)
        layout.addWidget(self._rpm_display, 2, 1)
        layout.addWidget(self._throttle_gauge, 2, 2)

        # Row 3 = IAT + CLT
        layout.addWidget(self._env_display, 3, 0, 1, 3)

        # Subscribe to DataBus
        if self._data_bus is not None:
            sub_id = self._data_bus.subscribe("*", self._on_sample)
            self._subscription_ids.append(sub_id)

        # Refresh timer at 10 Hz
        self._refresh_timer = QTimer(self)
        self._refresh_timer.setInterval(100)
        self._refresh_timer.timeout.connect(self._on_refresh)
        self._refresh_timer.start()

    def _on_sample(self, sample) -> None:
        """Handle incoming sample from DataBus."""
        channel_id = getattr(sample, "channel_id", None)
        value = getattr(sample, "calibrated_value", None)
        if channel_id is None or value is None:
            return
        self._readings[channel_id] = SensorReading(
            channel_id=channel_id, value=value,
            last_update=time.monotonic(), stale=False,
        )

    def _on_refresh(self) -> None:
        """Update all sub-widgets with latest readings."""
        now = time.monotonic()

        def get(ch: str) -> tuple[float, bool]:
            r = self._readings.get(ch)
            if r is None:
                return 0.0, True
            stale = (now - r.last_update) > STALE_THRESHOLD_SECONDS
            return r.value, stale

        # Cylinders
        for i, (cyl, egt_ch, afr_ch) in enumerate([
            (self._cyl1, "EGT1", "AFR1"),
            (self._cyl2, "EGT2", "AFR2"),
            (self._cyl3, "EGT3", "AFR3"),
            (self._cyl4, "EGT4", "AFR4"),
        ]):
            egt_val, egt_stale = get(egt_ch)
            afr_val, afr_stale = get(afr_ch)
            cyl.set_egt(min(egt_val / 900.0, 1.0), f"{egt_val:.0f}")
            cyl.set_afr(min(afr_val / 20.0, 1.0), f"{afr_val:.1f}")
            cyl.set_stale(egt_stale and afr_stale)
            cyl.update()

        # Oil
        oilt_val, oilt_stale = get("OilTemp")
        oilp_val, oilp_stale = get("OilP")
        self._oil_panel.temp_gauge.set_value(oilt_val / 150.0, f"{oilt_val:.0f}", oilt_stale)
        self._oil_panel.press_gauge.set_value(oilp_val / 10.0, f"{oilp_val:.1f}", oilp_stale)
        self._oil_panel.update()

        # RPM
        rpm_val, rpm_stale = get("RPM")
        self._rpm_display.set_value(f"{rpm_val:.0f}", rpm_stale)
        self._rpm_display.update()

        # Power (RPM/5800 * 100%)
        power_pct = min(rpm_val / 5800.0, 1.0) if rpm_val > 0 else 0.0
        self._power_gauge.set_value(power_pct, f"{int(power_pct*100)}%", rpm_stale)
        self._power_gauge.update()

        # Throttle (ChargeP)
        charge_val, charge_stale = get("ChargeP")
        if charge_val == 0.0:
            charge_val, charge_stale = get("Charge")
        throttle_pct = min(charge_val / 2.5, 1.0) if charge_val > 0 else 0.0
        self._throttle_gauge.set_value(throttle_pct, f"{int(throttle_pct*100)}%", charge_stale)
        self._throttle_gauge.update()

        # IAT + CLT
        iat_val, iat_stale = get("IAT")
        clt_val, clt_stale = get("CLT")
        self._env_display.set_iat(iat_val / 60.0, f"{iat_val:.0f}°C", iat_stale)
        self._env_display.set_clt(clt_val / 120.0, f"{clt_val:.0f}°C", clt_stale)
        self._env_display.update()

    def is_channel_stale(self, channel_id: str) -> bool:
        """Check if a channel is stale (backward compat)."""
        r = self._readings.get(channel_id)
        if r is None:
            return True
        return (time.monotonic() - r.last_update) > STALE_THRESHOLD_SECONDS

    def cleanup(self) -> None:
        """Stop timer and unsubscribe from DataBus."""
        self._refresh_timer.stop()
        if self._data_bus is not None:
            for sub_id in self._subscription_ids:
                self._data_bus.unsubscribe(sub_id)
            self._subscription_ids.clear()
