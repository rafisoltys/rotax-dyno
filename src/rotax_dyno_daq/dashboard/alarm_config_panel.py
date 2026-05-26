"""Alarm Threshold Configuration Panel - configure alarm thresholds per channel.

Implements Requirement 10.1 for the dashboard UI:
- Channel selector dropdown
- High warning, high critical, low warning, low critical threshold inputs
- Deadband input
- Enable/disable toggle per channel
- Apply button
"""

from __future__ import annotations

from typing import Optional

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from rotax_dyno_daq.alarms.manager import AlarmManager
from rotax_dyno_daq.core.models import AlarmConfig, AlarmThreshold

# Minimum touch target size in pixels (12mm × 12mm at 96 DPI ≈ 45×45 px)
MIN_TOUCH_TARGET_PX = 45


class AlarmConfigPanel(QWidget):
    """Alarm threshold configuration panel.

    Provides:
    - Channel selector dropdown
    - High warning, high critical, low warning, low critical threshold inputs
    - Deadband input
    - Enable/disable toggle per channel
    - Apply button to update alarm thresholds

    All interactive elements use minimum 45x45px touch targets.
    """

    def __init__(
        self,
        alarm_manager: AlarmManager,
        channel_ids: Optional[list[str]] = None,
        parent: Optional[QWidget] = None,
    ) -> None:
        """Initialize the alarm configuration panel.

        Args:
            alarm_manager: The AlarmManager instance for configuring thresholds.
            channel_ids: List of available channel IDs for the selector.
            parent: Optional parent widget.
        """
        super().__init__(parent)
        self._alarm_manager = alarm_manager
        self._channel_ids = channel_ids or []
        self._setup_ui()

    def _setup_ui(self) -> None:
        """Set up the panel layout."""
        layout = QVBoxLayout(self)
        layout.setSpacing(8)

        # --- Channel Selection ---
        channel_group = QGroupBox("Channel Selection")
        channel_layout = QHBoxLayout(channel_group)

        channel_layout.addWidget(QLabel("Channel:"))
        self._channel_selector = QComboBox()
        self._channel_selector.setMinimumHeight(MIN_TOUCH_TARGET_PX)
        self._channel_selector.addItems(self._channel_ids)
        channel_layout.addWidget(self._channel_selector, stretch=1)

        # Enable/disable toggle
        self._enabled_checkbox = QCheckBox("Enabled")
        self._enabled_checkbox.setMinimumSize(MIN_TOUCH_TARGET_PX, MIN_TOUCH_TARGET_PX)
        self._enabled_checkbox.setChecked(True)
        channel_layout.addWidget(self._enabled_checkbox)

        layout.addWidget(channel_group)

        # --- High Thresholds ---
        high_group = QGroupBox("High Thresholds")
        high_layout = QVBoxLayout(high_group)

        # High Warning
        hw_row = QHBoxLayout()
        hw_row.addWidget(QLabel("High Warning:"))
        self._high_warning_input = self._create_threshold_spinbox()
        hw_row.addWidget(self._high_warning_input)
        self._high_warning_enabled = QCheckBox("Set")
        self._high_warning_enabled.setMinimumSize(
            MIN_TOUCH_TARGET_PX, MIN_TOUCH_TARGET_PX
        )
        self._high_warning_enabled.setChecked(False)
        self._high_warning_enabled.toggled.connect(
            self._high_warning_input.setEnabled
        )
        self._high_warning_input.setEnabled(False)
        hw_row.addWidget(self._high_warning_enabled)
        high_layout.addLayout(hw_row)

        # High Critical
        hc_row = QHBoxLayout()
        hc_row.addWidget(QLabel("High Critical:"))
        self._high_critical_input = self._create_threshold_spinbox()
        hc_row.addWidget(self._high_critical_input)
        self._high_critical_enabled = QCheckBox("Set")
        self._high_critical_enabled.setMinimumSize(
            MIN_TOUCH_TARGET_PX, MIN_TOUCH_TARGET_PX
        )
        self._high_critical_enabled.setChecked(False)
        self._high_critical_enabled.toggled.connect(
            self._high_critical_input.setEnabled
        )
        self._high_critical_input.setEnabled(False)
        hc_row.addWidget(self._high_critical_enabled)
        high_layout.addLayout(hc_row)

        layout.addWidget(high_group)

        # --- Low Thresholds ---
        low_group = QGroupBox("Low Thresholds")
        low_layout = QVBoxLayout(low_group)

        # Low Warning
        lw_row = QHBoxLayout()
        lw_row.addWidget(QLabel("Low Warning:"))
        self._low_warning_input = self._create_threshold_spinbox()
        lw_row.addWidget(self._low_warning_input)
        self._low_warning_enabled = QCheckBox("Set")
        self._low_warning_enabled.setMinimumSize(
            MIN_TOUCH_TARGET_PX, MIN_TOUCH_TARGET_PX
        )
        self._low_warning_enabled.setChecked(False)
        self._low_warning_enabled.toggled.connect(
            self._low_warning_input.setEnabled
        )
        self._low_warning_input.setEnabled(False)
        lw_row.addWidget(self._low_warning_enabled)
        low_layout.addLayout(lw_row)

        # Low Critical
        lc_row = QHBoxLayout()
        lc_row.addWidget(QLabel("Low Critical:"))
        self._low_critical_input = self._create_threshold_spinbox()
        lc_row.addWidget(self._low_critical_input)
        self._low_critical_enabled = QCheckBox("Set")
        self._low_critical_enabled.setMinimumSize(
            MIN_TOUCH_TARGET_PX, MIN_TOUCH_TARGET_PX
        )
        self._low_critical_enabled.setChecked(False)
        self._low_critical_enabled.toggled.connect(
            self._low_critical_input.setEnabled
        )
        self._low_critical_input.setEnabled(False)
        lc_row.addWidget(self._low_critical_enabled)
        low_layout.addLayout(lc_row)

        layout.addWidget(low_group)

        # --- Deadband ---
        deadband_group = QGroupBox("Deadband")
        deadband_layout = QHBoxLayout(deadband_group)

        deadband_layout.addWidget(QLabel("Deadband:"))
        self._deadband_input = QDoubleSpinBox()
        self._deadband_input.setMinimumHeight(MIN_TOUCH_TARGET_PX)
        self._deadband_input.setRange(0.0, 10000.0)
        self._deadband_input.setDecimals(3)
        self._deadband_input.setValue(0.0)
        self._deadband_input.setToolTip(
            "Alarm clears when value returns within threshold by this amount"
        )
        deadband_layout.addWidget(self._deadband_input)

        deadband_layout.addStretch()
        layout.addWidget(deadband_group)

        # --- Apply Button ---
        apply_row = QHBoxLayout()
        apply_row.addStretch()
        self._apply_button = QPushButton("Apply Alarm Thresholds")
        self._apply_button.setMinimumSize(MIN_TOUCH_TARGET_PX, MIN_TOUCH_TARGET_PX)
        self._apply_button.setStyleSheet(
            "QPushButton { background-color: #e65100; color: white; "
            "font-weight: bold; padding: 8px 16px; border-radius: 4px; }"
            "QPushButton:hover { background-color: #f57c00; }"
        )
        self._apply_button.clicked.connect(self._on_apply)
        apply_row.addWidget(self._apply_button)
        layout.addLayout(apply_row)

        layout.addStretch()

    def _create_threshold_spinbox(self) -> QDoubleSpinBox:
        """Create a threshold input spinbox with standard settings."""
        spinbox = QDoubleSpinBox()
        spinbox.setMinimumHeight(MIN_TOUCH_TARGET_PX)
        spinbox.setRange(-100000.0, 100000.0)
        spinbox.setDecimals(3)
        spinbox.setValue(0.0)
        return spinbox

    def _on_apply(self) -> None:
        """Apply the alarm threshold configuration to the selected channel."""
        channel_id = self._channel_selector.currentText()
        if not channel_id:
            QMessageBox.warning(
                self, "No Channel", "Please select a channel."
            )
            return

        config = self._build_alarm_config(channel_id)
        if config is None:
            return  # Validation error already shown

        self._alarm_manager.configure_threshold(channel_id, config)
        QMessageBox.information(
            self,
            "Thresholds Applied",
            f"Alarm thresholds applied to channel '{channel_id}'.",
        )

    def _build_alarm_config(self, channel_id: str) -> Optional[AlarmConfig]:
        """Build an AlarmConfig from the current UI inputs.

        Args:
            channel_id: The channel to configure.

        Returns:
            An AlarmConfig if inputs are valid, None otherwise.
        """
        # Read threshold values (None if not enabled)
        high_warning: Optional[float] = None
        high_critical: Optional[float] = None
        low_warning: Optional[float] = None
        low_critical: Optional[float] = None

        if self._high_warning_enabled.isChecked():
            high_warning = self._high_warning_input.value()

        if self._high_critical_enabled.isChecked():
            high_critical = self._high_critical_input.value()

        if self._low_warning_enabled.isChecked():
            low_warning = self._low_warning_input.value()

        if self._low_critical_enabled.isChecked():
            low_critical = self._low_critical_input.value()

        deadband = self._deadband_input.value()

        # Validate threshold ordering
        if high_warning is not None and high_critical is not None:
            if high_warning >= high_critical:
                QMessageBox.warning(
                    self,
                    "Validation Error",
                    "High warning must be less than high critical.",
                )
                return None

        if low_warning is not None and low_critical is not None:
            if low_warning <= low_critical:
                QMessageBox.warning(
                    self,
                    "Validation Error",
                    "Low warning must be greater than low critical.",
                )
                return None

        if deadband < 0:
            QMessageBox.warning(
                self,
                "Validation Error",
                "Deadband must be non-negative.",
            )
            return None

        thresholds = AlarmThreshold(
            high_warning=high_warning,
            high_critical=high_critical,
            low_warning=low_warning,
            low_critical=low_critical,
            deadband=deadband,
        )

        enabled = self._enabled_checkbox.isChecked()

        return AlarmConfig(
            channel_id=channel_id,
            thresholds=thresholds,
            enabled=enabled,
        )

    def set_channel_ids(self, channel_ids: list[str]) -> None:
        """Update the available channel IDs in the selector.

        Args:
            channel_ids: List of channel IDs to populate the dropdown.
        """
        self._channel_ids = channel_ids
        self._channel_selector.clear()
        self._channel_selector.addItems(channel_ids)

    @property
    def channel_selector(self) -> QComboBox:
        """Access the channel selector (for testing)."""
        return self._channel_selector

    @property
    def enabled_checkbox(self) -> QCheckBox:
        """Access the enabled checkbox (for testing)."""
        return self._enabled_checkbox

    @property
    def high_warning_input(self) -> QDoubleSpinBox:
        """Access the high warning input (for testing)."""
        return self._high_warning_input

    @property
    def high_warning_enabled(self) -> QCheckBox:
        """Access the high warning enabled checkbox (for testing)."""
        return self._high_warning_enabled

    @property
    def high_critical_input(self) -> QDoubleSpinBox:
        """Access the high critical input (for testing)."""
        return self._high_critical_input

    @property
    def high_critical_enabled(self) -> QCheckBox:
        """Access the high critical enabled checkbox (for testing)."""
        return self._high_critical_enabled

    @property
    def low_warning_input(self) -> QDoubleSpinBox:
        """Access the low warning input (for testing)."""
        return self._low_warning_input

    @property
    def low_warning_enabled(self) -> QCheckBox:
        """Access the low warning enabled checkbox (for testing)."""
        return self._low_warning_enabled

    @property
    def low_critical_input(self) -> QDoubleSpinBox:
        """Access the low critical input (for testing)."""
        return self._low_critical_input

    @property
    def low_critical_enabled(self) -> QCheckBox:
        """Access the low critical enabled checkbox (for testing)."""
        return self._low_critical_enabled

    @property
    def deadband_input(self) -> QDoubleSpinBox:
        """Access the deadband input (for testing)."""
        return self._deadband_input

    @property
    def apply_button(self) -> QPushButton:
        """Access the apply button (for testing)."""
        return self._apply_button

    @property
    def alarm_manager(self) -> AlarmManager:
        """Access the alarm manager (for testing)."""
        return self._alarm_manager
