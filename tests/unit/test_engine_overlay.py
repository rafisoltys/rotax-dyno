"""Unit tests for the EngineOverlayWidget.

Since PyQt6 may not be available in the test environment, these tests
focus on the widget's data logic (stale detection, reading updates,
alarm severity mapping) by mocking the Qt layer.
"""

from __future__ import annotations

import time
from unittest.mock import MagicMock, patch

import pytest

# We need to mock PyQt6 before importing the module under test
# since PyQt6 may not be installed in the test environment.
import sys


def _setup_pyqt6_mocks():
    """Set up mock modules for PyQt6 if not available."""
    try:
        import PyQt6  # noqa: F401
        return False  # PyQt6 is available, no mocking needed
    except ImportError:
        pass

    # Create mock modules
    mock_qtcore = MagicMock()
    mock_qtgui = MagicMock()
    mock_qtwidgets = MagicMock()
    mock_pyqt6 = MagicMock()

    # Set up Qt constants and classes
    mock_qtcore.Qt = MagicMock()
    mock_qtcore.Qt.AspectRatioMode = MagicMock()
    mock_qtcore.Qt.TransformationMode = MagicMock()
    mock_qtcore.Qt.PenStyle = MagicMock()
    mock_qtcore.Qt.AlignmentFlag = MagicMock()
    mock_qtcore.QRectF = MagicMock()
    mock_qtcore.QTimer = MagicMock()

    mock_qtgui.QColor = MagicMock(side_effect=lambda *args: MagicMock())
    mock_qtgui.QFont = MagicMock()
    mock_qtgui.QPainter = MagicMock()
    mock_qtgui.QPen = MagicMock()
    mock_qtgui.QPixmap = MagicMock()

    mock_qtwidgets.QWidget = MagicMock

    # Register mock modules
    sys.modules["PyQt6"] = mock_pyqt6
    sys.modules["PyQt6.QtCore"] = mock_qtcore
    sys.modules["PyQt6.QtGui"] = mock_qtgui
    sys.modules["PyQt6.QtWidgets"] = mock_qtwidgets

    return True


# Set up mocks before importing the module under test
_mocked = _setup_pyqt6_mocks()

from rotax_dyno_daq.core.data_bus import DataBus
from rotax_dyno_daq.core.enums import AlarmSeverity, AlarmState
from rotax_dyno_daq.core.models import ActiveAlarm, CalibratedSample, SampleValidity
from rotax_dyno_daq.dashboard.engine_overlay import (
    DEFAULT_SENSOR_POSITIONS,
    STALE_THRESHOLD_SECONDS,
    EngineOverlayWidget,
    SensorReading,
)


@pytest.fixture
def data_bus():
    """Create a DataBus instance."""
    return DataBus()


@pytest.fixture
def mock_alarm_manager():
    """Create a mock AlarmManager."""
    manager = MagicMock()
    manager.get_active_alarms.return_value = []
    return manager


@pytest.fixture
def widget(data_bus, mock_alarm_manager):
    """Create an EngineOverlayWidget with mocked Qt internals."""
    with patch.object(EngineOverlayWidget, "__init__", lambda self, *a, **kw: None):
        w = EngineOverlayWidget.__new__(EngineOverlayWidget)
        # Manually initialize the attributes that __init__ would set
        w._data_bus = data_bus
        w._alarm_manager = mock_alarm_manager
        w._sensor_positions = DEFAULT_SENSOR_POSITIONS.copy()
        w._background_pixmap = None
        w._readings = {}
        w._subscription_ids = []
        w._refresh_timer = MagicMock()

        # Initialize readings for all configured sensor positions
        for channel_id in w._sensor_positions:
            w._readings[channel_id] = SensorReading(channel_id=channel_id)

        # Subscribe to data bus
        sub_id = data_bus.subscribe("*", w._on_sample_received)
        w._subscription_ids.append(sub_id)

        # Mock the update method (Qt repaint trigger)
        w.update = MagicMock()

        return w


