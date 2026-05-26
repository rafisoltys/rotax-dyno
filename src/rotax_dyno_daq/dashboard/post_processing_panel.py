"""Post-processing panel widget with filter parameters, preview, and save.

Implements Requirements 12.1, 12.2, 12.5, 12.6:
- Configurable low-pass filter cutoff frequency (0.1 Hz to Nyquist)
- Configurable moving average window size (3 to 101)
- Visual preview displaying raw and processed data as overlaid time-series charts
- Parameter validation with error messages for invalid inputs

The panel provides:
- Source file selector (browse button)
- Channel selector (multi-select for which channels to process)
- Filter parameters section with cutoff frequency and window size inputs
- Checkboxes for derived channels (EGT spread, rate of change)
- PyQtGraph preview plot showing raw (blue) and processed (orange) data overlaid
- Process & Save button triggering PostProcessor.process_and_save()
- All interactive elements minimum 45x45px touch targets
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

import numpy as np
import pyqtgraph as pg
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QCheckBox,
    QDoubleSpinBox,
    QFileDialog,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from rotax_dyno_daq.core.models import PostProcessConfig
from rotax_dyno_daq.processing.post_processor import PostProcessor

# Minimum touch target size in pixels (12mm × 12mm at 96 DPI ≈ 45×45 px)
MIN_TOUCH_TARGET_PX = 45


class PostProcessingPanel(QWidget):
    """Post-processing panel for filtering, smoothing, and derived channel calculations.

    Provides UI for selecting a source CSV file, choosing channels to process,
    configuring filter parameters, previewing results, and saving processed data.

    Attributes:
        post_processor: The PostProcessor instance used for filtering and saving.
    """

    def __init__(
        self,
        post_processor: Optional[PostProcessor] = None,
        parent: Optional[QWidget] = None,
    ) -> None:
        """Initialize the post-processing panel.

        Args:
            post_processor: PostProcessor instance. If None, creates a new one.
            parent: Optional parent widget.
        """
        super().__init__(parent)

        self.post_processor = post_processor or PostProcessor()

        # State
        self._source_path: Optional[Path] = None
        self._channel_data: dict[
            str, tuple[np.ndarray, np.ndarray, list[str], list[str]]
        ] = {}
        self._sample_rates: dict[str, float] = {}

        # Build UI
        self._setup_ui()

    def _setup_ui(self) -> None:
        """Build the panel layout with all sections."""
        main_layout = QVBoxLayout(self)
        main_layout.setSpacing(8)

        # Source file selector
        main_layout.addWidget(self._create_source_section())

        # Channel selector
        main_layout.addWidget(self._create_channel_section())

        # Filter parameters
        main_layout.addWidget(self._create_filter_section())

        # Derived channels
        main_layout.addWidget(self._create_derived_section())

        # Preview plot
        main_layout.addWidget(self._create_preview_section())

        # Action buttons
        main_layout.addLayout(self._create_action_buttons())

    def _create_source_section(self) -> QGroupBox:
        """Create the source file selector section.

        Returns:
            QGroupBox containing the file path display and browse button.
        """
        group = QGroupBox("Source File")
        layout = QHBoxLayout(group)

        self._source_path_edit = QLineEdit()
        self._source_path_edit.setReadOnly(True)
        self._source_path_edit.setPlaceholderText("Select a CSV file...")
        self._source_path_edit.setMinimumHeight(MIN_TOUCH_TARGET_PX)
        layout.addWidget(self._source_path_edit)

        self._browse_button = QPushButton("Browse...")
        self._browse_button.setMinimumSize(MIN_TOUCH_TARGET_PX, MIN_TOUCH_TARGET_PX)
        self._browse_button.clicked.connect(self._on_browse)
        layout.addWidget(self._browse_button)

        return group

    def _create_channel_section(self) -> QGroupBox:
        """Create the channel multi-select section.

        Returns:
            QGroupBox containing a list widget for channel selection.
        """
        group = QGroupBox("Channels to Process")
        layout = QVBoxLayout(group)

        self._channel_list = QListWidget()
        self._channel_list.setSelectionMode(
            QListWidget.SelectionMode.MultiSelection
        )
        self._channel_list.setMinimumHeight(100)
        layout.addWidget(self._channel_list)

        return group

    def _create_filter_section(self) -> QGroupBox:
        """Create the filter parameters section.

        Returns:
            QGroupBox containing cutoff frequency and window size inputs.
        """
        group = QGroupBox("Filter Parameters")
        layout = QVBoxLayout(group)

        # Low-pass filter cutoff frequency
        cutoff_layout = QHBoxLayout()
        cutoff_label = QLabel("Low-pass cutoff (Hz):")
        cutoff_label.setMinimumHeight(MIN_TOUCH_TARGET_PX)
        cutoff_layout.addWidget(cutoff_label)

        self._cutoff_spinbox = QDoubleSpinBox()
        self._cutoff_spinbox.setRange(0.0, 10000.0)
        self._cutoff_spinbox.setDecimals(2)
        self._cutoff_spinbox.setValue(0.0)
        self._cutoff_spinbox.setSpecialValueText("Disabled")
        self._cutoff_spinbox.setSingleStep(0.5)
        self._cutoff_spinbox.setMinimumSize(MIN_TOUCH_TARGET_PX, MIN_TOUCH_TARGET_PX)
        self._cutoff_spinbox.setToolTip(
            "Low-pass filter cutoff frequency. Must be between 0.1 Hz and "
            "Nyquist frequency (sample_rate / 2). Set to 0 to disable."
        )
        cutoff_layout.addWidget(self._cutoff_spinbox)

        self._nyquist_label = QLabel("Nyquist: --")
        cutoff_layout.addWidget(self._nyquist_label)
        layout.addLayout(cutoff_layout)

        # Moving average window size
        window_layout = QHBoxLayout()
        window_label = QLabel("Moving average window:")
        window_label.setMinimumHeight(MIN_TOUCH_TARGET_PX)
        window_layout.addWidget(window_label)

        self._window_spinbox = QSpinBox()
        self._window_spinbox.setRange(0, 999)
        self._window_spinbox.setValue(0)
        self._window_spinbox.setSpecialValueText("Disabled")
        self._window_spinbox.setSingleStep(2)
        self._window_spinbox.setMinimumSize(MIN_TOUCH_TARGET_PX, MIN_TOUCH_TARGET_PX)
        self._window_spinbox.setToolTip(
            "Moving average window size in samples. Must be between 3 and 101. "
            "Set to 0 to disable."
        )
        window_layout.addWidget(self._window_spinbox)
        layout.addLayout(window_layout)

        return group

    def _create_derived_section(self) -> QGroupBox:
        """Create the derived channels section.

        Returns:
            QGroupBox containing checkboxes for EGT spread and rate of change.
        """
        group = QGroupBox("Derived Channels")
        layout = QVBoxLayout(group)

        self._egt_spread_checkbox = QCheckBox("Calculate EGT spread")
        self._egt_spread_checkbox.setMinimumHeight(MIN_TOUCH_TARGET_PX)
        layout.addWidget(self._egt_spread_checkbox)

        self._rate_of_change_checkbox = QCheckBox("Calculate rate of change")
        self._rate_of_change_checkbox.setMinimumHeight(MIN_TOUCH_TARGET_PX)
        layout.addWidget(self._rate_of_change_checkbox)

        return group

    def _create_preview_section(self) -> QGroupBox:
        """Create the preview plot section.

        Returns:
            QGroupBox containing the PyQtGraph plot widget and preview button.
        """
        group = QGroupBox("Preview")
        layout = QVBoxLayout(group)

        # PyQtGraph plot widget
        self._preview_plot = pg.PlotWidget()
        self._preview_plot.setBackground("k")
        self._preview_plot.setLabel("bottom", "Time", units="s")
        self._preview_plot.setLabel("left", "Value")
        self._preview_plot.showGrid(x=True, y=True, alpha=0.3)
        self._preview_plot.addLegend()
        self._preview_plot.setMinimumHeight(200)
        layout.addWidget(self._preview_plot)

        # Raw data curve (blue)
        self._raw_curve = self._preview_plot.plot(
            [], [], pen=pg.mkPen(color="b", width=2), name="Raw"
        )
        # Processed data curve (orange)
        self._processed_curve = self._preview_plot.plot(
            [], [], pen=pg.mkPen(color=(255, 165, 0), width=2), name="Processed"
        )

        # Preview button
        self._preview_button = QPushButton("Preview")
        self._preview_button.setMinimumSize(MIN_TOUCH_TARGET_PX, MIN_TOUCH_TARGET_PX)
        self._preview_button.clicked.connect(self._on_preview)
        layout.addWidget(self._preview_button)

        return group

    def _create_action_buttons(self) -> QHBoxLayout:
        """Create the action buttons (Process & Save, Cancel).

        Returns:
            QHBoxLayout containing the action buttons.
        """
        layout = QHBoxLayout()
        layout.addStretch()

        self._cancel_button = QPushButton("Cancel")
        self._cancel_button.setMinimumSize(MIN_TOUCH_TARGET_PX, MIN_TOUCH_TARGET_PX)
        self._cancel_button.clicked.connect(self._on_cancel)
        layout.addWidget(self._cancel_button)

        self._process_button = QPushButton("Process && Save")
        self._process_button.setMinimumSize(MIN_TOUCH_TARGET_PX, MIN_TOUCH_TARGET_PX)
        self._process_button.clicked.connect(self._on_process_and_save)
        layout.addWidget(self._process_button)

        return layout

    # --- Event Handlers ---

    def _on_browse(self) -> None:
        """Handle browse button click to select a source CSV file."""
        file_path, _ = QFileDialog.getOpenFileName(
            self,
            "Select Source CSV File",
            "",
            "CSV Files (*.csv);;All Files (*)",
        )
        if file_path:
            self._load_source_file(Path(file_path))

    def _load_source_file(self, path: Path) -> None:
        """Load a source CSV file and populate the channel list.

        Args:
            path: Path to the CSV file to load.
        """
        if not path.exists():
            QMessageBox.warning(
                self, "File Not Found", f"The file does not exist:\n{path}"
            )
            return

        self._source_path = path
        self._source_path_edit.setText(str(path))

        # Parse the CSV to get channel information
        try:
            _, _, data_rows = self.post_processor._parse_csv(path)
            self._channel_data = self.post_processor._organize_by_channel(data_rows)
            self._sample_rates = self.post_processor._estimate_sample_rates(
                self._channel_data
            )
        except Exception as e:
            QMessageBox.warning(
                self, "Parse Error", f"Failed to parse CSV file:\n{e}"
            )
            return

        # Populate channel list
        self._channel_list.clear()
        for channel_id in sorted(self._channel_data.keys()):
            item = QListWidgetItem(channel_id)
            self._channel_list.addItem(item)

        # Update Nyquist label based on minimum sample rate
        if self._sample_rates:
            min_rate = min(self._sample_rates.values())
            nyquist = min_rate / 2.0
            self._nyquist_label.setText(f"Nyquist: {nyquist:.1f} Hz")
            self._cutoff_spinbox.setMaximum(nyquist)
        else:
            self._nyquist_label.setText("Nyquist: --")

        # Clear preview
        self._raw_curve.setData([], [])
        self._processed_curve.setData([], [])

    def _on_preview(self) -> None:
        """Handle preview button click to compute and display processed data."""
        errors = self._validate_parameters()
        if errors:
            QMessageBox.warning(
                self, "Invalid Parameters", "\n".join(errors)
            )
            return

        selected_channels = self._get_selected_channels()
        if not selected_channels:
            QMessageBox.warning(
                self,
                "No Channels Selected",
                "Please select at least one channel to preview.",
            )
            return

        # Use the first selected channel for preview
        preview_channel = selected_channels[0]
        if preview_channel not in self._channel_data:
            return

        timestamps, raw_values, _, _ = self._channel_data[preview_channel]

        # Convert timestamps from ms to seconds for display
        time_seconds = timestamps / 1000.0

        # Apply processing to get preview
        processed_values = raw_values.copy()

        cutoff_hz = self._cutoff_spinbox.value()
        window_size = self._window_spinbox.value()
        sample_rate = self._sample_rates.get(preview_channel, 100.0)

        if cutoff_hz > 0:
            try:
                processed_values = self.post_processor.low_pass_filter(
                    processed_values, cutoff_hz, sample_rate
                )
            except ValueError as e:
                QMessageBox.warning(
                    self, "Filter Error", f"Low-pass filter error:\n{e}"
                )
                return

        if window_size >= 3:
            try:
                processed_values = self.post_processor.moving_average(
                    processed_values, window_size
                )
            except ValueError as e:
                QMessageBox.warning(
                    self, "Filter Error", f"Moving average error:\n{e}"
                )
                return

        # Update preview plot
        # Filter out NaN for plotting
        raw_valid = ~np.isnan(raw_values)
        proc_valid = ~np.isnan(processed_values)

        self._raw_curve.setData(
            time_seconds[raw_valid].tolist(),
            raw_values[raw_valid].tolist(),
        )
        self._processed_curve.setData(
            time_seconds[proc_valid].tolist(),
            processed_values[proc_valid].tolist(),
        )

        # Update plot title
        self._preview_plot.setTitle(
            f"Preview: {preview_channel}", color="w", size="10pt"
        )

    def _on_process_and_save(self) -> None:
        """Handle Process & Save button click."""
        errors = self._validate_parameters()
        if errors:
            QMessageBox.warning(
                self, "Invalid Parameters", "\n".join(errors)
            )
            return

        if self._source_path is None:
            QMessageBox.warning(
                self, "No Source File", "Please select a source CSV file first."
            )
            return

        selected_channels = self._get_selected_channels()
        if not selected_channels:
            QMessageBox.warning(
                self,
                "No Channels Selected",
                "Please select at least one channel to process.",
            )
            return

        # Build PostProcessConfig
        config = self._build_config(selected_channels)

        try:
            output_path = self.post_processor.process_and_save(
                self._source_path, config
            )
            QMessageBox.information(
                self,
                "Processing Complete",
                f"Processed data saved to:\n{output_path}",
            )
        except (ValueError, FileNotFoundError) as e:
            QMessageBox.warning(
                self, "Processing Error", f"Failed to process data:\n{e}"
            )

    def _on_cancel(self) -> None:
        """Handle Cancel button click. Resets the panel state."""
        self._source_path = None
        self._source_path_edit.clear()
        self._channel_list.clear()
        self._channel_data.clear()
        self._sample_rates.clear()
        self._cutoff_spinbox.setValue(0.0)
        self._window_spinbox.setValue(0)
        self._egt_spread_checkbox.setChecked(False)
        self._rate_of_change_checkbox.setChecked(False)
        self._raw_curve.setData([], [])
        self._processed_curve.setData([], [])
        self._preview_plot.setTitle("")
        self._nyquist_label.setText("Nyquist: --")

    # --- Validation ---

    def _validate_parameters(self) -> list[str]:
        """Validate filter parameters against constraints.

        Returns:
            List of error messages. Empty list means all parameters are valid.
        """
        errors: list[str] = []

        cutoff_hz = self._cutoff_spinbox.value()
        window_size = self._window_spinbox.value()

        # Validate cutoff frequency
        if cutoff_hz > 0:
            if cutoff_hz < 0.1:
                errors.append(
                    "Cutoff frequency must be at least 0.1 Hz."
                )

            # Check against Nyquist for selected channels
            selected_channels = self._get_selected_channels()
            for channel_id in selected_channels:
                sample_rate = self._sample_rates.get(channel_id, 100.0)
                nyquist = sample_rate / 2.0
                if cutoff_hz >= nyquist:
                    errors.append(
                        f"Cutoff frequency ({cutoff_hz} Hz) must be less than "
                        f"Nyquist frequency ({nyquist:.1f} Hz) for channel "
                        f"'{channel_id}' (sample rate: {sample_rate:.1f} Hz)."
                    )
                    break  # One error is enough

        # Validate window size
        if window_size != 0 and (window_size < 3 or window_size > 101):
            errors.append(
                "Moving average window size must be between 3 and 101."
            )

        return errors

    # --- Helpers ---

    def _get_selected_channels(self) -> list[str]:
        """Get the list of selected channel IDs.

        Returns:
            List of channel ID strings that are currently selected.
        """
        selected_items = self._channel_list.selectedItems()
        return [item.text() for item in selected_items]

    def _build_config(self, selected_channels: list[str]) -> PostProcessConfig:
        """Build a PostProcessConfig from the current UI state.

        Args:
            selected_channels: List of channel IDs to process.

        Returns:
            PostProcessConfig with the current parameter values.
        """
        cutoff_hz = self._cutoff_spinbox.value()
        window_size = self._window_spinbox.value()

        # Determine rate-of-change channels
        rate_channels: list[str] = []
        if self._rate_of_change_checkbox.isChecked():
            rate_channels = selected_channels.copy()

        config = PostProcessConfig(
            source_path=self._source_path or Path("."),
            channels_to_process=selected_channels,
            low_pass_cutoff_hz=cutoff_hz if cutoff_hz > 0 else None,
            moving_average_window=window_size if window_size >= 3 else None,
            calculate_egt_spread=self._egt_spread_checkbox.isChecked(),
            calculate_rate_of_change=rate_channels,
        )

        return config

    # --- Public API ---

    def set_source_file(self, path: Path) -> None:
        """Programmatically set the source file path.

        Useful for integration with run management or other panels.

        Args:
            path: Path to the CSV file to load.
        """
        self._load_source_file(path)

    @property
    def source_path(self) -> Optional[Path]:
        """The currently loaded source file path."""
        return self._source_path

    @property
    def cutoff_frequency(self) -> float:
        """The current cutoff frequency value from the spinbox."""
        return self._cutoff_spinbox.value()

    @property
    def window_size(self) -> int:
        """The current window size value from the spinbox."""
        return self._window_spinbox.value()

    @property
    def egt_spread_enabled(self) -> bool:
        """Whether EGT spread calculation is enabled."""
        return self._egt_spread_checkbox.isChecked()

    @property
    def rate_of_change_enabled(self) -> bool:
        """Whether rate of change calculation is enabled."""
        return self._rate_of_change_checkbox.isChecked()
