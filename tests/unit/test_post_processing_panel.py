"""Unit tests for PostProcessingPanel widget.

Tests cover:
- Panel creation and UI elements
- Source file loading and channel population
- Parameter validation (cutoff ≤ Nyquist, window in [3, 101])
- Preview functionality
- Process & Save triggering
- Touch target minimum sizes
- Cancel/reset behavior
"""

from __future__ import annotations

import csv
import os
import sys
from pathlib import Path
from unittest.mock import patch, MagicMock

import numpy as np
import pytest

# Set offscreen platform before importing any Qt modules
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication

from rotax_dyno_daq.dashboard.post_processing_panel import (
    MIN_TOUCH_TARGET_PX,
    PostProcessingPanel,
)
from rotax_dyno_daq.processing.post_processor import PostProcessor


@pytest.fixture(scope="module")
def qapp():
    """Create a QApplication instance for the test module."""
    app = QApplication.instance()
    if app is None:
        app = QApplication(sys.argv)
    return app


@pytest.fixture
def panel(qapp):
    """Create a PostProcessingPanel instance for testing."""
    p = PostProcessingPanel()
    yield p
    p.close()


@pytest.fixture
def sample_csv(tmp_path):
    """Create a sample CSV file for testing."""
    csv_path = tmp_path / "test_run.csv"
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["# Run Name", "Test Run"])
        writer.writerow(["# Start Time", "2024-01-15T10:30:00"])
        writer.writerow(
            ["timestamp_ms", "channel_id", "calibrated_value", "unit", "validity"]
        )
        # Write 20 samples for EGT1 at 10 Hz (100ms intervals)
        for i in range(20):
            timestamp_ms = i * 100.0
            writer.writerow([f"{timestamp_ms:.3f}", "EGT1", f"{600.0 + i}", "°C", "valid"])
        # Write 20 samples for EGT2 at 10 Hz
        for i in range(20):
            timestamp_ms = i * 100.0
            writer.writerow([f"{timestamp_ms:.3f}", "EGT2", f"{580.0 + i * 2}", "°C", "valid"])
        # Write 20 samples for OilP at 10 Hz
        for i in range(20):
            timestamp_ms = i * 100.0
            writer.writerow([f"{timestamp_ms:.3f}", "OilP", f"{3.0 + i * 0.1}", "bar", "valid"])
    return csv_path


class TestPanelCreation:
    """Tests for panel initialization and UI structure."""

    def test_panel_creates_successfully(self, panel: PostProcessingPanel):
        """Panel should be created without errors."""
        assert panel is not None

    def test_has_post_processor(self, panel: PostProcessingPanel):
        """Panel should have a PostProcessor instance."""
        assert isinstance(panel.post_processor, PostProcessor)

    def test_custom_post_processor(self, qapp):
        """Panel should accept a custom PostProcessor."""
        custom_pp = PostProcessor()
        p = PostProcessingPanel(post_processor=custom_pp)
        assert p.post_processor is custom_pp
        p.close()

    def test_initial_state(self, panel: PostProcessingPanel):
        """Panel should start with no source file and default parameters."""
        assert panel.source_path is None
        assert panel.cutoff_frequency == 0.0
        assert panel.window_size == 0
        assert panel.egt_spread_enabled is False
        assert panel.rate_of_change_enabled is False


class TestSourceFileLoading:
    """Tests for source file selection and channel population."""

    def test_load_source_file(self, panel: PostProcessingPanel, sample_csv: Path):
        """Loading a source file should populate the channel list."""
        panel.set_source_file(sample_csv)

        assert panel.source_path == sample_csv
        assert panel._channel_list.count() == 3  # EGT1, EGT2, OilP

    def test_channel_list_sorted(self, panel: PostProcessingPanel, sample_csv: Path):
        """Channel list should be sorted alphabetically."""
        panel.set_source_file(sample_csv)

        channels = [
            panel._channel_list.item(i).text()
            for i in range(panel._channel_list.count())
        ]
        assert channels == sorted(channels)

    def test_nyquist_label_updated(self, panel: PostProcessingPanel, sample_csv: Path):
        """Nyquist label should show the minimum Nyquist frequency."""
        panel.set_source_file(sample_csv)

        # All channels at 10 Hz -> Nyquist = 5.0 Hz
        assert "5.0" in panel._nyquist_label.text()

    def test_nonexistent_file_shows_warning(self, panel: PostProcessingPanel, tmp_path):
        """Loading a non-existent file should not crash."""
        fake_path = tmp_path / "nonexistent.csv"
        # Should not raise; shows a warning dialog
        with patch("rotax_dyno_daq.dashboard.post_processing_panel.QMessageBox.warning"):
            panel._load_source_file(fake_path)
        assert panel.source_path is None

    def test_source_path_edit_shows_path(
        self, panel: PostProcessingPanel, sample_csv: Path
    ):
        """The source path line edit should display the file path."""
        panel.set_source_file(sample_csv)
        assert str(sample_csv) in panel._source_path_edit.text()


