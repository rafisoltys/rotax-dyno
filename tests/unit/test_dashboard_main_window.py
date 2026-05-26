"""Unit tests for DashboardWindow main window and navigation.

Tests cover:
- Tab creation and naming
- Recording indicator visibility and elapsed time formatting
- Touch target minimum sizes
- Connection status updates
"""

from __future__ import annotations

import os
import sys

import pytest

# Set offscreen platform before importing any Qt modules
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication

from rotax_dyno_daq.alarms.manager import AlarmManager
from rotax_dyno_daq.core.data_bus import DataBus
from rotax_dyno_daq.dashboard.main_window import MIN_TOUCH_TARGET_PX, DashboardWindow


@pytest.fixture(scope="module")
def qapp():
    """Create a QApplication instance for the test module."""
    app = QApplication.instance()
    if app is None:
        app = QApplication(sys.argv)
    return app


@pytest.fixture
def data_bus():
    """Create a DataBus instance for testing."""
    return DataBus()


@pytest.fixture
def alarm_manager(data_bus):
    """Create an AlarmManager instance for testing."""
    return AlarmManager(data_bus=data_bus)


@pytest.fixture
def dashboard(qapp, data_bus, alarm_manager):
    """Create a DashboardWindow instance for testing."""
    window = DashboardWindow(data_bus=data_bus, alarm_manager=alarm_manager)
    yield window
    window.close()


class TestTabNavigation:
    """Tests for tabbed navigation between views."""

    def test_has_five_tabs(self, dashboard: DashboardWindow):
        """DashboardWindow should have exactly 5 tabs."""
        assert dashboard.tab_widget.count() == 5

    def test_tab_names(self, dashboard: DashboardWindow):
        """Tabs should have the correct names in order."""
        expected_names = [
            "Engine Overlay",
            "Strip Charts",
            "Alarms",
            "Runs",
            "Post-Processing",
        ]
        actual_names = [
            dashboard.tab_widget.tabText(i)
            for i in range(dashboard.tab_widget.count())
        ]
        assert actual_names == expected_names

    def test_default_tab_is_engine_overlay(self, dashboard: DashboardWindow):
        """The first tab (Engine Overlay) should be selected by default."""
        assert dashboard.tab_widget.currentIndex() == 0

    def test_tab_bar_minimum_height(self, dashboard: DashboardWindow):
        """Tab bar should have minimum height for touch targets."""
        tab_bar = dashboard.tab_widget.tabBar()
        assert tab_bar.minimumHeight() >= MIN_TOUCH_TARGET_PX


class TestRecordingIndicator:
    """Tests for recording indicator and elapsed time display."""

    def test_recording_indicator_hidden_by_default(self, dashboard: DashboardWindow):
        """Recording indicator should be hidden when no run is active."""
        # Use isVisibleTo(parent) since the window itself may not be shown in offscreen mode
        assert not dashboard._rec_indicator.isVisibleTo(dashboard)
        assert not dashboard._elapsed_label.isVisibleTo(dashboard)

    def test_start_recording_shows_indicator(self, dashboard: DashboardWindow):
        """Starting recording should show the REC indicator and elapsed time."""
        dashboard.start_recording()
        assert dashboard._rec_indicator.isVisibleTo(dashboard)
        assert dashboard._elapsed_label.isVisibleTo(dashboard)
        assert dashboard.is_recording is True

    def test_stop_recording_hides_indicator(self, dashboard: DashboardWindow):
        """Stopping recording should hide the REC indicator and elapsed time."""
        dashboard.start_recording()
        dashboard.stop_recording()
        assert not dashboard._rec_indicator.isVisibleTo(dashboard)
        assert not dashboard._elapsed_label.isVisibleTo(dashboard)
        assert dashboard.is_recording is False

    def test_start_recording_resets_elapsed_time(self, dashboard: DashboardWindow):
        """Starting recording should reset elapsed time to zero."""
        dashboard._elapsed_seconds = 100
        dashboard.start_recording()
        assert dashboard.elapsed_seconds == 0
        assert dashboard._elapsed_label.text() == "00:00:00"

    def test_elapsed_time_format(self, dashboard: DashboardWindow):
        """Elapsed time should be formatted as HH:MM:SS."""
        assert DashboardWindow._format_elapsed_time(0) == "00:00:00"
        assert DashboardWindow._format_elapsed_time(59) == "00:00:59"
        assert DashboardWindow._format_elapsed_time(60) == "00:01:00"
        assert DashboardWindow._format_elapsed_time(3661) == "01:01:01"
        assert DashboardWindow._format_elapsed_time(86399) == "23:59:59"

    def test_timer_increments_elapsed_seconds(self, dashboard: DashboardWindow):
        """The timer callback should increment elapsed seconds and update label."""
        dashboard.start_recording()
        # Simulate timer ticks
        dashboard._update_elapsed_time()
        assert dashboard.elapsed_seconds == 1
        assert dashboard._elapsed_label.text() == "00:00:01"

        dashboard._update_elapsed_time()
        assert dashboard.elapsed_seconds == 2
        assert dashboard._elapsed_label.text() == "00:00:02"


class TestTouchTargetSize:
    """Tests for minimum touch target size compliance."""

    def test_rec_indicator_minimum_size(self, dashboard: DashboardWindow):
        """Recording indicator should meet minimum touch target size."""
        assert dashboard._rec_indicator.minimumWidth() >= MIN_TOUCH_TARGET_PX
        assert dashboard._rec_indicator.minimumHeight() >= MIN_TOUCH_TARGET_PX

    def test_elapsed_label_minimum_size(self, dashboard: DashboardWindow):
        """Elapsed time label should meet minimum touch target size."""
        assert dashboard._elapsed_label.minimumWidth() >= MIN_TOUCH_TARGET_PX
        assert dashboard._elapsed_label.minimumHeight() >= MIN_TOUCH_TARGET_PX

    def test_connection_label_minimum_size(self, dashboard: DashboardWindow):
        """Connection status label should meet minimum touch target size."""
        assert dashboard._connection_label.minimumWidth() >= MIN_TOUCH_TARGET_PX
        assert dashboard._connection_label.minimumHeight() >= MIN_TOUCH_TARGET_PX


class TestConnectionStatus:
    """Tests for remote monitoring connection status display."""

    def test_default_connection_status(self, dashboard: DashboardWindow):
        """Default connection status should show disconnected."""
        assert "Disconnected" in dashboard._connection_label.text()

    def test_set_connected_status(self, dashboard: DashboardWindow):
        """Setting connected status should show client count."""
        dashboard.set_connection_status(connected=True, client_count=2)
        assert "2 client(s)" in dashboard._connection_label.text()

    def test_set_disconnected_status(self, dashboard: DashboardWindow):
        """Setting disconnected status should show disconnected text."""
        dashboard.set_connection_status(connected=True, client_count=1)
        dashboard.set_connection_status(connected=False)
        assert "Disconnected" in dashboard._connection_label.text()


class TestWindowProperties:
    """Tests for general window properties."""

    def test_window_title(self, dashboard: DashboardWindow):
        """Window should have the correct title."""
        assert dashboard.windowTitle() == "Rotax Dyno DAQ"

    def test_window_is_qmainwindow(self, dashboard: DashboardWindow):
        """DashboardWindow should extend QMainWindow."""
        from PyQt6.QtWidgets import QMainWindow
        assert isinstance(dashboard, QMainWindow)

    def test_central_widget_is_tab_widget(self, dashboard: DashboardWindow):
        """Central widget should be the tab widget."""
        assert dashboard.centralWidget() is dashboard.tab_widget