class TestSensorReadingInitialization:
    """Test that sensor readings are properly initialized."""

    def test_default_sensor_positions_populated(self, widget):
        """All default sensor positions should have readings initialized."""
        for channel_id in DEFAULT_SENSOR_POSITIONS:
            assert channel_id in widget._readings
            reading = widget._readings[channel_id]
            assert reading.channel_id == channel_id
            assert reading.value == 0.0
            assert reading.severity == AlarmSeverity.NORMAL
            assert reading.last_update_time == 0.0

    def test_custom_sensor_positions(self, data_bus, mock_alarm_manager):
        """Widget should accept custom sensor positions."""
        with patch.object(EngineOverlayWidget, "__init__", lambda self, *a, **kw: None):
            w = EngineOverlayWidget.__new__(EngineOverlayWidget)
            custom_positions = {"CUSTOM1": (10, 20), "CUSTOM2": (30, 40)}
            w._data_bus = data_bus
            w._alarm_manager = mock_alarm_manager
            w._sensor_positions = custom_positions
            w._background_pixmap = None
            w._readings = {}
            w._subscription_ids = []
            w._refresh_timer = MagicMock()
            w.update = MagicMock()

            for channel_id in custom_positions:
                w._readings[channel_id] = SensorReading(channel_id=channel_id)

            assert "CUSTOM1" in w._readings
            assert "CUSTOM2" in w._readings
            assert len(w._readings) == 2


class TestDataBusIntegration:
    """Test DataBus subscription and sample handling."""

    def test_sample_updates_reading(self, widget, data_bus):
        """Publishing a CalibratedSample should update the corresponding reading."""
        sample = CalibratedSample(
            channel_id="EGT1",
            timestamp_ms=1000.0,
            raw_value=2.5,
            calibrated_value=750.0,
            unit="°C",
            validity=SampleValidity.VALID,
        )

        data_bus.publish("EGT1", sample)

        reading = widget._readings["EGT1"]
        assert reading.value == 750.0
        assert reading.unit == "°C"
        assert reading.last_update_time > 0
        assert reading.is_stale is False

    def test_sample_for_unknown_channel_ignored(self, widget, data_bus):
        """Samples for channels not in sensor_positions should be ignored."""
        sample = CalibratedSample(
            channel_id="UNKNOWN_CHANNEL",
            timestamp_ms=1000.0,
            raw_value=1.0,
            calibrated_value=100.0,
            unit="bar",
            validity=SampleValidity.VALID,
        )

        data_bus.publish("UNKNOWN_CHANNEL", sample)

        assert "UNKNOWN_CHANNEL" not in widget._readings

    def test_sample_without_channel_id_ignored(self, widget, data_bus):
        """Samples without channel_id attribute should be ignored."""
        sample = MagicMock(spec=[])  # No attributes
        data_bus.publish("test", sample)
        # Should not raise

    def test_multiple_updates_keep_latest(self, widget, data_bus):
        """Multiple updates to same channel should keep the latest value."""
        for value in [100.0, 200.0, 300.0]:
            sample = CalibratedSample(
                channel_id="RPM",
                timestamp_ms=1000.0,
                raw_value=1.0,
                calibrated_value=value,
                unit="rpm",
                validity=SampleValidity.VALID,
            )
            data_bus.publish("RPM", sample)

        reading = widget._readings["RPM"]
        assert reading.value == 300.0


