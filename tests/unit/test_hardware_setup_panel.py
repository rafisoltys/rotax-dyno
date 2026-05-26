"""Unit tests for the HardwareSetupPanel.

Mocks daqhats since it is not available on Windows/non-Pi environments.
"""

from __future__ import annotations

import sys
from unittest.mock import MagicMock, patch

import pytest


def _can_import_pyqt6():
    """Check if PyQt6 can be imported."""
    try:
        from PyQt6.QtWidgets import QApplication
        return True
    except ImportError:
        return False


# Skip all tests if PyQt6 is not available
pytestmark = pytest.mark.skipif(
    not _can_import_pyqt6(),
    reason="PyQt6 not available or no display",
)


@pytest.fixture
def qapp():
    """Create or get the QApplication instance."""
    from PyQt6.QtWidgets import QApplication

    app = QApplication.instance()
    if app is None:
        app = QApplication(sys.argv)
    return app


@pytest.fixture
def panel(qapp):
    """Create a HardwareSetupPanel instance for testing."""
    from rotax_dyno_daq.dashboard.hardware_setup_panel import HardwareSetupPanel

    widget = HardwareSetupPanel(config_manager=None)
    return widget


@pytest.fixture
def panel_with_config(qapp):
    """Create a HardwareSetupPanel with a mock config manager."""
    from rotax_dyno_daq.dashboard.hardware_setup_panel import HardwareSetupPanel

    mock_config_manager = MagicMock()
    mock_config_manager.config = MagicMock()
    mock_config_manager.config.channels = []
    widget = HardwareSetupPanel(config_manager=mock_config_manager)
    return widget, mock_config_manager


class TestHardwareSetupPanelInit:
    """Tests for panel initialization."""

    def test_panel_creates_without_error(self, panel):
        """Panel should instantiate without errors."""
        assert panel is not None

    def test_panel_has_scan_button(self, panel):
        """Panel should have a Scan Hardware button."""
        assert panel._scan_btn is not None
        assert panel._scan_btn.text() == "Scan Hardware"

    def test_panel_has_save_button(self, panel):
        """Panel should have a Save & Apply button."""
        assert panel._save_btn is not None
        assert "Save" in panel._save_btn.text()

    def test_save_button_initially_disabled(self, panel):
        """Save button should be disabled until channels are detected."""
        assert not panel._save_btn.isEnabled()

    def test_panel_has_status_label(self, panel):
        """Panel should have a status label."""
        assert panel._status_label is not None

    def test_panel_has_table(self, panel):
        """Panel should have a channel table."""
        assert panel._table is not None
        assert panel._table.columnCount() == 10

    def test_table_headers(self, panel):
        """Table should have correct column headers."""
        expected_headers = [
            "HAT Type",
            "Address",
            "Channel",
            "Live Voltage",
            "Assigned To",
            "Unit",
            "Cal Type",
            "Slope",
            "Offset",
            "Action",
        ]
        for col, expected in enumerate(expected_headers):
            header_item = panel._table.horizontalHeaderItem(col)
            assert header_item is not None
            assert header_item.text() == expected

    def test_daqhats_unavailable_message(self, panel):
        """When daqhats is not available, status should show appropriate message."""
        from rotax_dyno_daq.dashboard.hardware_setup_panel import DAQHATS_AVAILABLE

        if not DAQHATS_AVAILABLE:
            assert "daqhats library not available" in panel._status_label.text()


