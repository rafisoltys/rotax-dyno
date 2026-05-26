"""Unit tests for the StripChartWidget and StripChartPanel.

Since PyQtGraph and PyQt6 may not be available in the test environment,
we mock the Qt/pyqtgraph modules at the sys.modules level before importing
the module under test. This tests the data handling logic without requiring
a display server.
"""

from __future__ import annotations

import sys
import types
from collections import deque
from dataclasses import dataclass
from unittest.mock import MagicMock, patch

import pytest


@dataclass
class FakeSample:
    """Minimal sample for testing the data callback."""

    channel_id: str
    timestamp_ms: float
    calibrated_value: float
    unit: str = "°C"


# ---- Module-level mocking setup ----

class _MockModule(types.ModuleType):
    """A module that returns MagicMock for any missing attribute."""

    def __getattr__(self, name):
        if name.startswith("__") and name.endswith("__"):
            raise AttributeError(name)
        return MagicMock()


def _make_mock_qt_modules():
    """Create mock modules for PyQt6 and pyqtgraph."""
    # pyqtgraph mock - PlotWidget needs to be a real class (not MagicMock)
    # so that __new__ and attribute setting work correctly
    mock_pg = types.ModuleType("pyqtgraph")

    class FakePlotWidget:
        """Fake PlotWidget base class for testing."""

        def __init__(self, *args, **kwargs):
            pass

        def setTitle(self, *args, **kwargs):
            pass

        def setLabel(self, *args, **kwargs):
            pass

        def showGrid(self, *args, **kwargs):
            pass

        def setBackground(self, *args, **kwargs):
            pass

        def setMouseEnabled(self, *args, **kwargs):
            pass

        def enableAutoRange(self, *args, **kwargs):
            pass

        def plot(self, *args, **kwargs):
            return MagicMock()

        def addItem(self, *args, **kwargs):
            pass

        def removeItem(self, *args, **kwargs):
            pass

        def setXRange(self, *args, **kwargs):
            pass

        def closeEvent(self, event):
            pass

        def deleteLater(self):
            pass

        def setParent(self, parent):
            pass

    mock_pg.PlotWidget = FakePlotWidget
    mock_pg.mkPen = MagicMock(return_value=MagicMock())
    mock_pg.InfiniteLine = MagicMock(return_value=MagicMock())

    # PyQt6 mocks
    mock_pyqt6 = _MockModule("PyQt6")
    mock_qtcore = _MockModule("PyQt6.QtCore")
    mock_qtwidgets = _MockModule("PyQt6.QtWidgets")
    mock_qtgui = _MockModule("PyQt6.QtGui")

    # QTimer mock that returns a mock instance
    class FakeQTimer:
        def __init__(self, parent=None):
            self._interval = 0
            self._running = False
            self._callback = None

        def setInterval(self, ms):
            self._interval = ms

        def start(self):
            self._running = True

        def stop(self):
            self._running = False

        @property
        def timeout(self):
            mock = MagicMock()
            mock.connect = MagicMock()
            return mock

    mock_qtcore.QTimer = FakeQTimer

    # Qt namespace mock
    class FakeQt:
        class PenStyle:
            DashLine = 2

        class ScrollBarPolicy:
            ScrollBarAlwaysOff = 1

    mock_qtcore.Qt = FakeQt

    # QWidget and layout mocks
    class FakeQWidget:
        def __init__(self, parent=None):
            pass

        def setParent(self, parent):
            pass

        def deleteLater(self):
            pass

        def closeEvent(self, event):
            pass

    class FakeQScrollArea(FakeQWidget):
        def setWidgetResizable(self, v):
            pass

        def setHorizontalScrollBarPolicy(self, p):
            pass

        def setWidget(self, w):
            pass

    class FakeQGridLayout:
        def __init__(self, parent=None):
            self._items = []

        def setSpacing(self, s):
            pass

        def addWidget(self, widget, row, col):
            self._items.append(widget)

        def removeWidget(self, widget):
            if widget in self._items:
                self._items.remove(widget)

        def count(self):
            return len(self._items)

        def takeAt(self, idx):
            if self._items:
                item = self._items.pop(0)
                mock_item = MagicMock()
                mock_item.widget.return_value = item
                return mock_item
            return None

    class FakeQVBoxLayout:
        def __init__(self, parent=None):
            pass

        def setContentsMargins(self, *args):
            pass

        def addWidget(self, widget):
            pass

    mock_qtwidgets.QWidget = FakeQWidget
    mock_qtwidgets.QScrollArea = FakeQScrollArea
    mock_qtwidgets.QGridLayout = FakeQGridLayout
    mock_qtwidgets.QVBoxLayout = FakeQVBoxLayout

    mock_pyqt6.QtCore = mock_qtcore
    mock_pyqt6.QtWidgets = mock_qtwidgets
    mock_pyqt6.QtGui = mock_qtgui

    return {
        "pyqtgraph": mock_pg,
        "PyQt6": mock_pyqt6,
        "PyQt6.QtCore": mock_qtcore,
        "PyQt6.QtWidgets": mock_qtwidgets,
        "PyQt6.QtGui": mock_qtgui,
    }