class TestParameterValidation:
    """Tests for filter parameter validation."""

    def test_valid_parameters_no_errors(
        self, panel: PostProcessingPanel, sample_csv: Path
    ):
        """Valid parameters should produce no validation errors."""
        panel.set_source_file(sample_csv)
        # Select a channel
        panel._channel_list.item(0).setSelected(True)
        # Set valid cutoff (below Nyquist of 5.0 Hz)
        panel._cutoff_spinbox.setValue(2.0)
        panel._window_spinbox.setValue(5)

        errors = panel._validate_parameters()
        assert errors == []

    def test_cutoff_above_nyquist_rejected(
        self, panel: PostProcessingPanel, sample_csv: Path
    ):
        """Cutoff frequency above Nyquist should produce an error."""
        panel.set_source_file(sample_csv)
        # Select a channel
        panel._channel_list.item(0).setSelected(True)
        # Set cutoff above Nyquist (sample rate ~10 Hz, Nyquist = 5 Hz)
        panel._cutoff_spinbox.setValue(6.0)

        errors = panel._validate_parameters()
        assert len(errors) > 0
        assert any("Nyquist" in e or "cutoff" in e.lower() for e in errors)

    def test_cutoff_at_exactly_nyquist_rejected(
        self, panel: PostProcessingPanel, sample_csv: Path
    ):
        """Cutoff at exactly Nyquist should produce an error."""
        panel.set_source_file(sample_csv)
        panel._channel_list.item(0).setSelected(True)
        # Nyquist is 5.0 Hz for 10 Hz sample rate
        panel._cutoff_spinbox.setValue(5.0)

        errors = panel._validate_parameters()
        assert len(errors) > 0

    def test_cutoff_below_minimum_rejected(
        self, panel: PostProcessingPanel, sample_csv: Path
    ):
        """Cutoff below 0.1 Hz should produce an error."""
        panel.set_source_file(sample_csv)
        panel._channel_list.item(0).setSelected(True)
        panel._cutoff_spinbox.setValue(0.05)

        errors = panel._validate_parameters()
        assert len(errors) > 0
        assert any("0.1" in e for e in errors)

    def test_window_below_3_rejected(self, panel: PostProcessingPanel):
        """Window size below 3 (but not 0/disabled) should produce an error."""
        panel._window_spinbox.setValue(2)

        errors = panel._validate_parameters()
        assert len(errors) > 0
        assert any("3" in e and "101" in e for e in errors)

    def test_window_above_101_rejected(self, panel: PostProcessingPanel):
        """Window size above 101 should produce an error."""
        panel._window_spinbox.setValue(102)

        errors = panel._validate_parameters()
        assert len(errors) > 0
        assert any("3" in e and "101" in e for e in errors)

    def test_window_zero_is_disabled(self, panel: PostProcessingPanel):
        """Window size of 0 means disabled, should not produce an error."""
        panel._window_spinbox.setValue(0)

        errors = panel._validate_parameters()
        assert errors == []

    def test_cutoff_zero_is_disabled(self, panel: PostProcessingPanel):
        """Cutoff of 0 means disabled, should not produce an error."""
        panel._cutoff_spinbox.setValue(0.0)

        errors = panel._validate_parameters()
        assert errors == []

    def test_valid_window_boundaries(self, panel: PostProcessingPanel):
        """Window sizes at boundaries (3 and 101) should be valid."""
        panel._window_spinbox.setValue(3)
        assert panel._validate_parameters() == []

        panel._window_spinbox.setValue(101)
        assert panel._validate_parameters() == []