class TestHardwareSetupPanelScan:
    """Tests for the scan functionality with mocked daqhats."""

    def test_scan_no_hats_found(self, panel):
        """Scan with no HATs should show 'No HATs detected'."""
        with patch(
            "rotax_dyno_daq.dashboard.hardware_setup_panel.DAQHATS_AVAILABLE", True
        ), patch(
            "rotax_dyno_daq.dashboard.hardware_setup_panel.hat_list",
            return_value=[],
        ):
            panel._on_scan_clicked()

        assert "No HATs detected" in panel._status_label.text()
        assert panel._table.rowCount() == 0
        assert not panel._save_btn.isEnabled()

    def test_scan_finds_mcc118(self, panel):
        """Scan finding an MCC 118 should populate table with 8 channels."""
        from rotax_dyno_daq.dashboard.hardware_setup_panel import (
            MCC_118_CHANNELS,
            MCC_118_ID,
        )

        mock_hat_info = MagicMock()
        mock_hat_info.id = MCC_118_ID
        mock_hat_info.address = 0

        mock_mcc118_instance = MagicMock()
        mock_mcc118_instance.a_in_read = MagicMock(return_value=2.5)

        with patch(
            "rotax_dyno_daq.dashboard.hardware_setup_panel.DAQHATS_AVAILABLE", True
        ), patch(
            "rotax_dyno_daq.dashboard.hardware_setup_panel.hat_list",
            return_value=[mock_hat_info],
        ), patch(
            "rotax_dyno_daq.dashboard.hardware_setup_panel._mcc118_class",
            return_value=mock_mcc118_instance,
        ):
            panel._on_scan_clicked()

        assert panel._table.rowCount() == MCC_118_CHANNELS
        assert "MCC 118 at address 0" in panel._status_label.text()
        assert panel._save_btn.isEnabled()

    def test_scan_finds_mcc134(self, panel):
        """Scan finding an MCC 134 should populate table with 4 channels."""
        from rotax_dyno_daq.dashboard.hardware_setup_panel import (
            MCC_134_CHANNELS,
            MCC_134_ID,
        )

        mock_hat_info = MagicMock()
        mock_hat_info.id = MCC_134_ID
        mock_hat_info.address = 1

        mock_mcc134_instance = MagicMock()
        mock_mcc134_instance.t_in_read = MagicMock(return_value=25.0)

        with patch(
            "rotax_dyno_daq.dashboard.hardware_setup_panel.DAQHATS_AVAILABLE", True
        ), patch(
            "rotax_dyno_daq.dashboard.hardware_setup_panel.hat_list",
            return_value=[mock_hat_info],
        ), patch(
            "rotax_dyno_daq.dashboard.hardware_setup_panel._mcc134_class",
            return_value=mock_mcc134_instance,
        ):
            panel._on_scan_clicked()

        assert panel._table.rowCount() == MCC_134_CHANNELS
        assert "MCC 134 at address 1" in panel._status_label.text()
        assert panel._save_btn.isEnabled()

    def test_scan_finds_both_hats(self, panel):
        """Scan finding both HAT types should show all channels."""
        from rotax_dyno_daq.dashboard.hardware_setup_panel import (
            MCC_118_CHANNELS,
            MCC_118_ID,
            MCC_134_CHANNELS,
            MCC_134_ID,
        )

        mock_hat_118 = MagicMock()
        mock_hat_118.id = MCC_118_ID
        mock_hat_118.address = 0

        mock_hat_134 = MagicMock()
        mock_hat_134.id = MCC_134_ID
        mock_hat_134.address = 1

        mock_mcc118_instance = MagicMock()
        mock_mcc134_instance = MagicMock()

        def mock_mcc118_class(address):
            return mock_mcc118_instance

        def mock_mcc134_class(address):
            return mock_mcc134_instance

        with patch(
            "rotax_dyno_daq.dashboard.hardware_setup_panel.DAQHATS_AVAILABLE", True
        ), patch(
            "rotax_dyno_daq.dashboard.hardware_setup_panel.hat_list",
            return_value=[mock_hat_118, mock_hat_134],
        ), patch(
            "rotax_dyno_daq.dashboard.hardware_setup_panel._mcc118_class",
            side_effect=mock_mcc118_class,
        ), patch(
            "rotax_dyno_daq.dashboard.hardware_setup_panel._mcc134_class",
            side_effect=mock_mcc134_class,
        ):
            panel._on_scan_clicked()

        expected_rows = MCC_118_CHANNELS + MCC_134_CHANNELS
        assert panel._table.rowCount() == expected_rows
        assert "MCC 118" in panel._status_label.text()
        assert "MCC 134" in panel._status_label.text()


