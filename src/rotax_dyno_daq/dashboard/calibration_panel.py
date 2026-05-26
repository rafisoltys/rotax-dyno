"""Calibration Configuration Panel - configure channel calibration profiles.

Implements Requirement 11.1 for the dashboard UI:
- Channel selector dropdown
- Unit label input
- Calibration type selector (Linear / Lookup Table)
- Linear: slope and offset inputs (QDoubleSpinBox)
- Lookup Table: table widget for voltage-to-unit point pairs (add/remove rows)
- Min/max valid voltage inputs
- Apply button to hot-swap calibration
"""

from __future__ import annotations

from typing import Optional

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import (
    QComboBox,
    QDoubleSpinBox,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSizePolicy,
    QStackedWidget,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from rotax_dyno_daq.calibration.engine import CalibrationEngine
from rotax_dyno_daq.core.enums import CalibrationType
from rotax_dyno_daq.core.models import (
    CalibrationProfile,
    LinearCalibrationParams,
    LookupTableParams,
)

# Minimum touch target size in pixels (12mm × 12mm at 96 DPI ≈ 45×45 px)
MIN_TOUCH_TARGET_PX = 45


class CalibrationPanel(QWidget):
    """Calibration configuration panel for channel calibration profiles.

    Provides:
    - Channel selector dropdown
    - Unit label input
    - Calibration type selector (Linear / Lookup Table)
    - Linear mode: slope and offset inputs (QDoubleSpinBox)
    - Lookup Table mode: table widget for voltage-to-unit point pairs
    - Min/max valid voltage inputs
    - Apply button to hot-swap calibration without restarting acquisition
    """

    def __init__(
        self,
        calibration_engine: CalibrationEngine,
        channel_ids: Optional[list[str]] = None,
        parent: Optional[QWidget] = None,
    ) -> None:
        """Initialize the calibration configuration panel.

        Args:
            calibration_engine: The CalibrationEngine for applying profiles.
            channel_ids: List of available channel IDs for the selector.
            parent: Optional parent widget.
        """
        super().__init__(parent)
        self._calibration_engine = calibration_engine
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

        layout.addWidget(channel_group)

        # --- Calibration Settings ---
        settings_group = QGroupBox("Calibration Settings")
        settings_layout = QVBoxLayout(settings_group)

        # Unit label
        unit_row = QHBoxLayout()
        unit_row.addWidget(QLabel("Unit Label:"))
        self._unit_input = QLineEdit()
        self._unit_input.setPlaceholderText("e.g., °C, bar, RPM, λ")
        self._unit_input.setMinimumHeight(MIN_TOUCH_TARGET_PX)
        unit_row.addWidget(self._unit_input)
        settings_layout.addLayout(unit_row)

        # Calibration type selector
        type_row = QHBoxLayout()
        type_row.addWidget(QLabel("Calibration Type:"))
        self._type_selector = QComboBox()
        self._type_selector.setMinimumHeight(MIN_TOUCH_TARGET_PX)
        self._type_selector.addItems(["Linear", "Lookup Table"])
        self._type_selector.currentIndexChanged.connect(self._on_type_changed)
        type_row.addWidget(self._type_selector)
        settings_layout.addLayout(type_row)

        # Voltage range
        voltage_row = QHBoxLayout()
        voltage_row.addWidget(QLabel("Min Valid Voltage:"))
        self._min_voltage = QDoubleSpinBox()
        self._min_voltage.setMinimumHeight(MIN_TOUCH_TARGET_PX)
        self._min_voltage.setRange(-10.0, 10.0)
        self._min_voltage.setDecimals(3)
        self._min_voltage.setValue(0.0)
        self._min_voltage.setSuffix(" V")
        voltage_row.addWidget(self._min_voltage)

        voltage_row.addWidget(QLabel("Max Valid Voltage:"))
        self._max_voltage = QDoubleSpinBox()
        self._max_voltage.setMinimumHeight(MIN_TOUCH_TARGET_PX)
        self._max_voltage.setRange(-10.0, 10.0)
        self._max_voltage.setDecimals(3)
        self._max_voltage.setValue(5.0)
        self._max_voltage.setSuffix(" V")
        voltage_row.addWidget(self._max_voltage)
        settings_layout.addLayout(voltage_row)

        # Stacked widget for Linear / Lookup Table parameters
        self._params_stack = QStackedWidget()

        # Linear parameters page
        self._linear_page = self._create_linear_page()
        self._params_stack.addWidget(self._linear_page)

        # Lookup table parameters page
        self._lookup_page = self._create_lookup_page()
        self._params_stack.addWidget(self._lookup_page)

        settings_layout.addWidget(self._params_stack)
        layout.addWidget(settings_group)

        # --- Apply Button ---
        apply_row = QHBoxLayout()
        apply_row.addStretch()
        self._apply_button = QPushButton("Apply Calibration")
        self._apply_button.setMinimumSize(MIN_TOUCH_TARGET_PX, MIN_TOUCH_TARGET_PX)
        self._apply_button.setStyleSheet(
            "QPushButton { background-color: #1565c0; color: white; "
            "font-weight: bold; padding: 8px 16px; border-radius: 4px; }"
            "QPushButton:hover { background-color: #1976d2; }"
        )
        self._apply_button.clicked.connect(self._on_apply)
        apply_row.addWidget(self._apply_button)
        layout.addLayout(apply_row)

    def _create_linear_page(self) -> QWidget:
        """Create the linear calibration parameters page."""
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 8, 0, 0)

        params_row = QHBoxLayout()

        params_row.addWidget(QLabel("Slope:"))
        self._slope_input = QDoubleSpinBox()
        self._slope_input.setMinimumHeight(MIN_TOUCH_TARGET_PX)
        self._slope_input.setRange(-1e6, 1e6)
        self._slope_input.setDecimals(6)
        self._slope_input.setValue(1.0)
        params_row.addWidget(self._slope_input)

        params_row.addWidget(QLabel("Offset:"))
        self._offset_input = QDoubleSpinBox()
        self._offset_input.setMinimumHeight(MIN_TOUCH_TARGET_PX)
        self._offset_input.setRange(-1e6, 1e6)
        self._offset_input.setDecimals(6)
        self._offset_input.setValue(0.0)
        params_row.addWidget(self._offset_input)

        layout.addLayout(params_row)

        # Formula preview
        self._formula_label = QLabel("y = 1.000000 × x + 0.000000")
        self._formula_label.setFont(QFont("", 9))
        self._formula_label.setStyleSheet("QLabel { color: #888; }")
        layout.addWidget(self._formula_label)

        self._slope_input.valueChanged.connect(self._update_formula_preview)
        self._offset_input.valueChanged.connect(self._update_formula_preview)

        layout.addStretch()
        return page

    def _create_lookup_page(self) -> QWidget:
        """Create the lookup table calibration parameters page."""
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 8, 0, 0)

        # Table for voltage-to-unit pairs
        self._lookup_table = QTableWidget()
        self._lookup_table.setColumnCount(2)
        self._lookup_table.setHorizontalHeaderLabels(["Voltage (V)", "Unit Value"])
        self._lookup_table.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.Stretch
        )
        self._lookup_table.verticalHeader().setDefaultSectionSize(MIN_TOUCH_TARGET_PX)
        layout.addWidget(self._lookup_table, stretch=1)

        # Add/Remove row buttons
        button_row = QHBoxLayout()

        self._add_row_button = QPushButton("+ Add Point")
        self._add_row_button.setMinimumSize(MIN_TOUCH_TARGET_PX, MIN_TOUCH_TARGET_PX)
        self._add_row_button.clicked.connect(self._on_add_row)
        button_row.addWidget(self._add_row_button)

        self._remove_row_button = QPushButton("- Remove Point")
        self._remove_row_button.setMinimumSize(MIN_TOUCH_TARGET_PX, MIN_TOUCH_TARGET_PX)
        self._remove_row_button.clicked.connect(self._on_remove_row)
        button_row.addWidget(self._remove_row_button)

        button_row.addStretch()

        point_count_label = QLabel("(2-64 points required)")
        point_count_label.setStyleSheet("QLabel { color: #888; }")
        button_row.addWidget(point_count_label)

        layout.addLayout(button_row)

        # Initialize with 2 empty rows
        self._add_lookup_row(0.0, 0.0)
        self._add_lookup_row(5.0, 100.0)

        return page

    def _on_type_changed(self, index: int) -> None:
        """Handle calibration type selection change."""
        self._params_stack.setCurrentIndex(index)

    def _update_formula_preview(self) -> None:
        """Update the linear formula preview label."""
        slope = self._slope_input.value()
        offset = self._offset_input.value()
        self._formula_label.setText(f"y = {slope:.6f} × x + {offset:.6f}")

    def _on_add_row(self) -> None:
        """Add a new row to the lookup table."""
        if self._lookup_table.rowCount() >= 64:
            QMessageBox.warning(
                self,
                "Maximum Points",
                "Lookup table can have at most 64 points.",
            )
            return
        self._add_lookup_row(0.0, 0.0)

    def _add_lookup_row(self, voltage: float = 0.0, value: float = 0.0) -> None:
        """Add a row to the lookup table with given values."""
        row = self._lookup_table.rowCount()
        self._lookup_table.insertRow(row)
        self._lookup_table.setItem(row, 0, QTableWidgetItem(f"{voltage:.4f}"))
        self._lookup_table.setItem(row, 1, QTableWidgetItem(f"{value:.4f}"))

    def _on_remove_row(self) -> None:
        """Remove the selected row from the lookup table."""
        selected = self._lookup_table.currentRow()
        if selected >= 0:
            self._lookup_table.removeRow(selected)
        elif self._lookup_table.rowCount() > 0:
            # Remove last row if none selected
            self._lookup_table.removeRow(self._lookup_table.rowCount() - 1)

    def _on_apply(self) -> None:
        """Apply the calibration profile to the selected channel."""
        channel_id = self._channel_selector.currentText()
        if not channel_id:
            QMessageBox.warning(
                self, "No Channel", "Please select a channel."
            )
            return

        profile = self._build_profile()
        if profile is None:
            return  # Validation error already shown

        # Validate the profile
        result = self._calibration_engine.validate_profile(profile)
        if not result.valid:
            QMessageBox.critical(
                self,
                "Validation Error",
                "Calibration profile is invalid:\n" + "\n".join(result.errors),
            )
            return

        # Apply the profile (hot-swap)
        self._calibration_engine.update_profile(channel_id, profile)
        QMessageBox.information(
            self,
            "Calibration Applied",
            f"Calibration profile applied to channel '{channel_id}'.",
        )

    def _build_profile(self) -> Optional[CalibrationProfile]:
        """Build a CalibrationProfile from the current UI inputs.

        Returns:
            A CalibrationProfile if inputs are valid, None otherwise.
        """
        unit_label = self._unit_input.text().strip()
        if not unit_label:
            QMessageBox.warning(
                self, "Validation Error", "Unit label must not be empty."
            )
            return None

        min_v = self._min_voltage.value()
        max_v = self._max_voltage.value()

        if min_v >= max_v:
            QMessageBox.warning(
                self,
                "Validation Error",
                "Min valid voltage must be less than max valid voltage.",
            )
            return None

        type_index = self._type_selector.currentIndex()

        if type_index == 0:
            # Linear
            cal_type = CalibrationType.LINEAR
            linear_params = LinearCalibrationParams(
                slope=self._slope_input.value(),
                offset=self._offset_input.value(),
            )
            return CalibrationProfile(
                calibration_type=cal_type,
                unit_label=unit_label,
                min_valid_voltage=min_v,
                max_valid_voltage=max_v,
                linear_params=linear_params,
            )
        else:
            # Lookup Table
            cal_type = CalibrationType.LOOKUP_TABLE
            points = self._read_lookup_points()
            if points is None:
                return None  # Error already shown

            lookup_params = LookupTableParams(points=points)
            return CalibrationProfile(
                calibration_type=cal_type,
                unit_label=unit_label,
                min_valid_voltage=min_v,
                max_valid_voltage=max_v,
                lookup_params=lookup_params,
            )

    def _read_lookup_points(self) -> Optional[list[tuple[float, float]]]:
        """Read voltage-to-unit point pairs from the lookup table widget.

        Returns:
            List of (voltage, unit_value) tuples, or None if parsing fails.
        """
        points: list[tuple[float, float]] = []
        for row in range(self._lookup_table.rowCount()):
            voltage_item = self._lookup_table.item(row, 0)
            value_item = self._lookup_table.item(row, 1)

            if voltage_item is None or value_item is None:
                QMessageBox.warning(
                    self,
                    "Validation Error",
                    f"Row {row + 1} has empty cells.",
                )
                return None

            try:
                voltage = float(voltage_item.text())
            except ValueError:
                QMessageBox.warning(
                    self,
                    "Validation Error",
                    f"Row {row + 1}: invalid voltage value '{voltage_item.text()}'.",
                )
                return None

            try:
                value = float(value_item.text())
            except ValueError:
                QMessageBox.warning(
                    self,
                    "Validation Error",
                    f"Row {row + 1}: invalid unit value '{value_item.text()}'.",
                )
                return None

            points.append((voltage, value))

        return points

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
    def type_selector(self) -> QComboBox:
        """Access the calibration type selector (for testing)."""
        return self._type_selector

    @property
    def unit_input(self) -> QLineEdit:
        """Access the unit label input (for testing)."""
        return self._unit_input

    @property
    def slope_input(self) -> QDoubleSpinBox:
        """Access the slope input (for testing)."""
        return self._slope_input

    @property
    def offset_input(self) -> QDoubleSpinBox:
        """Access the offset input (for testing)."""
        return self._offset_input

    @property
    def min_voltage_input(self) -> QDoubleSpinBox:
        """Access the min voltage input (for testing)."""
        return self._min_voltage

    @property
    def max_voltage_input(self) -> QDoubleSpinBox:
        """Access the max voltage input (for testing)."""
        return self._max_voltage

    @property
    def lookup_table(self) -> QTableWidget:
        """Access the lookup table widget (for testing)."""
        return self._lookup_table

    @property
    def apply_button(self) -> QPushButton:
        """Access the apply button (for testing)."""
        return self._apply_button

    @property
    def calibration_engine(self) -> CalibrationEngine:
        """Access the calibration engine (for testing)."""
        return self._calibration_engine