class TestPreview:
    """Tests for preview functionality."""

    def test_preview_updates_plot_data(
        self, panel: PostProcessingPanel, sample_csv: Path
    ):
        """Preview should update the plot curves with data."""
        panel.set_source_file(sample_csv)
        # Select first channel
        panel._channel_list.item(0).setSelected(True)
        # Set a valid window size
        panel._window_spinbox.setValue(3)

        panel._on_preview()

        # Check that curves have data
        raw_x, raw_y = panel._raw_curve.getData()
        proc_x, proc_y = panel._processed_curve.getData()

        assert len(raw_x) > 0
        assert len(proc_x) > 0

    def test_preview_without_selection_shows_warning(
        self, panel: PostProcessingPanel, sample_csv: Path
    ):
        """Preview with no channels selected should show a warning."""
        panel.set_source_file(sample_csv)
        # Don't select any channels

        with patch("rotax_dyno_daq.dashboard.post_processing_panel.QMessageBox.warning") as mock_warn:
            panel._on_preview()
            mock_warn.assert_called_once()

    def test_preview_with_invalid_params_shows_warning(
        self, panel: PostProcessingPanel, sample_csv: Path
    ):
        """Preview with invalid parameters should show a warning."""
        panel.set_source_file(sample_csv)
        panel._channel_list.item(0).setSelected(True)
        panel._window_spinbox.setValue(2)  # Invalid

        with patch("rotax_dyno_daq.dashboard.post_processing_panel.QMessageBox.warning") as mock_warn:
            panel._on_preview()
            mock_warn.assert_called_once()


class TestProcessAndSave:
    """Tests for Process & Save functionality."""

    def test_process_and_save_creates_file(
        self, panel: PostProcessingPanel, sample_csv: Path
    ):
        """Process & Save should create a processed CSV file."""
        panel.set_source_file(sample_csv)
        panel._channel_list.item(0).setSelected(True)
        panel._window_spinbox.setValue(3)

        with patch("rotax_dyno_daq.dashboard.post_processing_panel.QMessageBox.information"):
            panel._on_process_and_save()

        # Check that the processed file was created
        expected_output = sample_csv.parent / f"{sample_csv.stem}_processed.csv"
        assert expected_output.exists()

    def test_process_without_source_shows_warning(self, panel: PostProcessingPanel):
        """Process & Save without a source file should show a warning."""
        with patch("rotax_dyno_daq.dashboard.post_processing_panel.QMessageBox.warning") as mock_warn:
            panel._on_process_and_save()
            mock_warn.assert_called_once()

    def test_process_without_channels_shows_warning(
        self, panel: PostProcessingPanel, sample_csv: Path
    ):
        """Process & Save without selected channels should show a warning."""
        panel.set_source_file(sample_csv)
        # Don't select any channels

        with patch("rotax_dyno_daq.dashboard.post_processing_panel.QMessageBox.warning") as mock_warn:
            panel._on_process_and_save()
            mock_warn.assert_called_once()

    def test_process_with_invalid_params_shows_warning(
        self, panel: PostProcessingPanel, sample_csv: Path
    ):
        """Process & Save with invalid parameters should show a warning."""
        panel.set_source_file(sample_csv)
        panel._channel_list.item(0).setSelected(True)
        panel._window_spinbox.setValue(2)  # Invalid

        with patch("rotax_dyno_daq.dashboard.post_processing_panel.QMessageBox.warning") as mock_warn:
            panel._on_process_and_save()
            mock_warn.assert_called_once()


class TestBuildConfig:
    """Tests for PostProcessConfig construction from UI state."""

    def test_builds_config_with_cutoff(
        self, panel: PostProcessingPanel, sample_csv: Path
    ):
        """Config should include cutoff when set."""
        panel.set_source_file(sample_csv)
        panel._cutoff_spinbox.setValue(2.5)

        config = panel._build_config(["EGT1"])
        assert config.low_pass_cutoff_hz == 2.5

    def test_builds_config_with_window(
        self, panel: PostProcessingPanel, sample_csv: Path
    ):
        """Config should include window size when set."""
        panel.set_source_file(sample_csv)
        panel._window_spinbox.setValue(7)

        config = panel._build_config(["EGT1"])
        assert config.moving_average_window == 7

    def test_builds_config_disabled_cutoff(
        self, panel: PostProcessingPanel, sample_csv: Path
    ):
        """Config should have None cutoff when disabled (0)."""
        panel.set_source_file(sample_csv)
        panel._cutoff_spinbox.setValue(0.0)

        config = panel._build_config(["EGT1"])
        assert config.low_pass_cutoff_hz is None

    def test_builds_config_disabled_window(
        self, panel: PostProcessingPanel, sample_csv: Path
    ):
        """Config should have None window when disabled (0)."""
        panel.set_source_file(sample_csv)
        panel._window_spinbox.setValue(0)

        config = panel._build_config(["EGT1"])
        assert config.moving_average_window is None

    def test_builds_config_with_egt_spread(
        self, panel: PostProcessingPanel, sample_csv: Path
    ):
        """Config should include EGT spread when checkbox is checked."""
        panel.set_source_file(sample_csv)
        panel._egt_spread_checkbox.setChecked(True)

        config = panel._build_config(["EGT1"])
        assert config.calculate_egt_spread is True

    def test_builds_config_with_rate_of_change(
        self, panel: PostProcessingPanel, sample_csv: Path
    ):
        """Config should include rate of change channels when checkbox is checked."""
        panel.set_source_file(sample_csv)
        panel._rate_of_change_checkbox.setChecked(True)

        config = panel._build_config(["EGT1", "OilP"])
        assert config.calculate_rate_of_change == ["EGT1", "OilP"]

    def test_builds_config_without_rate_of_change(
        self, panel: PostProcessingPanel, sample_csv: Path
    ):
        """Config should have empty rate of change when checkbox is unchecked."""
        panel.set_source_file(sample_csv)
        panel._rate_of_change_checkbox.setChecked(False)

        config = panel._build_config(["EGT1"])
        assert config.calculate_rate_of_change == []