class TestHardwareSetupPanelTable:
    """Tests for table population and interaction."""

    def _setup_panel_with_channels(self, panel):
        """Helper to set up panel with mock detected channels."""
        from rotax_dyno_daq.dashboard.hardware_setup_panel import (
            DetectedChannel,
            MCC_118_ID,
            MCC_134_ID,
        )

        panel._detected_channels = [
            DetectedChannel(
                hat_type="MCC 118",
                hat_id=MCC_118_ID,
                address=0,
                channel=0,
                hat_instance=None,
            ),
            DetectedChannel(
                hat_type="MCC 134",
                hat_id=MCC_134_ID,
                address=1,
                channel=0,
                hat_instance=None,
            ),
        ]
        panel._populate_table()

    def test_table_has_measurement_dropdown(self, panel):
        """Each row should have a measurement type dropdown."""
        from PyQt6.QtWidgets import QComboBox
        from rotax_dyno_daq.dashboard.hardware_setup_panel import MEASUREMENT_TYPES

        self._setup_panel_with_channels(panel)

        combo = panel._table.cellWidget(0, 4)
        assert isinstance(combo, QComboBox)
        assert combo.count() == len(MEASUREMENT_TYPES)
        assert combo.itemText(0) == "(unassigned)"

    def test_table_has_unit_field(self, panel):
        """Each row should have a unit text field."""
        from PyQt6.QtWidgets import QLineEdit

        self._setup_panel_with_channels(panel)

        unit_edit = panel._table.cellWidget(0, 5)
        assert isinstance(unit_edit, QLineEdit)
        assert unit_edit.text() == "V"

    def test_table_has_calibration_type_dropdown(self, panel):
        """Each row should have a calibration type dropdown."""
        from PyQt6.QtWidgets import QComboBox

        self._setup_panel_with_channels(panel)

        cal_combo = panel._table.cellWidget(0, 6)
        assert isinstance(cal_combo, QComboBox)
        assert cal_combo.itemText(0) == "linear"
        assert cal_combo.itemText(1) == "lookup_table"

    def test_table_has_slope_spinbox(self, panel):
        """Each row should have a slope spinbox defaulting to 1.0."""
        from PyQt6.QtWidgets import QDoubleSpinBox

        self._setup_panel_with_channels(panel)

        slope_spin = panel._table.cellWidget(0, 7)
        assert isinstance(slope_spin, QDoubleSpinBox)
        assert slope_spin.value() == 1.0

    def test_table_has_offset_spinbox(self, panel):
        """Each row should have an offset spinbox defaulting to 0.0."""
        from PyQt6.QtWidgets import QDoubleSpinBox

        self._setup_panel_with_channels(panel)

        offset_spin = panel._table.cellWidget(0, 8)
        assert isinstance(offset_spin, QDoubleSpinBox)
        assert offset_spin.value() == 0.0

    def test_clear_row_resets_assignment(self, panel):
        """Clear button should reset row to unassigned defaults."""
        from PyQt6.QtWidgets import QComboBox, QDoubleSpinBox

        self._setup_panel_with_channels(panel)

        # Set some values
        combo = panel._table.cellWidget(0, 4)
        combo.setCurrentIndex(1)  # Set to "OilP"

        slope_spin = panel._table.cellWidget(0, 7)
        slope_spin.setValue(5.0)

        # Clear the row
        panel._clear_row(0)

        assert combo.currentText() == "(unassigned)"
        assert slope_spin.value() == 1.0

    def test_hat_type_column_shows_correct_type(self, panel):
        """HAT Type column should show the correct HAT type name."""
        self._setup_panel_with_channels(panel)

        type_item_0 = panel._table.item(0, 0)
        assert type_item_0.text() == "MCC 118"

        type_item_1 = panel._table.item(1, 0)
        assert type_item_1.text() == "MCC 134"