@pytest.fixture(autouse=True)
def mock_qt_modules(monkeypatch):
    """Install mock Qt/pyqtgraph modules before each test."""
    mocks = _make_mock_qt_modules()
    for name, mod in mocks.items():
        monkeypatch.setitem(sys.modules, name, mod)

    # Clear cached imports of the dashboard package and its modules
    modules_to_clear = [
        k for k in sys.modules if k.startswith("rotax_dyno_daq.dashboard")
    ]
    for mod_name in modules_to_clear:
        monkeypatch.delitem(sys.modules, mod_name, raising=False)

    yield


@pytest.fixture
def data_bus():
    """Create a real DataBus instance for testing."""
    from rotax_dyno_daq.core.data_bus import DataBus

    return DataBus()


class TestStripChartWidget:
    """Tests for StripChartWidget data handling logic."""

    def _create_widget(self, data_bus, channel_id="EGT1", unit="°C", time_window=60):
        """Create a StripChartWidget instance with mocked Qt."""
        from rotax_dyno_daq.dashboard.strip_chart import StripChartWidget

        widget = StripChartWidget(
            channel_id=channel_id,
            unit=unit,
            data_bus=data_bus,
            time_window_seconds=time_window,
            display_name=channel_id,
        )
        return widget

    def test_on_sample_stores_data(self, data_bus):
        """Verify that incoming samples are stored in the ring buffer."""
        widget = self._create_widget(data_bus)

        sample = FakeSample(
            channel_id="EGT1", timestamp_ms=1000.0, calibrated_value=450.0
        )
        data_bus.publish("EGT1", sample)

        assert len(widget._timestamps) == 1
        assert widget._timestamps[0] == 1.0  # 1000ms -> 1.0s
        assert widget._values[0] == 450.0

    def test_on_sample_multiple_points(self, data_bus):
        """Verify multiple samples accumulate in the buffer."""
        widget = self._create_widget(data_bus)

        for i in range(5):
            sample = FakeSample(
                channel_id="EGT1",
                timestamp_ms=float(i * 1000),
                calibrated_value=400.0 + i * 10,
            )
            data_bus.publish("EGT1", sample)

        assert len(widget._timestamps) == 5
        assert widget._values[0] == 400.0
        assert widget._values[4] == 440.0

    def test_on_sample_ignores_invalid_sample(self, data_bus):
        """Verify that samples without expected attributes are ignored."""
        widget = self._create_widget(data_bus)

        # Publish something that doesn't have calibrated_value
        data_bus.publish("EGT1", {"raw": 123})

        assert len(widget._timestamps) == 0
        assert len(widget._values) == 0

    def test_ring_buffer_capacity(self, data_bus):
        """Verify the ring buffer respects maxlen based on time window."""
        widget = self._create_widget(data_bus, time_window=30)

        # maxlen = 30 * 100 = 3000
        assert widget._timestamps.maxlen == 3000
        assert widget._values.maxlen == 3000

    def test_set_time_window_clamps_minimum(self, data_bus):
        """Verify time window is clamped to minimum of 30 seconds."""
        widget = self._create_widget(data_bus, time_window=60)
        widget.set_time_window(10)  # Below minimum

        assert widget.time_window_seconds == 30

    def test_set_time_window_clamps_maximum(self, data_bus):
        """Verify time window is clamped to maximum of 600 seconds."""
        widget = self._create_widget(data_bus, time_window=60)
        widget.set_time_window(1000)  # Above maximum

        assert widget.time_window_seconds == 600

    def test_set_time_window_preserves_data(self, data_bus):
        """Verify existing data is preserved when time window changes."""
        widget = self._create_widget(data_bus, time_window=60)

        # Add some data
        for i in range(10):
            sample = FakeSample(
                channel_id="EGT1",
                timestamp_ms=float(i * 100),
                calibrated_value=float(i),
            )
            data_bus.publish("EGT1", sample)

        widget.set_time_window(120)

        assert len(widget._timestamps) == 10
        assert widget._values[0] == 0.0
        assert widget._values[9] == 9.0

    def test_time_window_clamped_on_init(self, data_bus):
        """Verify time window is clamped during initialization."""
        widget = self._create_widget(data_bus, time_window=5)
        assert widget.time_window_seconds == 30

        widget2 = self._create_widget(data_bus, time_window=999)
        assert widget2.time_window_seconds == 600

    def test_cleanup_unsubscribes_from_databus(self, data_bus):
        """Verify cleanup unsubscribes from DataBus."""
        widget = self._create_widget(data_bus)

        widget.cleanup()

        # Verify unsubscription by publishing - should not add data
        sample = FakeSample(
            channel_id="EGT1", timestamp_ms=5000.0, calibrated_value=500.0
        )
        data_bus.publish("EGT1", sample)
        assert len(widget._timestamps) == 0

    def test_databus_subscription_uses_channel_id(self, data_bus):
        """Verify the widget only receives data for its channel."""
        widget = self._create_widget(data_bus, channel_id="EGT1")

        # Publish to a different channel
        sample = FakeSample(
            channel_id="OilP", timestamp_ms=1000.0, calibrated_value=3.5
        )
        data_bus.publish("OilP", sample)

        assert len(widget._timestamps) == 0

        # Publish to the correct channel
        sample2 = FakeSample(
            channel_id="EGT1", timestamp_ms=2000.0, calibrated_value=450.0
        )
        data_bus.publish("EGT1", sample2)

        assert len(widget._timestamps) == 1

    def test_update_plot_with_no_data(self, data_bus):
        """Verify _update_plot handles empty buffer gracefully."""
        widget = self._create_widget(data_bus)
        # Should not raise
        widget._update_plot()

    def test_update_plot_sets_curve_data(self, data_bus):
        """Verify _update_plot updates the curve with buffer data."""
        widget = self._create_widget(data_bus)

        # Add data
        for i in range(3):
            sample = FakeSample(
                channel_id="EGT1",
                timestamp_ms=float(i * 1000),
                calibrated_value=100.0 + i,
            )
            data_bus.publish("EGT1", sample)

        widget._update_plot()

        # The curve's setData should have been called
        widget._curve.setData.assert_called_once()
        args = widget._curve.setData.call_args[0]
        assert args[0] == [0.0, 1.0, 2.0]
        assert args[1] == [100.0, 101.0, 102.0]


