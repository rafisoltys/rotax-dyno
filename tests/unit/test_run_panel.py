"""Unit tests for RunPanel and RunStartDialog widgets.

Tests cover:
- RunStartDialog validation (name length, notes length)
- RunPanel start/stop button states
- RunPanel run log table population
- RunPanel filter functionality
- Touch target minimum sizes
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

import pytest

# Set offscreen platform before importing any Qt modules
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication

from rotax_dyno_daq.dashboard.run_panel import (
    MIN_TOUCH_TARGET_PX,
    RunPanel,
    RunStartDialog,
)
from rotax_dyno_daq.storage.run_manager import RunManager


@pytest.fixture(scope="module")
def qapp():
    """Create a QApplication instance for the test module."""
    app = QApplication.instance()
    if app is None:
        app = QApplication(sys.argv)
    return app


@pytest.fixture
def run_log_path(tmp_path):
    """Create a temporary path for the run log."""
    return tmp_path / "run_log.json"


@pytest.fixture
def run_manager(run_log_path):
    """Create a RunManager instance for testing."""
    return RunManager(run_log_path=run_log_path)


@pytest.fixture
def run_panel(qapp, run_manager):
    """Create a RunPanel instance for testing."""
    panel = RunPanel(run_manager=run_manager)
    yield panel
    panel.close()


@pytest.fixture
def run_dialog(qapp):
    """Create a RunStartDialog instance for testing."""
    dialog = RunStartDialog()
    yield dialog
    dialog.close()


class TestRunStartDialog:
    """Tests for the RunStartDialog."""

    def test_name_input_max_length(self, run_dialog: RunStartDialog):
        """Name input should have max length of 100."""
        assert run_dialog.name_input.maxLength() == 100

    def test_name_input_minimum_height(self, run_dialog: RunStartDialog):
        """Name input should meet minimum touch target height."""
        assert run_dialog.name_input.minimumHeight() >= MIN_TOUCH_TARGET_PX

    def test_run_name_property(self, run_dialog: RunStartDialog):
        """run_name property should return stripped text."""
        run_dialog.name_input.setText("  Test Run  ")
        assert run_dialog.run_name == "Test Run"

    def test_run_notes_property(self, run_dialog: RunStartDialog):
        """run_notes property should return the notes text."""
        run_dialog.notes_input.setPlainText("Some notes here")
        assert run_dialog.run_notes == "Some notes here"

    def test_empty_name_returns_empty(self, run_dialog: RunStartDialog):
        """Empty name input should return empty string."""
        run_dialog.name_input.setText("")
        assert run_dialog.run_name == ""

    def test_dialog_has_title(self, run_dialog: RunStartDialog):
        """Dialog should have appropriate title."""
        assert run_dialog.windowTitle() == "Start New Run"


class TestRunPanel:
    """Tests for the RunPanel widget."""

    def test_start_button_enabled_by_default(self, run_panel: RunPanel):
        """Start button should be enabled when no run is active."""
        assert run_panel.start_button.isEnabled()

    def test_stop_button_disabled_by_default(self, run_panel: RunPanel):
        """Stop button should be disabled when no run is active."""
        assert not run_panel.stop_button.isEnabled()

    def test_start_button_minimum_size(self, run_panel: RunPanel):
        """Start button should meet minimum touch target size."""
        assert run_panel.start_button.minimumWidth() >= MIN_TOUCH_TARGET_PX
        assert run_panel.start_button.minimumHeight() >= MIN_TOUCH_TARGET_PX

    def test_stop_button_minimum_size(self, run_panel: RunPanel):
        """Stop button should meet minimum touch target size."""
        assert run_panel.stop_button.minimumWidth() >= MIN_TOUCH_TARGET_PX
        assert run_panel.stop_button.minimumHeight() >= MIN_TOUCH_TARGET_PX

    def test_run_table_has_correct_columns(self, run_panel: RunPanel):
        """Run log table should have 6 columns."""
        assert run_panel.run_table.columnCount() == 6

    def test_run_table_column_headers(self, run_panel: RunPanel):
        """Run log table should have correct column headers."""
        expected = ["Name", "Date", "Duration", "Notes", "Tags", "Run ID"]
        for i, name in enumerate(expected):
            item = run_panel.run_table.horizontalHeaderItem(i)
            assert item is not None
            assert item.text() == name

    def test_run_table_empty_initially(self, run_panel: RunPanel):
        """Run log table should be empty when no runs exist."""
        assert run_panel.run_table.rowCount() == 0

    def test_run_table_populated_after_run(self, run_panel: RunPanel):
        """Run log table should show completed runs."""
        rm = run_panel.run_manager
        rm.start_run("Test Run 1", "Some notes")
        rm.stop_run()
        run_panel._refresh_run_log()
        assert run_panel.run_table.rowCount() == 1
        assert run_panel.run_table.item(0, 0).text() == "Test Run 1"

    def test_name_filter_minimum_height(self, run_panel: RunPanel):
        """Name filter input should meet minimum touch target height."""
        assert run_panel.name_filter.minimumHeight() >= MIN_TOUCH_TARGET_PX

    def test_tag_filter_minimum_height(self, run_panel: RunPanel):
        """Tag filter input should meet minimum touch target height."""
        assert run_panel.tag_filter.minimumHeight() >= MIN_TOUCH_TARGET_PX

    def test_format_duration_seconds(self):
        """Duration formatting should handle seconds only."""
        assert RunPanel._format_duration(45) == "45s"

    def test_format_duration_minutes(self):
        """Duration formatting should handle minutes and seconds."""
        assert RunPanel._format_duration(125) == "2m 5s"

    def test_format_duration_hours(self):
        """Duration formatting should handle hours, minutes, and seconds."""
        assert RunPanel._format_duration(3661) == "1h 1m 1s"

    def test_name_filter_triggers_refresh(self, run_panel: RunPanel):
        """Changing name filter should trigger a run log refresh."""
        rm = run_panel.run_manager
        rm.start_run("Alpha Run", "")
        rm.stop_run()
        rm.start_run("Beta Run", "")
        rm.stop_run()

        run_panel._refresh_run_log()
        assert run_panel.run_table.rowCount() == 2

        # Filter by name
        run_panel.name_filter.setText("Alpha")
        # The filter change triggers _on_filter_changed -> _refresh_run_log
        assert run_panel.run_table.rowCount() == 1
        assert run_panel.run_table.item(0, 0).text() == "Alpha Run"
