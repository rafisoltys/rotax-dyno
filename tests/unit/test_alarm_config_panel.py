"""Unit tests for AlarmConfigPanel widget.

Tests cover:
- Channel selector population
- Threshold input enable/disable toggles
- Deadband input
- Enable/disable checkbox
- Touch target minimum sizes
- AlarmConfig building and validation
"""

from __future__ import annotations

import os
import sys

import pytest

# Set offscreen platform before importing any Qt modules
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication

from rotax_dyno_daq.alarms.manager import AlarmManager
from rotax_dyno_daq.core.models import AlarmConfig, AlarmThreshold
from rotax_dyno_daq.dashboard.alarm_config_panel import (
    MIN_TOUCH_TARGET_PX,
    AlarmConfigPanel,
)


@pytest.fixture(scope="module")
def qapp():
    """Create a QApplication instance for the test module."""
    app = QApplication.instance()
    if app is None:
        app = QApplication(sys.argv)
    return app


@pytest.fixture
def alarm_manager():
    """Create an AlarmManager instance for testing."""
    return AlarmManager()


@pytest.fixture
def channel_ids():
    """Sample channel IDs for testing."""
    return ["EGT1", "EGT2", "OilP", "RPM"]


@pytest.fixture
def alarm_panel(qapp, alarm_manager, channel_ids):
    """Create an AlarmConfigPanel instance for testing."""
    panel = AlarmConfigPanel(
        alarm_manager=alarm_manager,
        channel_ids=channel_ids,
    )
    yield panel
    panel.close()


class TestChannelSelector:
    """Tests for channel selector dropdown."""

    def test_channel_selector_populated(
        self, alarm_panel: AlarmConfigPanel, channel_ids
    ):
        """Channel selector should contain all provided channel IDs."""
        assert alarm_panel.channel_selector.count() == len(channel_ids)
        for i, cid in enumerate(channel_ids):
            assert alarm_panel.channel_selector.itemText(i) == cid

    def test_channel_selector_minimum_height(self, alarm_panel: AlarmConfigPanel):
        """Channel selector should meet minimum touch target height."""
        assert alarm_panel.channel_selector.minimumHeight() >= MIN_TOUCH_TARGET_PX

    def test_set_channel_ids(self, alarm_panel: AlarmConfigPanel):
        """set_channel_ids should update the selector contents."""
        new_ids = ["CH_A", "CH_B"]
        alarm_panel.set_channel_ids(new_ids)
        assert alarm_panel.channel_selector.count() == 2
        assert alarm_panel.channel_selector.itemText(0) == "CH_A"


class TestThresholdInputs:
    """Tests for threshold input spinboxes and enable toggles."""

    def test_high_warning_disabled_by_default(self, alarm_panel: AlarmConfigPanel):
        """High warning input should be disabled by default."""
        assert not alarm_panel.high_warning_input.isEnabled()

    def test_high_warning_enabled_on_check(self, alarm_panel: AlarmConfigPanel):
        """Checking high warning checkbox should enable the input."""
        alarm_panel.high_warning_enabled.setChecked(True)
        assert alarm_panel.high_warning_input.isEnabled()

    def test_high_critical_disabled_by_default(self, alarm_panel: AlarmConfigPanel):
        """High critical input should be disabled by default."""
        assert not alarm_panel.high_critical_input.isEnabled()

    def test_high_critical_enabled_on_check(self, alarm_panel: AlarmConfigPanel):
        """Checking high critical checkbox should enable the input."""
        alarm_panel.high_critical_enabled.setChecked(True)
        assert alarm_panel.high_critical_input.isEnabled()

    def test_low_warning_disabled_by_default(self, alarm_panel: AlarmConfigPanel):
        """Low warning input should be disabled by default."""
        assert not alarm_panel.low_warning_input.isEnabled()

    def test_low_warning_enabled_on_check(self, alarm_panel: AlarmConfigPanel):
        """Checking low warning checkbox should enable the input."""
        alarm_panel.low_warning_enabled.setChecked(True)
        assert alarm_panel.low_warning_input.isEnabled()

    def test_low_critical_disabled_by_default(self, alarm_panel: AlarmConfigPanel):
        """Low critical input should be disabled by default."""
        assert not alarm_panel.low_critical_input.isEnabled()

    def test_low_critical_enabled_on_check(self, alarm_panel: AlarmConfigPanel):
        """Checking low critical checkbox should enable the input."""
        alarm_panel.low_critical_enabled.setChecked(True)
        assert alarm_panel.low_critical_input.isEnabled()

    def test_threshold_inputs_minimum_height(self, alarm_panel: AlarmConfigPanel):
        """All threshold inputs should meet minimum touch target height."""
        assert alarm_panel.high_warning_input.minimumHeight() >= MIN_TOUCH_TARGET_PX
        assert alarm_panel.high_critical_input.minimumHeight() >= MIN_TOUCH_TARGET_PX
        assert alarm_panel.low_warning_input.minimumHeight() >= MIN_TOUCH_TARGET_PX
        assert alarm_panel.low_critical_input.minimumHeight() >= MIN_TOUCH_TARGET_PX


