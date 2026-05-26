"""Unit tests for AlarmIndicatorWidget."""

from __future__ import annotations

import sys
from datetime import datetime, timezone
from unittest.mock import patch, MagicMock

import pytest

from rotax_dyno_daq.alarms.manager import AlarmManager
from rotax_dyno_daq.core.enums import AlarmSeverity, AlarmState
from rotax_dyno_daq.core.models import ActiveAlarm, AlarmConfig, AlarmThreshold


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
def alarm_manager():
    """Create an AlarmManager without DataBus."""
    return AlarmManager()


@pytest.fixture
def sample_warning_alarm():
    """Create a sample WARNING alarm."""
    return ActiveAlarm(
        alarm_id="alarm-001",
        channel_id="EGT1",
        severity=AlarmSeverity.WARNING,
        triggered_at=datetime.now(timezone.utc),
        value=850.0,
        threshold_crossed=800.0,
        state=AlarmState.ACTIVE,
    )


@pytest.fixture
def sample_critical_alarm():
    """Create a sample CRITICAL alarm."""
    return ActiveAlarm(
        alarm_id="alarm-002",
        channel_id="OilTemp",
        severity=AlarmSeverity.CRITICAL,
        triggered_at=datetime.now(timezone.utc),
        value=150.0,
        threshold_crossed=140.0,
        state=AlarmState.ACTIVE,
    )


@pytest.fixture
def configured_alarm_manager(alarm_manager):
    """AlarmManager with thresholds configured for EGT1 and OilTemp."""
    alarm_manager.configure_threshold(
        "EGT1",
        AlarmConfig(
            channel_id="EGT1",
            thresholds=AlarmThreshold(high_warning=800.0, high_critical=900.0, deadband=10.0),
        ),
    )
    alarm_manager.configure_threshold(
        "OilTemp",
        AlarmConfig(
            channel_id="OilTemp",
            thresholds=AlarmThreshold(high_warning=120.0, high_critical=140.0, deadband=5.0),
        ),
    )
    return alarm_manager


@pytest.fixture
def qapp():
    """Create or get the QApplication instance."""
    from PyQt6.QtWidgets import QApplication

    app = QApplication.instance()
    if app is None:
        app = QApplication(sys.argv)
    return app


@pytest.fixture
def alarm_widget(qapp, configured_alarm_manager):
    """Create an AlarmIndicatorWidget for testing."""
    from rotax_dyno_daq.dashboard.alarm_widget import AlarmIndicatorWidget

    widget = AlarmIndicatorWidget(configured_alarm_manager)
    yield widget
    widget.cleanup()