class TestStripChartPanel:
    """Tests for StripChartPanel container logic."""

    def _create_panel(self, data_bus, time_window=60):
        """Create a StripChartPanel instance."""
        from rotax_dyno_daq.dashboard.strip_chart import StripChartPanel

        panel = StripChartPanel(
            data_bus=data_bus,
            time_window_seconds=time_window,
        )
        return panel

    def test_add_channel_creates_chart(self, data_bus):
        """Verify adding a channel creates a StripChartWidget."""
        panel = self._create_panel(data_bus)

        chart = panel.add_channel("EGT1", "°C", "Exhaust Gas Temp 1")

        assert "EGT1" in panel._charts
        assert chart is panel._charts["EGT1"]
        assert chart.channel_id == "EGT1"

    def test_add_channel_returns_existing(self, data_bus):
        """Verify adding a duplicate channel returns the existing chart."""
        panel = self._create_panel(data_bus)

        first = panel.add_channel("EGT1", "°C")
        second = panel.add_channel("EGT1", "°C")

        assert first is second

    def test_remove_channel(self, data_bus):
        """Verify removing a channel cleans up and removes the chart."""
        panel = self._create_panel(data_bus)
        panel.add_channel("EGT1", "°C")

        assert "EGT1" in panel._charts

        panel.remove_channel("EGT1")

        assert "EGT1" not in panel._charts

    def test_remove_channel_unsubscribes(self, data_bus):
        """Verify removing a channel unsubscribes from DataBus."""
        panel = self._create_panel(data_bus)
        panel.add_channel("EGT1", "°C")

        # Get the chart's deque reference before removal
        chart = panel._charts["EGT1"]
        timestamps_ref = chart._timestamps

        panel.remove_channel("EGT1")

        # Publishing should not add data to the removed chart's buffer
        sample = FakeSample(
            channel_id="EGT1", timestamp_ms=1000.0, calibrated_value=450.0
        )
        data_bus.publish("EGT1", sample)
        assert len(timestamps_ref) == 0

    def test_remove_nonexistent_channel_is_noop(self, data_bus):
        """Verify removing a non-existent channel does nothing."""
        panel = self._create_panel(data_bus)
        # Should not raise
        panel.remove_channel("NONEXISTENT")

    def test_set_time_window_updates_all_charts(self, data_bus):
        """Verify set_time_window propagates to all charts."""
        panel = self._create_panel(data_bus)
        panel.add_channel("EGT1", "°C")
        panel.add_channel("OilP", "bar")

        panel.set_time_window(120)

        assert panel._time_window_seconds == 120
        assert panel._charts["EGT1"].time_window_seconds == 120
        assert panel._charts["OilP"].time_window_seconds == 120

    def test_set_time_window_clamps_values(self, data_bus):
        """Verify panel time window is clamped to valid range."""
        panel = self._create_panel(data_bus)

        panel.set_time_window(5)
        assert panel._time_window_seconds == 30

        panel.set_time_window(9999)
        assert panel._time_window_seconds == 600

    def test_get_chart_returns_chart(self, data_bus):
        """Verify get_chart returns the correct chart or None."""
        panel = self._create_panel(data_bus)
        panel.add_channel("EGT1", "°C")

        assert panel.get_chart("EGT1") is panel._charts["EGT1"]
        assert panel.get_chart("NONEXISTENT") is None

    def test_cleanup_cleans_all_charts(self, data_bus):
        """Verify cleanup unsubscribes all charts and clears the dict."""
        panel = self._create_panel(data_bus)
        panel.add_channel("EGT1", "°C")
        panel.add_channel("OilP", "bar")

        # Get references to check unsubscription
        egt_timestamps = panel._charts["EGT1"]._timestamps
        oilp_timestamps = panel._charts["OilP"]._timestamps

        panel.cleanup()

        assert len(panel._charts) == 0

        # Verify unsubscription
        data_bus.publish("EGT1", FakeSample("EGT1", 1000.0, 450.0))
        data_bus.publish("OilP", FakeSample("OilP", 1000.0, 3.5))
        assert len(egt_timestamps) == 0
        assert len(oilp_timestamps) == 0

    def test_multiple_channels_receive_independent_data(self, data_bus):
        """Verify each chart only receives its own channel's data."""
        panel = self._create_panel(data_bus)
        panel.add_channel("EGT1", "°C")
        panel.add_channel("OilP", "bar")

        data_bus.publish("EGT1", FakeSample("EGT1", 1000.0, 450.0))
        data_bus.publish("OilP", FakeSample("OilP", 2000.0, 3.5))

        assert len(panel._charts["EGT1"]._timestamps) == 1
        assert panel._charts["EGT1"]._values[0] == 450.0
        assert len(panel._charts["OilP"]._timestamps) == 1
        assert panel._charts["OilP"]._values[0] == 3.5