class TestDeadband:
    """Tests for deadband input."""

    def test_deadband_default_value(self, alarm_panel: AlarmConfigPanel):
        """Deadband should default to 0.0."""
        assert alarm_panel.deadband_input.value() == 0.0

    def test_deadband_minimum_height(self, alarm_panel: AlarmConfigPanel):
        """Deadband input should meet minimum touch target height."""
        assert alarm_panel.deadband_input.minimumHeight() >= MIN_TOUCH_TARGET_PX

    def test_deadband_non_negative_range(self, alarm_panel: AlarmConfigPanel):
        """Deadband minimum should be 0.0 (non-negative)."""
        assert alarm_panel.deadband_input.minimum() == 0.0


class TestEnabledCheckbox:
    """Tests for the enable/disable checkbox."""

    def test_enabled_by_default(self, alarm_panel: AlarmConfigPanel):
        """Enabled checkbox should be checked by default."""
        assert alarm_panel.enabled_checkbox.isChecked()

    def test_enabled_checkbox_minimum_size(self, alarm_panel: AlarmConfigPanel):
        """Enabled checkbox should meet minimum touch target size."""
        assert alarm_panel.enabled_checkbox.minimumWidth() >= MIN_TOUCH_TARGET_PX
        assert alarm_panel.enabled_checkbox.minimumHeight() >= MIN_TOUCH_TARGET_PX


class TestApplyButton:
    """Tests for the apply button."""

    def test_apply_button_minimum_size(self, alarm_panel: AlarmConfigPanel):
        """Apply button should meet minimum touch target size."""
        assert alarm_panel.apply_button.minimumWidth() >= MIN_TOUCH_TARGET_PX
        assert alarm_panel.apply_button.minimumHeight() >= MIN_TOUCH_TARGET_PX

    def test_build_alarm_config_no_thresholds(self, alarm_panel: AlarmConfigPanel):
        """Building config with no thresholds enabled should produce None values."""
        config = alarm_panel._build_alarm_config("EGT1")
        assert config is not None
        assert config.channel_id == "EGT1"
        assert config.thresholds.high_warning is None
        assert config.thresholds.high_critical is None
        assert config.thresholds.low_warning is None
        assert config.thresholds.low_critical is None
        assert config.thresholds.deadband == 0.0
        assert config.enabled is True

    def test_build_alarm_config_with_thresholds(self, alarm_panel: AlarmConfigPanel):
        """Building config with thresholds enabled should include values."""
        alarm_panel.high_warning_enabled.setChecked(True)
        alarm_panel.high_warning_input.setValue(80.0)
        alarm_panel.high_critical_enabled.setChecked(True)
        alarm_panel.high_critical_input.setValue(100.0)
        alarm_panel.deadband_input.setValue(2.5)

        config = alarm_panel._build_alarm_config("EGT1")
        assert config is not None
        assert config.thresholds.high_warning == 80.0
        assert config.thresholds.high_critical == 100.0
        assert config.thresholds.low_warning is None
        assert config.thresholds.low_critical is None
        assert config.thresholds.deadband == 2.5

    def test_build_alarm_config_disabled(self, alarm_panel: AlarmConfigPanel):
        """Building config with enabled unchecked should set enabled=False."""
        alarm_panel.enabled_checkbox.setChecked(False)
        config = alarm_panel._build_alarm_config("EGT1")
        assert config is not None
        assert config.enabled is False