class TestAlarmIndicatorWidget:
    """Tests for AlarmIndicatorWidget."""

    def test_initial_state_no_alarms(self, alarm_widget):
        """Widget starts with no alarm items displayed."""
        assert alarm_widget.get_active_alarm_count() == 0

    def test_alarm_appears_after_threshold_crossing(self, alarm_widget, configured_alarm_manager):
        """Alarm widget shows alarm after threshold is crossed and poll fires."""
        # Trigger a warning alarm
        configured_alarm_manager.evaluate("EGT1", 850.0)

        # Manually trigger poll
        alarm_widget._poll_alarms()

        assert alarm_widget.get_active_alarm_count() == 1

    def test_alarm_disappears_after_clearing(self, alarm_widget, configured_alarm_manager):
        """Alarm widget removes alarm after it clears via deadband."""
        # Trigger alarm
        configured_alarm_manager.evaluate("EGT1", 850.0)
        alarm_widget._poll_alarms()
        assert alarm_widget.get_active_alarm_count() == 1

        # Clear alarm (value below threshold - deadband: 800 - 10 = 790)
        configured_alarm_manager.evaluate("EGT1", 785.0)
        alarm_widget._poll_alarms()
        assert alarm_widget.get_active_alarm_count() == 0

    def test_acknowledge_button_calls_manager(self, alarm_widget, configured_alarm_manager):
        """Acknowledge button calls AlarmManager.acknowledge()."""
        # Trigger alarm
        configured_alarm_manager.evaluate("EGT1", 850.0)
        alarm_widget._poll_alarms()

        # Get the alarm ID
        alarms = configured_alarm_manager.get_active_alarms()
        assert len(alarms) == 1
        alarm_id = alarms[0].alarm_id

        # Click acknowledge
        alarm_widget._on_acknowledge(alarm_id)

        # Verify alarm is now acknowledged
        alarms = configured_alarm_manager.get_active_alarms()
        assert alarms[0].state == AlarmState.ACKNOWLEDGED

    def test_acknowledged_alarm_visual_remains(self, alarm_widget, configured_alarm_manager):
        """Acknowledged alarm still shows in widget (visual remains)."""
        configured_alarm_manager.evaluate("EGT1", 850.0)
        alarm_widget._poll_alarms()

        alarms = configured_alarm_manager.get_active_alarms()
        alarm_id = alarms[0].alarm_id
        alarm_widget._on_acknowledge(alarm_id)

        # Poll again - widget should still be there
        alarm_widget._poll_alarms()
        assert alarm_widget.get_active_alarm_count() == 1

    def test_multiple_alarms_displayed(self, alarm_widget, configured_alarm_manager):
        """Multiple alarms are displayed simultaneously."""
        configured_alarm_manager.evaluate("EGT1", 850.0)
        configured_alarm_manager.evaluate("OilTemp", 150.0)
        alarm_widget._poll_alarms()

        assert alarm_widget.get_active_alarm_count() == 2

    def test_status_label_updates(self, alarm_widget, configured_alarm_manager):
        """Status label shows correct alarm count."""
        assert "No active alarms" in alarm_widget._status_label.text()

        configured_alarm_manager.evaluate("EGT1", 850.0)
        alarm_widget._poll_alarms()
        assert "1 active alarm" in alarm_widget._status_label.text()

        configured_alarm_manager.evaluate("OilTemp", 150.0)
        alarm_widget._poll_alarms()
        assert "2 active alarms" in alarm_widget._status_label.text()

    def test_audible_enabled_by_default(self, alarm_widget):
        """Audible alerts are enabled by default."""
        assert alarm_widget.audible_enabled is True

    def test_set_audible_disabled(self, alarm_widget):
        """Can disable audible alerts globally."""
        alarm_widget.set_audible_enabled(False)
        assert alarm_widget.audible_enabled is False

    def test_acknowledge_nonexistent_alarm_no_error(self, alarm_widget):
        """Acknowledging a non-existent alarm does not raise."""
        # Should not raise
        alarm_widget._on_acknowledge("nonexistent-id")

    def test_poll_timer_interval(self, alarm_widget):
        """Poll timer is set to 100ms (10 Hz)."""
        assert alarm_widget._poll_timer.interval() == 100

    def test_alarm_widget_shows_correct_severity_colors(self, alarm_widget, configured_alarm_manager):
        """Warning alarms use amber, critical alarms use red styling."""
        from rotax_dyno_daq.dashboard.alarm_widget import AlarmItemWidget

        # Trigger warning
        configured_alarm_manager.evaluate("EGT1", 850.0)
        alarm_widget._poll_alarms()

        alarms = configured_alarm_manager.get_active_alarms()
        alarm_id = alarms[0].alarm_id
        item_widget = alarm_widget.get_alarm_widget(alarm_id)
        assert item_widget is not None
        assert item_widget.alarm.severity == AlarmSeverity.WARNING

    def test_alarm_widget_ack_button_disabled_after_acknowledge(
        self, alarm_widget, configured_alarm_manager
    ):
        """ACK button is disabled after alarm is acknowledged."""
        configured_alarm_manager.evaluate("EGT1", 850.0)
        alarm_widget._poll_alarms()

        alarms = configured_alarm_manager.get_active_alarms()
        alarm_id = alarms[0].alarm_id
        item_widget = alarm_widget.get_alarm_widget(alarm_id)

        # Initially enabled
        assert item_widget.ack_button.isEnabled()

        # Acknowledge
        alarm_widget._on_acknowledge(alarm_id)
        alarm_widget._poll_alarms()

        # Button should be disabled
        assert not item_widget.ack_button.isEnabled()
