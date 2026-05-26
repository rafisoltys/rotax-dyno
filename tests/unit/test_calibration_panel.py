"""Unit tests for CalibrationPanel widget.

Tests cover:
- Channel selector population
- Calibration type switching (Linear / Lookup Table)
- Linear parameter inputs (slope, offset)
- Lookup table row add/remove
- Touch target minimum sizes
- Profile building and validation
"""

from __future__ import annotations

import os
import sys

import pytest

# Set offscreen platform before importing any Qt modules
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication

from rotax_dyno_daq.calibration.engine import CalibrationEngine
from rotax_dyno_daq.core.enums import CalibrationType
from rotax_dyno_daq.dashboard.calibration_panel import (
    MIN_TOUCH_TARGET_PX,
    CalibrationPanel,
)


@pytest.fixture(scope="module")
def qapp():
    """Create a QApplication instance for the test module."""
    app = QApplication.instance()
    if app is None:
        app = QApplication(sys.argv)
    return app


@pytest.fixture
def calibration_engine():
    """Create a CalibrationEngine instance for testing."""
    return CalibrationEngine()


@pytest.fixture
def channel_ids():
    """Sample channel IDs for testing."""
    return ["EGT1", "EGT2", "OilP", "RPM", "AFR1"]


@pytest.fixture
def cal_panel(qapp, calibration_engine, channel_ids):
    """Create a CalibrationPanel instance for testing."""
    panel = CalibrationPanel(
        calibration_engine=calibration_engine,
        channel_ids=channel_ids,
    )
    yield panel
    panel.close()


class TestChannelSelector:
    """Tests for channel selector dropdown."""

    def test_channel_selector_populated(self, cal_panel: CalibrationPanel, channel_ids):
        """Channel selector should contain all provided channel IDs."""
        assert cal_panel.channel_selector.count() == len(channel_ids)
        for i, cid in enumerate(channel_ids):
            assert cal_panel.channel_selector.itemText(i) == cid

    def test_channel_selector_minimum_height(self, cal_panel: CalibrationPanel):
        """Channel selector should meet minimum touch target height."""
        assert cal_panel.channel_selector.minimumHeight() >= MIN_TOUCH_TARGET_PX

    def test_set_channel_ids(self, cal_panel: CalibrationPanel):
        """set_channel_ids should update the selector contents."""
        new_ids = ["CH1", "CH2"]
        cal_panel.set_channel_ids(new_ids)
        assert cal_panel.channel_selector.count() == 2
        assert cal_panel.channel_selector.itemText(0) == "CH1"
        assert cal_panel.channel_selector.itemText(1) == "CH2"


class TestCalibrationType:
    """Tests for calibration type switching."""

    def test_default_type_is_linear(self, cal_panel: CalibrationPanel):
        """Default calibration type should be Linear (index 0)."""
        assert cal_panel.type_selector.currentIndex() == 0

    def test_type_selector_has_two_options(self, cal_panel: CalibrationPanel):
        """Type selector should have Linear and Lookup Table options."""
        assert cal_panel.type_selector.count() == 2
        assert cal_panel.type_selector.itemText(0) == "Linear"
        assert cal_panel.type_selector.itemText(1) == "Lookup Table"

    def test_type_selector_minimum_height(self, cal_panel: CalibrationPanel):
        """Type selector should meet minimum touch target height."""
        assert cal_panel.type_selector.minimumHeight() >= MIN_TOUCH_TARGET_PX


class TestLinearParameters:
    """Tests for linear calibration parameter inputs."""

    def test_slope_default_value(self, cal_panel: CalibrationPanel):
        """Slope should default to 1.0."""
        assert cal_panel.slope_input.value() == 1.0

    def test_offset_default_value(self, cal_panel: CalibrationPanel):
        """Offset should default to 0.0."""
        assert cal_panel.offset_input.value() == 0.0

    def test_slope_minimum_height(self, cal_panel: CalibrationPanel):
        """Slope input should meet minimum touch target height."""
        assert cal_panel.slope_input.minimumHeight() >= MIN_TOUCH_TARGET_PX

    def test_offset_minimum_height(self, cal_panel: CalibrationPanel):
        """Offset input should meet minimum touch target height."""
        assert cal_panel.offset_input.minimumHeight() >= MIN_TOUCH_TARGET_PX


class TestLookupTable:
    """Tests for lookup table calibration parameters."""

    def test_lookup_table_initial_rows(self, cal_panel: CalibrationPanel):
        """Lookup table should start with 2 rows."""
        assert cal_panel.lookup_table.rowCount() == 2

    def test_lookup_table_columns(self, cal_panel: CalibrationPanel):
        """Lookup table should have 2 columns (Voltage, Unit Value)."""
        assert cal_panel.lookup_table.columnCount() == 2

    def test_add_row(self, cal_panel: CalibrationPanel):
        """Adding a row should increase the table row count."""
        initial_count = cal_panel.lookup_table.rowCount()
        cal_panel._on_add_row()
        assert cal_panel.lookup_table.rowCount() == initial_count + 1

    def test_remove_row(self, cal_panel: CalibrationPanel):
        """Removing a row should decrease the table row count."""
        cal_panel._on_add_row()  # Ensure we have at least 3 rows
        count_before = cal_panel.lookup_table.rowCount()
        cal_panel._on_remove_row()
        assert cal_panel.lookup_table.rowCount() == count_before - 1


class TestVoltageRange:
    """Tests for min/max valid voltage inputs."""

    def test_min_voltage_default(self, cal_panel: CalibrationPanel):
        """Min voltage should default to 0.0."""
        assert cal_panel.min_voltage_input.value() == 0.0

    def test_max_voltage_default(self, cal_panel: CalibrationPanel):
        """Max voltage should default to 5.0."""
        assert cal_panel.max_voltage_input.value() == 5.0

    def test_min_voltage_minimum_height(self, cal_panel: CalibrationPanel):
        """Min voltage input should meet minimum touch target height."""
        assert cal_panel.min_voltage_input.minimumHeight() >= MIN_TOUCH_TARGET_PX

    def test_max_voltage_minimum_height(self, cal_panel: CalibrationPanel):
        """Max voltage input should meet minimum touch target height."""
        assert cal_panel.max_voltage_input.minimumHeight() >= MIN_TOUCH_TARGET_PX


class TestApplyButton:
    """Tests for the apply button."""

    def test_apply_button_minimum_size(self, cal_panel: CalibrationPanel):
        """Apply button should meet minimum touch target size."""
        assert cal_panel.apply_button.minimumWidth() >= MIN_TOUCH_TARGET_PX
        assert cal_panel.apply_button.minimumHeight() >= MIN_TOUCH_TARGET_PX

    def test_build_linear_profile(self, cal_panel: CalibrationPanel):
        """Building a linear profile should produce correct CalibrationProfile."""
        cal_panel.unit_input.setText("bar")
        cal_panel.slope_input.setValue(2.5)
        cal_panel.offset_input.setValue(-1.0)
        cal_panel.min_voltage_input.setValue(0.0)
        cal_panel.max_voltage_input.setValue(5.0)
        cal_panel.type_selector.setCurrentIndex(0)  # Linear

        profile = cal_panel._build_profile()
        assert profile is not None
        assert profile.calibration_type == CalibrationType.LINEAR
        assert profile.unit_label == "bar"
        assert profile.linear_params is not None
        assert profile.linear_params.slope == 2.5
        assert profile.linear_params.offset == -1.0
        assert profile.min_valid_voltage == 0.0
        assert profile.max_valid_voltage == 5.0