class TestHardwareSetupPanelSave:
    """Tests for save functionality."""

    def test_save_with_no_assignments_shows_message(self, panel_with_config):
        """Save with no assigned channels should show info message."""
        from rotax_dyno_daq.dashboard.hardware_setup_panel import (
            DetectedChannel,
            MCC_118_ID,
        )

        panel, mock_config = panel_with_config

        # Set up channels but leave all unassigned
        panel._detected_channels = [
            DetectedChannel(
                hat_type="MCC 118",
                hat_id=MCC_118_ID,
                address=0,
                channel=0,
                hat_instance=None,
            ),
        ]
        panel._populate_table()

        # Mock QMessageBox to avoid blocking
        with patch(
            "rotax_dyno_daq.dashboard.hardware_setup_panel.QMessageBox.information"
        ) as mock_msg:
            panel._on_save_clicked()
            mock_msg.assert_called_once()

        mock_config.save.assert_not_called()

    def test_save_with_assignments_calls_config_save(self, panel_with_config):
        """Save with assigned channels should call config_manager.save()."""
        from rotax_dyno_daq.dashboard.hardware_setup_panel import (
            DetectedChannel,
            MEASUREMENT_TYPES,
            MCC_118_ID,
        )

        panel, mock_config = panel_with_config

        panel._detected_channels = [
            DetectedChannel(
                hat_type="MCC 118",
                hat_id=MCC_118_ID,
                address=0,
                channel=0,
                hat_instance=None,
            ),
        ]
        panel._populate_table()

        # Assign channel to OilP
        combo = panel._table.cellWidget(0, 4)
        combo.setCurrentIndex(MEASUREMENT_TYPES.index("OilP"))

        panel._on_save_clicked()

        mock_config.save.assert_called_once()

    def test_save_builds_correct_channel_config(self, panel_with_config):
        """Save should build ChannelConfig with correct values from the table."""
        from rotax_dyno_daq.dashboard.hardware_setup_panel import (
            DetectedChannel,
            MEASUREMENT_TYPES,
            MCC_118_ID,
        )

        panel, mock_config = panel_with_config

        panel._detected_channels = [
            DetectedChannel(
                hat_type="MCC 118",
                hat_id=MCC_118_ID,
                address=0,
                channel=3,
                hat_instance=None,
            ),
        ]
        panel._populate_table()

        # Configure the row
        combo = panel._table.cellWidget(0, 4)
        combo.setCurrentIndex(MEASUREMENT_TYPES.index("RPM"))

        unit_edit = panel._table.cellWidget(0, 5)
        unit_edit.setText("rpm")

        slope_spin = panel._table.cellWidget(0, 7)
        slope_spin.setValue(1000.0)

        offset_spin = panel._table.cellWidget(0, 8)
        offset_spin.setValue(0.0)

        panel._on_save_clicked()

        # Verify the channel config was set
        channels = mock_config.config.channels
        assert len(channels) == 1
        ch = channels[0]
        assert ch.channel_id == "RPM"
        assert ch.hat_address == 0
        assert ch.hat_channel == 3
        assert ch.calibration.unit_label == "rpm"
        assert ch.calibration.linear_params.slope == 1000.0
        assert ch.calibration.linear_params.offset == 0.0

    def test_save_without_config_manager_shows_warning(self, panel):
        """Save without a config manager should show a warning."""
        from rotax_dyno_daq.dashboard.hardware_setup_panel import (
            DetectedChannel,
            MCC_118_ID,
        )

        panel._detected_channels = [
            DetectedChannel(
                hat_type="MCC 118",
                hat_id=MCC_118_ID,
                address=0,
                channel=0,
                hat_instance=None,
            ),
        ]
        panel._populate_table()

        combo = panel._table.cellWidget(0, 4)
        combo.setCurrentIndex(1)

        with patch(
            "rotax_dyno_daq.dashboard.hardware_setup_panel.QMessageBox.warning"
        ) as mock_msg:
            panel._on_save_clicked()
            mock_msg.assert_called_once()