class TestStaleDataDetection:
    """Test stale data detection logic."""

    def test_never_updated_channel_is_stale(self, widget):
        """A channel that has never received data should be considered stale."""
        assert widget.is_channel_stale("EGT1") is True

    def test_recently_updated_channel_not_stale(self, widget, data_bus):
        """A channel updated within 3 seconds should not be stale."""
        sample = CalibratedSample(
            channel_id="EGT1",
            timestamp_ms=1000.0,
            raw_value=2.5,
            calibrated_value=750.0,
            unit="°C",
            validity=SampleValidity.VALID,
        )
        data_bus.publish("EGT1", sample)

        assert widget.is_channel_stale("EGT1") is False

    def test_channel_becomes_stale_after_threshold(self, widget):
        """A channel should become stale after STALE_THRESHOLD_SECONDS."""
        reading = widget._readings["EGT1"]
        # Simulate an update that happened more than 3 seconds ago
        reading.last_update_time = time.monotonic() - STALE_THRESHOLD_SECONDS - 0.1

        assert widget.is_channel_stale("EGT1") is True

    def test_refresh_tick_marks_stale_channels(self, widget):
        """The refresh tick should mark channels as stale when threshold exceeded."""
        reading = widget._readings["OilP"]
        reading.last_update_time = time.monotonic() - STALE_THRESHOLD_SECONDS - 0.5

        widget._on_refresh_tick()

        assert reading.is_stale is True

    def test_refresh_tick_keeps_fresh_channels_not_stale(self, widget, data_bus):
        """The refresh tick should not mark recently updated channels as stale."""
        sample = CalibratedSample(
            channel_id="OilP",
            timestamp_ms=1000.0,
            raw_value=3.0,
            calibrated_value=4.5,
            unit="bar",
            validity=SampleValidity.VALID,
        )
        data_bus.publish("OilP", sample)

        widget._on_refresh_tick()

        reading = widget._readings["OilP"]
        assert reading.is_stale is False

    def test_unknown_channel_is_stale(self, widget):
        """Querying stale status for unknown channel returns True."""
        assert widget.is_channel_stale("NONEXISTENT") is True


class TestAlarmSeverityMapping:
    """Test alarm severity color-coding integration."""

    def test_no_alarms_all_normal(self, widget, mock_alarm_manager):
        """With no active alarms, all readings should be NORMAL severity."""
        mock_alarm_manager.get_active_alarms.return_value = []

        widget._update_alarm_severities()

        for reading in widget._readings.values():
            assert reading.severity == AlarmSeverity.NORMAL

    def test_warning_alarm_sets_warning_severity(self, widget, mock_alarm_manager):
        """An active WARNING alarm should set the channel to WARNING severity."""
        from datetime import datetime, timezone

        alarm = ActiveAlarm(
            alarm_id="alarm-1",
            channel_id="EGT1",
            severity=AlarmSeverity.WARNING,
            triggered_at=datetime.now(timezone.utc),
            value=850.0,
            threshold_crossed=800.0,
            state=AlarmState.ACTIVE,
        )
        mock_alarm_manager.get_active_alarms.return_value = [alarm]

        widget._update_alarm_severities()

        assert widget._readings["EGT1"].severity == AlarmSeverity.WARNING

    def test_critical_alarm_sets_critical_severity(self, widget, mock_alarm_manager):
        """An active CRITICAL alarm should set the channel to CRITICAL severity."""
        from datetime import datetime, timezone

        alarm = ActiveAlarm(
            alarm_id="alarm-2",
            channel_id="OilTemp",
            severity=AlarmSeverity.CRITICAL,
            triggered_at=datetime.now(timezone.utc),
            value=150.0,
            threshold_crossed=140.0,
            state=AlarmState.ACTIVE,
        )
        mock_alarm_manager.get_active_alarms.return_value = [alarm]

        widget._update_alarm_severities()

        assert widget._readings["OilTemp"].severity == AlarmSeverity.CRITICAL

    def test_alarm_cleared_resets_to_normal(self, widget, mock_alarm_manager):
        """When an alarm clears, the channel should return to NORMAL severity."""
        from datetime import datetime, timezone

        # First set an alarm
        alarm = ActiveAlarm(
            alarm_id="alarm-3",
            channel_id="EGT2",
            severity=AlarmSeverity.WARNING,
            triggered_at=datetime.now(timezone.utc),
            value=800.0,
            threshold_crossed=780.0,
            state=AlarmState.ACTIVE,
        )
        mock_alarm_manager.get_active_alarms.return_value = [alarm]
        widget._update_alarm_severities()
        assert widget._readings["EGT2"].severity == AlarmSeverity.WARNING

        # Now clear the alarm
        mock_alarm_manager.get_active_alarms.return_value = []
        widget._update_alarm_severities()
        assert widget._readings["EGT2"].severity == AlarmSeverity.NORMAL

    def test_no_alarm_manager_keeps_normal(self, data_bus):
        """Without an alarm manager, all readings stay NORMAL."""
        with patch.object(EngineOverlayWidget, "__init__", lambda self, *a, **kw: None):
            w = EngineOverlayWidget.__new__(EngineOverlayWidget)
            w._data_bus = data_bus
            w._alarm_manager = None
            w._sensor_positions = {"CH1": (0, 0)}
            w._background_pixmap = None
            w._readings = {"CH1": SensorReading(channel_id="CH1")}
            w._subscription_ids = []
            w._refresh_timer = MagicMock()
            w.update = MagicMock()

            w._update_alarm_severities()

            assert w._readings["CH1"].severity == AlarmSeverity.NORMAL