class TestCancelReset:
    """Tests for cancel/reset behavior."""

    def test_cancel_resets_state(
        self, panel: PostProcessingPanel, sample_csv: Path
    ):
        """Cancel should reset all panel state."""
        panel.set_source_file(sample_csv)
        panel._cutoff_spinbox.setValue(5.0)
        panel._window_spinbox.setValue(7)
        panel._egt_spread_checkbox.setChecked(True)
        panel._rate_of_change_checkbox.setChecked(True)

        panel._on_cancel()

        assert panel.source_path is None
        assert panel._source_path_edit.text() == ""
        assert panel._channel_list.count() == 0
        assert panel.cutoff_frequency == 0.0
        assert panel.window_size == 0
        assert panel.egt_spread_enabled is False
        assert panel.rate_of_change_enabled is False


class TestTouchTargetSize:
    """Tests for minimum touch target size compliance (45x45 px)."""

    def test_browse_button_minimum_size(self, panel: PostProcessingPanel):
        """Browse button should meet minimum touch target size."""
        assert panel._browse_button.minimumWidth() >= MIN_TOUCH_TARGET_PX
        assert panel._browse_button.minimumHeight() >= MIN_TOUCH_TARGET_PX

    def test_cutoff_spinbox_minimum_size(self, panel: PostProcessingPanel):
        """Cutoff spinbox should meet minimum touch target size."""
        assert panel._cutoff_spinbox.minimumWidth() >= MIN_TOUCH_TARGET_PX
        assert panel._cutoff_spinbox.minimumHeight() >= MIN_TOUCH_TARGET_PX

    def test_window_spinbox_minimum_size(self, panel: PostProcessingPanel):
        """Window spinbox should meet minimum touch target size."""
        assert panel._window_spinbox.minimumWidth() >= MIN_TOUCH_TARGET_PX
        assert panel._window_spinbox.minimumHeight() >= MIN_TOUCH_TARGET_PX

    def test_preview_button_minimum_size(self, panel: PostProcessingPanel):
        """Preview button should meet minimum touch target size."""
        assert panel._preview_button.minimumWidth() >= MIN_TOUCH_TARGET_PX
        assert panel._preview_button.minimumHeight() >= MIN_TOUCH_TARGET_PX

    def test_process_button_minimum_size(self, panel: PostProcessingPanel):
        """Process & Save button should meet minimum touch target size."""
        assert panel._process_button.minimumWidth() >= MIN_TOUCH_TARGET_PX
        assert panel._process_button.minimumHeight() >= MIN_TOUCH_TARGET_PX

    def test_cancel_button_minimum_size(self, panel: PostProcessingPanel):
        """Cancel button should meet minimum touch target size."""
        assert panel._cancel_button.minimumWidth() >= MIN_TOUCH_TARGET_PX
        assert panel._cancel_button.minimumHeight() >= MIN_TOUCH_TARGET_PX

    def test_egt_spread_checkbox_minimum_height(self, panel: PostProcessingPanel):
        """EGT spread checkbox should meet minimum touch target height."""
        assert panel._egt_spread_checkbox.minimumHeight() >= MIN_TOUCH_TARGET_PX

    def test_rate_of_change_checkbox_minimum_height(self, panel: PostProcessingPanel):
        """Rate of change checkbox should meet minimum touch target height."""
        assert panel._rate_of_change_checkbox.minimumHeight() >= MIN_TOUCH_TARGET_PX