class TestHardwareSetupPanelLiveReadings:
    """Tests for live voltage reading functionality."""

    def test_live_readings_start_after_scan(self, panel):
        """Live readings timer should start after a successful scan."""
        from rotax_dyno_daq.dashboard.hardware_setup_panel import MCC_118_ID

        mock_hat_info = MagicMock()
        mock_hat_info.id = MCC_118_ID
        mock_hat_info.address = 0

        mock_instance = MagicMock()
        mock_instance.a_in_read = MagicMock(return_value=1.234)

        with patch(
            "rotax_dyno_daq.dashboard.hardware_setup_panel.DAQHATS_AVAILABLE", True
        ), patch(
            "rotax_dyno_daq.dashboard.hardware_setup_panel.hat_list",
            return_value=[mock_hat_info],
        ), patch(
            "rotax_dyno_daq.dashboard.hardware_setup_panel._mcc118_class",
            return_value=mock_instance,
        ):
            panel._on_scan_clicked()

        assert panel._live_reading_active is True
        assert panel._read_timer.isActive()

    def test_update_live_readings_updates_voltage_column(self, panel):
        """Timer tick should update the voltage column with read values."""
        from rotax_dyno_daq.dashboard.hardware_setup_panel import (
            DetectedChannel,
            MCC_118_ID,
        )

        mock_instance = MagicMock()
        mock_instance.a_in_read = MagicMock(return_value=3.1415)

        panel._detected_channels = [
            DetectedChannel(
                hat_type="MCC 118",
                hat_id=MCC_118_ID,
                address=0,
                channel=0,
                hat_instance=mock_instance,
            ),
        ]
        panel._populate_table()
        panel._live_reading_active = True

        panel._update_live_readings()

        voltage_item = panel._table.item(0, 3)
        assert voltage_item.text() == "3.1415"

    def test_update_live_readings_handles_error(self, panel):
        """Timer tick should show ERR when reading fails."""
        from rotax_dyno_daq.dashboard.hardware_setup_panel import (
            DetectedChannel,
            MCC_118_ID,
        )

        mock_instance = MagicMock()
        mock_instance.a_in_read = MagicMock(side_effect=RuntimeError("read failed"))

        panel._detected_channels = [
            DetectedChannel(
                hat_type="MCC 118",
                hat_id=MCC_118_ID,
                address=0,
                channel=0,
                hat_instance=mock_instance,
            ),
        ]
        panel._populate_table()
        panel._live_reading_active = True

        panel._update_live_readings()

        voltage_item = panel._table.item(0, 3)
        assert voltage_item.text() == "ERR"

    def test_stop_live_readings(self, panel):
        """Stopping live readings should deactivate the timer."""
        panel._live_reading_active = True
        panel._read_timer.start()

        panel._stop_live_readings()

        assert panel._live_reading_active is False
        assert not panel._read_timer.isActive()


class TestHardwareSetupPanelTouchTargets:
    """Tests for minimum touch target compliance."""

    def test_scan_button_minimum_size(self, panel):
        """Scan button should meet minimum touch target size."""
        assert panel._scan_btn.minimumHeight() >= 45

    def test_save_button_minimum_size(self, panel):
        """Save button should meet minimum touch target size."""
        assert panel._save_btn.minimumHeight() >= 45

    def test_table_row_height(self, panel):
        """Table rows should meet minimum touch target height."""
        from rotax_dyno_daq.dashboard.hardware_setup_panel import (
            DetectedChannel,
            MCC_118_ID,
        )

        panel._detected_channels = [
            DetectedChannel(
                hat_type="MCC 118",
                hat_id=MCC_118_ID,
                address=0,
                channel=0,
                hat_instance=None,
            ),
        ]
        panel._populate_table()

        assert panel._table.rowHeight(0) >= 45