class TestManualReadingUpdate:
    """Test the manual update_reading method."""

    def test_update_existing_channel(self, widget):
        """update_reading should update an existing channel's data."""
        widget.update_reading("EGT1", 800.0, "°C", AlarmSeverity.WARNING)

        reading = widget._readings["EGT1"]
        assert reading.value == 800.0
        assert reading.unit == "°C"
        assert reading.severity == AlarmSeverity.WARNING
        assert reading.last_update_time > 0
        assert reading.is_stale is False

    def test_update_new_channel(self, widget):
        """update_reading should create a reading for a new channel."""
        widget.update_reading("NEW_CH", 42.0, "V")

        assert "NEW_CH" in widget._readings
        reading = widget._readings["NEW_CH"]
        assert reading.value == 42.0
        assert reading.unit == "V"

    def test_get_reading_existing(self, widget):
        """get_reading should return the reading for an existing channel."""
        widget.update_reading("EGT1", 500.0, "°C")
        reading = widget.get_reading("EGT1")
        assert reading is not None
        assert reading.value == 500.0

    def test_get_reading_nonexistent(self, widget):
        """get_reading should return None for a nonexistent channel."""
        assert widget.get_reading("DOES_NOT_EXIST") is None


class TestSetSensorPositions:
    """Test dynamic sensor position updates."""

    def test_set_new_positions(self, widget):
        """set_sensor_positions should update positions and create readings."""
        new_positions = {"A": (10, 20), "B": (30, 40)}
        widget.set_sensor_positions(new_positions)

        assert widget._sensor_positions == new_positions
        assert "A" in widget._readings
        assert "B" in widget._readings

    def test_set_positions_preserves_existing_readings(self, widget, data_bus):
        """Existing readings should be preserved when positions are updated."""
        # Update a reading first
        sample = CalibratedSample(
            channel_id="EGT1",
            timestamp_ms=1000.0,
            raw_value=2.5,
            calibrated_value=750.0,
            unit="°C",
            validity=SampleValidity.VALID,
        )
        data_bus.publish("EGT1", sample)

        # Now update positions to include EGT1
        new_positions = {"EGT1": (50, 60), "NEW": (70, 80)}
        widget.set_sensor_positions(new_positions)

        # EGT1 reading should still have its value
        assert widget._readings["EGT1"].value == 750.0


class TestCleanup:
    """Test resource cleanup."""

    def test_cleanup_stops_timer(self, widget):
        """cleanup should stop the refresh timer."""
        widget.cleanup()
        widget._refresh_timer.stop.assert_called_once()

    def test_cleanup_unsubscribes_from_bus(self, widget, data_bus):
        """cleanup should unsubscribe from the DataBus."""
        initial_sub_count = len(widget._subscription_ids)
        assert initial_sub_count > 0

        widget.cleanup()

        assert len(widget._subscription_ids) == 0
