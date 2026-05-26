"""Hardware discovery and channel binding panel.

Provides auto-detection of MCC DAQ HATs, live voltage preview,
channel-to-measurement assignment, inline calibration configuration,
sensor preset selection, and save/apply functionality.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Callable, Optional

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from rotax_dyno_daq.config.manager import ConfigurationManager
from rotax_dyno_daq.core.enums import CalibrationType, ChannelType
from rotax_dyno_daq.core.models import (
    CalibrationProfile,
    ChannelConfig,
    LinearCalibrationParams,
)

logger = logging.getLogger(__name__)

# Minimum touch target size in pixels (12mm × 12mm at 96 DPI ≈ 45×45 px)
MIN_TOUCH_TARGET_PX = 45

# Conditional daqhats import
DAQHATS_AVAILABLE = False
hat_list: Any = None
HatIDs: Any = None
_mcc118_class: Any = None
_mcc134_class: Any = None

try:
    from daqhats import hat_list as _hat_list  # type: ignore[import-not-found]
    from daqhats import HatIDs as _HatIDs  # type: ignore[import-not-found]
    from daqhats import mcc118 as _mcc118  # type: ignore[import-not-found]
    from daqhats import mcc134 as _mcc134  # type: ignore[import-not-found]

    hat_list = _hat_list
    HatIDs = _HatIDs
    _mcc118_class = _mcc118
    _mcc134_class = _mcc134
    DAQHATS_AVAILABLE = True
except ImportError:
    pass

# MCC HAT type IDs — use HatIDs enum values when available, fallback to known constants
if DAQHATS_AVAILABLE and HatIDs is not None:
    MCC_118_ID = int(HatIDs.MCC_118)
    MCC_134_ID = int(HatIDs.MCC_134)
else:
    MCC_118_ID = 0x0142  # 322
    MCC_134_ID = 0x0143  # 323

# Number of channels per HAT type
MCC_118_CHANNELS = 8
MCC_134_CHANNELS = 4

# Measurement type options for channel assignment
MEASUREMENT_TYPES = [
    "(unassigned)",
    "OilP",
    "ChargeP",
    "RPM",
    "AFR1",
    "AFR2",
    "AFR3",
    "AFR4",
    "EGT1",
    "EGT2",
    "EGT3",
    "EGT4",
    "CLT",
    "OilTemp",
    "IAT",
    "AUX",
]

# Mapping from measurement type to ChannelType enum
_MEASUREMENT_TO_CHANNEL_TYPE: dict[str, ChannelType] = {
    "OilP": ChannelType.PRESSURE,
    "ChargeP": ChannelType.PRESSURE,
    "RPM": ChannelType.RPM,
    "AFR1": ChannelType.AFR,
    "AFR2": ChannelType.AFR,
    "AFR3": ChannelType.AFR,
    "AFR4": ChannelType.AFR,
    "EGT1": ChannelType.THERMOCOUPLE,
    "EGT2": ChannelType.THERMOCOUPLE,
    "EGT3": ChannelType.THERMOCOUPLE,
    "EGT4": ChannelType.THERMOCOUPLE,
    "CLT": ChannelType.THERMOCOUPLE,
    "OilTemp": ChannelType.THERMOCOUPLE,
    "IAT": ChannelType.THERMOCOUPLE,
    "AUX": ChannelType.PRESSURE,
}

# Default sample rates per channel type
_DEFAULT_SAMPLE_RATES: dict[ChannelType, float] = {
    ChannelType.THERMOCOUPLE: 1.0,
    ChannelType.PRESSURE: 100.0,
    ChannelType.RPM: 100.0,
    ChannelType.AFR: 10.0,
}


# --- Sensor Presets ---

@dataclass
class SensorPreset:
    """A sensor preset that auto-fills slope, offset, and unit."""

    name: str
    slope: float
    offset: float
    unit: str


SENSOR_PRESETS: list[SensorPreset] = [
    SensorPreset(name="(custom)", slope=1.0, offset=0.0, unit="V"),
    SensorPreset(name="Custom Range...", slope=1.0, offset=0.0, unit="V"),
    SensorPreset(name="Bosch 0-10 bar", slope=2.5, offset=-1.25, unit="bar"),
    SensorPreset(name="Generic 0-5 bar", slope=1.25, offset=-0.625, unit="bar"),
    SensorPreset(name="Innovate LC-2 λ", slope=0.2, offset=0.5, unit="λ"),
    SensorPreset(name="VDO Oil Pressure", slope=2.5, offset=-1.25, unit="bar"),
    SensorPreset(name="Generic RPM", slope=1800.0, offset=0.0, unit="RPM"),
    SensorPreset(name="Raw Voltage", slope=1.0, offset=0.0, unit="V"),
]

SENSOR_PRESET_NAMES = [p.name for p in SENSOR_PRESETS]


@dataclass
class DetectedChannel:
    """A detected HAT channel with its metadata."""

    hat_type: str  # "MCC 118" or "MCC 134"
    hat_id: int
    address: int
    channel: int
    hat_instance: Any = None  # mcc118 or mcc134 instance for live reading


def _is_mcc118(hat_id: int) -> bool:
    """Check if a HAT ID corresponds to an MCC 118 board."""
    if hat_id == MCC_118_ID:
        return True
    # Fallback: check against known constant in case enum differs
    if hat_id == 0x0142 or hat_id == 322:
        return True
    return False


def _is_mcc134(hat_id: int) -> bool:
    """Check if a HAT ID corresponds to an MCC 134 board."""
    if hat_id == MCC_134_ID:
        return True
    # Fallback: check against known constant in case enum differs
    if hat_id == 0x0143 or hat_id == 323:
        return True
    return False


class CustomSensorDialog(QDialog):
    """Dialog for defining a custom sensor calibration via voltage-to-value mapping.

    The user specifies min/max voltage and min/max value, and the dialog
    calculates slope and offset automatically:
        slope = (max_value - min_value) / (max_voltage - min_voltage)
        offset = min_value - slope * min_voltage
    """

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Custom Sensor Range")
        self.setMinimumWidth(350)
        self._slope: float = 1.0
        self._offset: float = 0.0
        self._unit: str = "bar"
        self._setup_ui()

    def _setup_ui(self) -> None:
        """Set up the dialog layout with voltage/value fields."""
        layout = QVBoxLayout(self)
        layout.setSpacing(12)

        form_layout = QFormLayout()
        form_layout.setSpacing(10)

        # Min Voltage
        self._min_voltage_spin = QDoubleSpinBox()
        self._min_voltage_spin.setRange(-100.0, 100.0)
        self._min_voltage_spin.setDecimals(3)
        self._min_voltage_spin.setValue(0.0)
        self._min_voltage_spin.setSuffix(" V")
        self._min_voltage_spin.setMinimumHeight(MIN_TOUCH_TARGET_PX)
        form_layout.addRow("Min Voltage:", self._min_voltage_spin)

        # Max Voltage
        self._max_voltage_spin = QDoubleSpinBox()
        self._max_voltage_spin.setRange(-100.0, 100.0)
        self._max_voltage_spin.setDecimals(3)
        self._max_voltage_spin.setValue(5.0)
        self._max_voltage_spin.setSuffix(" V")
        self._max_voltage_spin.setMinimumHeight(MIN_TOUCH_TARGET_PX)
        form_layout.addRow("Max Voltage:", self._max_voltage_spin)

        # Min Value
        self._min_value_spin = QDoubleSpinBox()
        self._min_value_spin.setRange(-99999.0, 99999.0)
        self._min_value_spin.setDecimals(4)
        self._min_value_spin.setValue(0.0)
        self._min_value_spin.setMinimumHeight(MIN_TOUCH_TARGET_PX)
        form_layout.addRow("Min Value:", self._min_value_spin)

        # Max Value
        self._max_value_spin = QDoubleSpinBox()
        self._max_value_spin.setRange(-99999.0, 99999.0)
        self._max_value_spin.setDecimals(4)
        self._max_value_spin.setValue(100.0)
        self._max_value_spin.setMinimumHeight(MIN_TOUCH_TARGET_PX)
        form_layout.addRow("Max Value:", self._max_value_spin)

        # Unit
        self._unit_edit = QLineEdit("bar")
        self._unit_edit.setMinimumHeight(MIN_TOUCH_TARGET_PX)
        self._unit_edit.setPlaceholderText("e.g. bar, PSI, °C")
        form_layout.addRow("Unit:", self._unit_edit)

        layout.addLayout(form_layout)

        # Dialog buttons
        button_box = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        button_box.accepted.connect(self._validate_and_accept)
        button_box.rejected.connect(self.reject)

        # Ensure buttons meet touch target size
        for button in button_box.buttons():
            button.setMinimumSize(MIN_TOUCH_TARGET_PX, MIN_TOUCH_TARGET_PX)

        layout.addWidget(button_box)

    def _validate_and_accept(self) -> None:
        """Validate inputs, calculate slope/offset, and accept."""
        min_v = self._min_voltage_spin.value()
        max_v = self._max_voltage_spin.value()
        min_val = self._min_value_spin.value()
        max_val = self._max_value_spin.value()

        if abs(max_v - min_v) < 1e-9:
            QMessageBox.warning(
                self,
                "Validation Error",
                "Min Voltage and Max Voltage must be different.",
            )
            return

        unit = self._unit_edit.text().strip()
        if not unit:
            QMessageBox.warning(
                self, "Validation Error", "Unit must not be empty."
            )
            return

        # Calculate slope and offset
        self._slope = (max_val - min_val) / (max_v - min_v)
        self._offset = min_val - self._slope * min_v
        self._unit = unit
        self.accept()

    @property
    def slope(self) -> float:
        """Calculated slope value."""
        return self._slope

    @property
    def offset(self) -> float:
        """Calculated offset value."""
        return self._offset

    @property
    def unit(self) -> str:
        """User-specified unit string."""
        return self._unit


class HardwareSetupPanel(QWidget):
    """Hardware discovery and channel binding panel.

    Features:
    - "Scan Hardware" button that calls daqhats.hat_list() to find connected HATs
    - Table showing: HAT Type | Address | Channel | Live Voltage | Assigned To |
      Sensor Preset | Unit | Cal Type | Slope | Offset | Action
    - Each row has a live voltage reading updated at 2 Hz
    - "Assigned To" is a QComboBox with measurement types
    - "Sensor Preset" auto-fills slope/offset/unit for common sensors
    - "Save & Apply" button that writes config and triggers reader restart
    """

    def __init__(
        self,
        config_manager: Optional[ConfigurationManager] = None,
        parent: Optional[QWidget] = None,
    ) -> None:
        """Initialize the Hardware Setup panel.

        Args:
            config_manager: The configuration manager for saving channel assignments.
            parent: Optional parent widget.
        """
        super().__init__(parent)
        self._config_manager = config_manager
        self._detected_channels: list[DetectedChannel] = []
        self._hat_instances: dict[int, Any] = {}  # address -> hat instance
        self._live_reading_active = False

        # Callback invoked after Save & Apply succeeds — app.py sets this
        self._on_config_applied_callback: Optional[Callable[[], None]] = None

        self._setup_ui()
        self._setup_timer()

    @property
    def on_config_applied(self) -> Optional[Callable[[], None]]:
        """Callback invoked after configuration is saved and applied."""
        return self._on_config_applied_callback

    @on_config_applied.setter
    def on_config_applied(self, callback: Optional[Callable[[], None]]) -> None:
        """Set the callback invoked after configuration is saved and applied."""
        self._on_config_applied_callback = callback

    def _setup_ui(self) -> None:
        """Build the panel UI layout."""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(10)

        # Header section
        header_layout = QHBoxLayout()

        title_label = QLabel("Hardware Setup")
        title_font = title_label.font()
        title_font.setPointSize(16)
        title_font.setBold(True)
        title_label.setFont(title_font)
        header_layout.addWidget(title_label)

        header_layout.addStretch()

        # Scan button
        self._scan_btn = QPushButton("Scan Hardware")
        self._scan_btn.setMinimumSize(MIN_TOUCH_TARGET_PX * 3, MIN_TOUCH_TARGET_PX)
        self._scan_btn.clicked.connect(self._on_scan_clicked)
        header_layout.addWidget(self._scan_btn)

        layout.addLayout(header_layout)

        # CSV Directory chooser
        csv_dir_layout = QHBoxLayout()
        csv_dir_label = QLabel("CSV Directory:")
        csv_dir_label.setMinimumHeight(MIN_TOUCH_TARGET_PX)
        csv_dir_layout.addWidget(csv_dir_label)

        self._csv_dir_edit = QLineEdit()
        self._csv_dir_edit.setMinimumHeight(MIN_TOUCH_TARGET_PX)
        self._csv_dir_edit.setPlaceholderText("Path to CSV log directory")
        self._csv_dir_edit.setReadOnly(True)
        # Pre-fill from config if available
        if self._config_manager is not None:
            self._csv_dir_edit.setText(
                str(self._config_manager.config.csv_directory)
            )
        csv_dir_layout.addWidget(self._csv_dir_edit, stretch=1)

        self._csv_dir_browse_btn = QPushButton("Browse…")
        self._csv_dir_browse_btn.setMinimumSize(
            MIN_TOUCH_TARGET_PX * 2, MIN_TOUCH_TARGET_PX
        )
        self._csv_dir_browse_btn.clicked.connect(self._on_csv_dir_browse)
        csv_dir_layout.addWidget(self._csv_dir_browse_btn)

        layout.addLayout(csv_dir_layout)

        # Status label
        self._status_label = QLabel("")
        self._status_label.setStyleSheet("QLabel { color: #666; padding: 4px; }")
        layout.addWidget(self._status_label)

        if not DAQHATS_AVAILABLE:
            self._status_label.setText(
                "daqhats library not available — hardware detection requires Raspberry Pi"
            )
            self._status_label.setStyleSheet("QLabel { color: #c00; padding: 4px; }")

        # Channel table
        self._table = QTableWidget()
        self._table.setColumnCount(11)
        self._table.setHorizontalHeaderLabels([
            "HAT Type",
            "Address",
            "Channel",
            "Live Voltage",
            "Assigned To",
            "Sensor Preset",
            "Unit",
            "Cal Type",
            "Slope",
            "Offset",
            "Action",
        ])
        self._table.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.Stretch
        )
        self._table.verticalHeader().setVisible(False)
        self._table.setSelectionMode(QTableWidget.SelectionMode.NoSelection)
        self._table.setMinimumHeight(300)
        layout.addWidget(self._table)

        # Bottom action bar
        action_layout = QHBoxLayout()
        action_layout.addStretch()

        self._save_btn = QPushButton("Save && Apply")
        self._save_btn.setMinimumSize(MIN_TOUCH_TARGET_PX * 3, MIN_TOUCH_TARGET_PX)
        self._save_btn.setStyleSheet(
            "QPushButton { background-color: #2196F3; color: white; "
            "font-weight: bold; border-radius: 4px; padding: 8px 16px; }"
            "QPushButton:hover { background-color: #1976D2; }"
        )
        self._save_btn.clicked.connect(self._on_save_clicked)
        self._save_btn.setEnabled(False)
        action_layout.addWidget(self._save_btn)

        layout.addLayout(action_layout)

    def _setup_timer(self) -> None:
        """Set up the 500ms timer for live voltage readings."""
        self._read_timer = QTimer(self)
        self._read_timer.setInterval(500)  # 2 Hz refresh
        self._read_timer.timeout.connect(self._update_live_readings)

    def _on_scan_clicked(self) -> None:
        """Handle the Scan Hardware button click."""
        self._stop_live_readings()
        self._detected_channels.clear()
        self._close_hat_instances()

        if not DAQHATS_AVAILABLE:
            self._status_label.setText(
                "daqhats library not available — hardware detection requires Raspberry Pi"
            )
            self._status_label.setStyleSheet("QLabel { color: #c00; padding: 4px; }")
            return

        try:
            # Use filter_by_id=0 to get ALL HATs (no filtering)
            hats = hat_list(filter_by_id=0)
            logger.info("hat_list(filter_by_id=0) returned %d HAT(s)", len(hats))
        except Exception as e:
            logger.error("Failed to scan for HATs: %s", e)
            self._status_label.setText(f"Scan failed: {e}")
            self._status_label.setStyleSheet("QLabel { color: #c00; padding: 4px; }")
            return

        if not hats:
            self._status_label.setText("No HATs detected")
            self._status_label.setStyleSheet("QLabel { color: #666; padding: 4px; }")
            self._table.setRowCount(0)
            self._save_btn.setEnabled(False)
            return

        # Build status message
        found_parts: list[str] = []
        for hat_info in hats:
            hat_id = hat_info.id
            address = hat_info.address

            # Log detailed info for debugging detection issues
            id_string = getattr(hat_info, "product_name", None) or getattr(hat_info, "id_string", "")
            logger.info(
                "Detected HAT: id=0x%04X (%d), address=%d, product=%s",
                hat_id, hat_id, address, id_string,
            )

            if _is_mcc118(hat_id):
                hat_type_name = "MCC 118"
                num_channels = MCC_118_CHANNELS
                try:
                    instance = _mcc118_class(address)
                    self._hat_instances[address] = instance
                except Exception as e:
                    logger.error("Failed to open MCC 118 at address %d: %s", address, e)
                    continue
            elif _is_mcc134(hat_id):
                hat_type_name = "MCC 134"
                num_channels = MCC_134_CHANNELS
                try:
                    instance = _mcc134_class(address)
                    self._hat_instances[address] = instance
                except Exception as e:
                    logger.error("Failed to open MCC 134 at address %d: %s", address, e)
                    continue
            else:
                logger.warning(
                    "Skipping unknown HAT type: id=0x%04X (%d) at address %d",
                    hat_id, hat_id, address,
                )
                continue  # Skip unknown HAT types

            found_parts.append(f"{hat_type_name} at address {address}")

            for ch in range(num_channels):
                self._detected_channels.append(
                    DetectedChannel(
                        hat_type=hat_type_name,
                        hat_id=hat_id,
                        address=address,
                        channel=ch,
                        hat_instance=self._hat_instances.get(address),
                    )
                )

        status_text = f"Found: {', '.join(found_parts)}" if found_parts else "No supported HATs detected"
        self._status_label.setText(status_text)
        self._status_label.setStyleSheet("QLabel { color: #090; padding: 4px; }")

        self._populate_table()
        self._save_btn.setEnabled(len(self._detected_channels) > 0)
        self._start_live_readings()

    def _populate_table(self) -> None:
        """Populate the table with detected channels."""
        self._table.setRowCount(len(self._detected_channels))

        for row, det_ch in enumerate(self._detected_channels):
            # HAT Type (read-only)
            type_item = QTableWidgetItem(det_ch.hat_type)
            type_item.setFlags(type_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            self._table.setItem(row, 0, type_item)

            # Address (read-only)
            addr_item = QTableWidgetItem(str(det_ch.address))
            addr_item.setFlags(addr_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            self._table.setItem(row, 1, addr_item)

            # Channel (read-only)
            ch_item = QTableWidgetItem(str(det_ch.channel))
            ch_item.setFlags(ch_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            self._table.setItem(row, 2, ch_item)

            # Live Voltage (read-only, updated by timer)
            voltage_item = QTableWidgetItem("---")
            voltage_item.setFlags(voltage_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            self._table.setItem(row, 3, voltage_item)

            # Assigned To (dropdown)
            combo = QComboBox()
            combo.addItems(MEASUREMENT_TYPES)
            combo.setMinimumHeight(MIN_TOUCH_TARGET_PX)
            self._table.setCellWidget(row, 4, combo)

            # Sensor Preset (dropdown) — auto-fills slope/offset/unit
            preset_combo = QComboBox()
            preset_combo.addItems(SENSOR_PRESET_NAMES)
            preset_combo.setMinimumHeight(MIN_TOUCH_TARGET_PX)
            preset_combo.currentIndexChanged.connect(
                lambda index, r=row: self._on_preset_changed(r, index)
            )
            self._table.setCellWidget(row, 5, preset_combo)

            # Unit
            unit_edit = QLineEdit("V")
            unit_edit.setMinimumHeight(MIN_TOUCH_TARGET_PX)
            unit_edit.setPlaceholderText("Unit")
            self._table.setCellWidget(row, 6, unit_edit)

            # Calibration Type
            cal_combo = QComboBox()
            cal_combo.addItems(["linear", "lookup_table"])
            cal_combo.setMinimumHeight(MIN_TOUCH_TARGET_PX)
            self._table.setCellWidget(row, 7, cal_combo)

            # Slope
            slope_spin = QDoubleSpinBox()
            slope_spin.setRange(-99999.0, 99999.0)
            slope_spin.setDecimals(4)
            slope_spin.setValue(1.0)
            slope_spin.setMinimumHeight(MIN_TOUCH_TARGET_PX)
            self._table.setCellWidget(row, 8, slope_spin)

            # Offset
            offset_spin = QDoubleSpinBox()
            offset_spin.setRange(-99999.0, 99999.0)
            offset_spin.setDecimals(4)
            offset_spin.setValue(0.0)
            offset_spin.setMinimumHeight(MIN_TOUCH_TARGET_PX)
            self._table.setCellWidget(row, 9, offset_spin)

            # Action — clear assignment button
            clear_btn = QPushButton("Clear")
            clear_btn.setMinimumSize(MIN_TOUCH_TARGET_PX, MIN_TOUCH_TARGET_PX)
            clear_btn.clicked.connect(lambda checked, r=row: self._clear_row(r))
            self._table.setCellWidget(row, 10, clear_btn)

        # Set row heights for touch targets
        for row in range(self._table.rowCount()):
            self._table.setRowHeight(row, MIN_TOUCH_TARGET_PX)

    def _on_preset_changed(self, row: int, index: int) -> None:
        """Handle sensor preset selection — auto-fill slope/offset/unit.

        Args:
            row: The table row that changed.
            index: The selected preset index.
        """
        if index < 0 or index >= len(SENSOR_PRESETS):
            return

        preset = SENSOR_PRESETS[index]

        # Skip auto-fill for "(custom)" — user manages values manually
        if preset.name == "(custom)":
            return

        # Show custom range dialog for "Custom Range..."
        if preset.name == "Custom Range...":
            dialog = CustomSensorDialog(self)
            if dialog.exec() == QDialog.DialogCode.Accepted:
                # Fill calculated slope/offset/unit into the row
                slope_spin = self._table.cellWidget(row, 8)
                if isinstance(slope_spin, QDoubleSpinBox):
                    slope_spin.setValue(dialog.slope)

                offset_spin = self._table.cellWidget(row, 9)
                if isinstance(offset_spin, QDoubleSpinBox):
                    offset_spin.setValue(dialog.offset)

                unit_edit = self._table.cellWidget(row, 6)
                if isinstance(unit_edit, QLineEdit):
                    unit_edit.setText(dialog.unit)
            else:
                # User cancelled — revert to "(custom)" preset
                preset_combo = self._table.cellWidget(row, 5)
                if isinstance(preset_combo, QComboBox):
                    preset_combo.blockSignals(True)
                    preset_combo.setCurrentIndex(0)
                    preset_combo.blockSignals(False)
            return

        # Auto-fill slope
        slope_spin = self._table.cellWidget(row, 8)
        if isinstance(slope_spin, QDoubleSpinBox):
            slope_spin.setValue(preset.slope)

        # Auto-fill offset
        offset_spin = self._table.cellWidget(row, 9)
        if isinstance(offset_spin, QDoubleSpinBox):
            offset_spin.setValue(preset.offset)

        # Auto-fill unit
        unit_edit = self._table.cellWidget(row, 6)
        if isinstance(unit_edit, QLineEdit):
            unit_edit.setText(preset.unit)

    def _clear_row(self, row: int) -> None:
        """Reset a row's assignment to unassigned defaults."""
        combo = self._table.cellWidget(row, 4)
        if isinstance(combo, QComboBox):
            combo.setCurrentIndex(0)

        preset_combo = self._table.cellWidget(row, 5)
        if isinstance(preset_combo, QComboBox):
            preset_combo.setCurrentIndex(0)

        unit_edit = self._table.cellWidget(row, 6)
        if isinstance(unit_edit, QLineEdit):
            unit_edit.setText("V")

        cal_combo = self._table.cellWidget(row, 7)
        if isinstance(cal_combo, QComboBox):
            cal_combo.setCurrentIndex(0)

        slope_spin = self._table.cellWidget(row, 8)
        if isinstance(slope_spin, QDoubleSpinBox):
            slope_spin.setValue(1.0)

        offset_spin = self._table.cellWidget(row, 9)
        if isinstance(offset_spin, QDoubleSpinBox):
            offset_spin.setValue(0.0)

    def _start_live_readings(self) -> None:
        """Start the live voltage reading timer."""
        if self._detected_channels and DAQHATS_AVAILABLE:
            self._live_reading_active = True
            self._read_timer.start()

    def _stop_live_readings(self) -> None:
        """Stop the live voltage reading timer."""
        self._live_reading_active = False
        self._read_timer.stop()

    def _update_live_readings(self) -> None:
        """Read live voltages from all detected channels and update the table."""
        if not self._live_reading_active:
            return

        for row, det_ch in enumerate(self._detected_channels):
            voltage_item = self._table.item(row, 3)
            if voltage_item is None:
                continue

            try:
                if det_ch.hat_instance is None:
                    voltage_item.setText("N/A")
                    continue

                if _is_mcc118(det_ch.hat_id):
                    value = det_ch.hat_instance.a_in_read(det_ch.channel)
                elif _is_mcc134(det_ch.hat_id):
                    value = det_ch.hat_instance.t_in_read(det_ch.channel)
                else:
                    voltage_item.setText("N/A")
                    continue

                voltage_item.setText(f"{value:.4f}")
            except Exception as e:
                voltage_item.setText("ERR")
                logger.debug(
                    "Error reading HAT addr=%d ch=%d: %s",
                    det_ch.address,
                    det_ch.channel,
                    e,
                )

    def _on_save_clicked(self) -> None:
        """Handle Save & Apply button click — build config, save, and restart readers."""
        if self._config_manager is None:
            QMessageBox.warning(
                self,
                "No Configuration Manager",
                "Cannot save: no configuration manager is available.",
            )
            return

        channels: list[ChannelConfig] = []

        for row, det_ch in enumerate(self._detected_channels):
            # Get assignment
            combo = self._table.cellWidget(row, 4)
            if not isinstance(combo, QComboBox):
                continue
            measurement = combo.currentText()
            if measurement == "(unassigned)":
                continue

            # Get unit
            unit_edit = self._table.cellWidget(row, 6)
            unit = unit_edit.text() if isinstance(unit_edit, QLineEdit) else "V"

            # Get calibration type
            cal_combo = self._table.cellWidget(row, 7)
            cal_type_str = cal_combo.currentText() if isinstance(cal_combo, QComboBox) else "linear"
            cal_type = CalibrationType(cal_type_str)

            # Get slope/offset
            slope_spin = self._table.cellWidget(row, 8)
            slope = slope_spin.value() if isinstance(slope_spin, QDoubleSpinBox) else 1.0

            offset_spin = self._table.cellWidget(row, 9)
            offset = offset_spin.value() if isinstance(offset_spin, QDoubleSpinBox) else 0.0

            # Determine channel type from measurement
            channel_type = _MEASUREMENT_TO_CHANNEL_TYPE.get(
                measurement, ChannelType.PRESSURE
            )

            # Determine sample rate
            sample_rate = _DEFAULT_SAMPLE_RATES.get(channel_type, 10.0)

            # Build calibration profile
            calibration = CalibrationProfile(
                calibration_type=cal_type,
                unit_label=unit,
                min_valid_voltage=0.0,
                max_valid_voltage=5.0,
                linear_params=LinearCalibrationParams(slope=slope, offset=offset),
            )

            # Build channel config
            channel_config = ChannelConfig(
                channel_id=measurement,
                channel_type=channel_type,
                hat_address=det_ch.address,
                hat_channel=det_ch.channel,
                sample_rate_hz=sample_rate,
                calibration=calibration,
                display_name=measurement,
                enabled=True,
            )
            channels.append(channel_config)

        if not channels:
            QMessageBox.information(
                self,
                "No Assignments",
                "No channels are assigned. Please assign at least one channel before saving.",
            )
            return

        # Update config and save
        try:
            self._config_manager.config.channels = channels

            # Update CSV directory if changed
            csv_dir_text = self._csv_dir_edit.text().strip()
            if csv_dir_text:
                from pathlib import Path as _Path

                self._config_manager.config.csv_directory = _Path(csv_dir_text)

            self._config_manager.save()
            self._status_label.setText(
                f"Configuration saved — {len(channels)} channel(s) configured."
            )
            self._status_label.setStyleSheet("QLabel { color: #090; padding: 4px; }")
            logger.info("Hardware setup saved %d channel(s) to config.", len(channels))
        except Exception as e:
            logger.error("Failed to save hardware configuration: %s", e)
            QMessageBox.critical(
                self,
                "Save Failed",
                f"Failed to save configuration:\n{e}",
            )
            return

        # Invoke the config-applied callback to restart readers
        if self._on_config_applied_callback is not None:
            try:
                self._on_config_applied_callback()
                logger.info("Config-applied callback executed successfully.")
            except Exception as e:
                logger.error("Config-applied callback failed: %s", e)
                QMessageBox.warning(
                    self,
                    "Restart Warning",
                    f"Configuration saved but reader restart failed:\n{e}",
                )

    def _on_csv_dir_browse(self) -> None:
        """Open a directory chooser for the CSV log directory."""
        current_dir = self._csv_dir_edit.text() or ""
        chosen_dir = QFileDialog.getExistingDirectory(
            self,
            "Select CSV Log Directory",
            current_dir,
            QFileDialog.Option.ShowDirsOnly,
        )
        if chosen_dir:
            self._csv_dir_edit.setText(chosen_dir)

    def _close_hat_instances(self) -> None:
        """Close any open HAT instances."""
        for address, instance in self._hat_instances.items():
            try:
                if hasattr(instance, "close"):
                    instance.close()
            except Exception as e:
                logger.debug("Error closing HAT at address %d: %s", address, e)
        self._hat_instances.clear()

    def closeEvent(self, event: Any) -> None:
        """Clean up resources when the panel is closed."""
        self._stop_live_readings()
        self._close_hat_instances()
        super().closeEvent(event)

    @property
    def detected_channels(self) -> list[DetectedChannel]:
        """The list of detected channels from the last scan."""
        return self._detected_channels

    @property
    def status_text(self) -> str:
        """The current status label text."""
        return self._status_label.text()
