"""Run Management Panel - start/stop runs, view run log, manage tags.

Implements Requirements 13.1, 13.2, 13.5 for the dashboard UI:
- Start/Stop run buttons with minimum 45x45px touch targets
- Run name input (QLineEdit, max 100 chars)
- Notes input (QTextEdit, max 1000 chars)
- Run log table with filtering (name search, date range, tags)
- Tag management for completed runs
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from PyQt6.QtCore import Qt, QDate
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import (
    QComboBox,
    QDateEdit,
    QDialog,
    QDialogButtonBox,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSizePolicy,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from rotax_dyno_daq.storage.run_manager import RunFilters, RunManager

# Minimum touch target size in pixels (12mm × 12mm at 96 DPI ≈ 45×45 px)
MIN_TOUCH_TARGET_PX = 45


class RunStartDialog(QDialog):
    """Dialog for starting a new run with name and notes input.

    Validates:
    - Run name: 1-100 characters, non-empty
    - Notes: up to 1000 characters
    """

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Start New Run")
        self.setMinimumWidth(400)
        # Generate default run name from current timestamp
        self._default_name = datetime.now().strftime("log_%Y%m%d_%H%M%S")
        self._setup_ui()

    def _setup_ui(self) -> None:
        """Set up the dialog layout."""
        layout = QVBoxLayout(self)
        layout.setSpacing(12)

        # Run name input
        name_label = QLabel("Run Name (1-100 characters):")
        name_label.setFont(QFont("", 10))
        layout.addWidget(name_label)

        self._name_input = QLineEdit()
        self._name_input.setMaxLength(100)
        self._name_input.setText(self._default_name)
        self._name_input.setPlaceholderText("Enter run name...")
        self._name_input.setMinimumHeight(MIN_TOUCH_TARGET_PX)
        layout.addWidget(self._name_input)

        # Notes input
        notes_label = QLabel("Notes (optional, up to 1000 characters):")
        notes_label.setFont(QFont("", 10))
        layout.addWidget(notes_label)

        self._notes_input = QTextEdit()
        self._notes_input.setPlaceholderText(
            "Engine config, ambient conditions, etc..."
        )
        self._notes_input.setMaximumHeight(150)
        layout.addWidget(self._notes_input)

        # Character count label for notes
        self._notes_count_label = QLabel("0 / 1000")
        self._notes_count_label.setAlignment(Qt.AlignmentFlag.AlignRight)
        self._notes_input.textChanged.connect(self._update_notes_count)
        layout.addWidget(self._notes_count_label)

        # Dialog buttons
        button_box = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        button_box.accepted.connect(self._validate_and_accept)
        button_box.rejected.connect(self.reject)

        # Ensure buttons meet touch target size
        for button in button_box.buttons():
            button.setMinimumSize(MIN_TOUCH_TARGET_PX, MIN_TOUCH_TARGET_PX)

        layout.addWidget(button_box)

    def _update_notes_count(self) -> None:
        """Update the character count label for notes."""
        count = len(self._notes_input.toPlainText())
        self._notes_count_label.setText(f"{count} / 1000")
        if count > 1000:
            self._notes_count_label.setStyleSheet("QLabel { color: red; }")
        else:
            self._notes_count_label.setStyleSheet("")

    def _validate_and_accept(self) -> None:
        """Validate inputs and accept the dialog if valid."""
        name = self._name_input.text().strip()
        notes = self._notes_input.toPlainText()

        if not name:
            QMessageBox.warning(
                self, "Validation Error", "Run name must not be empty."
            )
            return

        if len(name) > 100:
            QMessageBox.warning(
                self,
                "Validation Error",
                f"Run name must be 1-100 characters (got {len(name)}).",
            )
            return

        if len(notes) > 1000:
            QMessageBox.warning(
                self,
                "Validation Error",
                f"Notes must be up to 1000 characters (got {len(notes)}).",
            )
            return

        self.accept()

    @property
    def run_name(self) -> str:
        """Return the entered run name (stripped)."""
        return self._name_input.text().strip()

    @property
    def run_notes(self) -> str:
        """Return the entered notes."""
        return self._notes_input.toPlainText()

    @property
    def name_input(self) -> QLineEdit:
        """Access the name input widget (for testing)."""
        return self._name_input

    @property
    def notes_input(self) -> QTextEdit:
        """Access the notes input widget (for testing)."""
        return self._notes_input


class RunPanel(QWidget):
    """Run management panel with start/stop controls, run log, and filtering.

    Provides:
    - Start/Stop run buttons (minimum 45x45px touch targets)
    - Run log table showing completed runs with summary stats
    - Filtering by name, date range, and tags
    - Tag management for completed runs
    """

    def __init__(
        self,
        run_manager: RunManager,
        parent: Optional[QWidget] = None,
    ) -> None:
        """Initialize the run management panel.

        Args:
            run_manager: The RunManager instance for run lifecycle operations.
            parent: Optional parent widget.
        """
        super().__init__(parent)
        self._run_manager = run_manager
        self._setup_ui()
        self._refresh_run_log()

    def _setup_ui(self) -> None:
        """Set up the panel layout."""
        layout = QVBoxLayout(self)
        layout.setSpacing(8)

        # --- Run Controls Section ---
        controls_group = QGroupBox("Run Controls")
        controls_layout = QHBoxLayout(controls_group)

        self._start_button = QPushButton("▶ Start Run")
        self._start_button.setMinimumSize(MIN_TOUCH_TARGET_PX, MIN_TOUCH_TARGET_PX)
        self._start_button.setStyleSheet(
            "QPushButton { background-color: #2e7d32; color: white; "
            "font-weight: bold; padding: 8px 16px; border-radius: 4px; }"
            "QPushButton:hover { background-color: #388e3c; }"
        )
        self._start_button.clicked.connect(self._on_start_run)
        controls_layout.addWidget(self._start_button)

        self._stop_button = QPushButton("■ Stop Run")
        self._stop_button.setMinimumSize(MIN_TOUCH_TARGET_PX, MIN_TOUCH_TARGET_PX)
        self._stop_button.setEnabled(False)
        self._stop_button.setStyleSheet(
            "QPushButton { background-color: #c62828; color: white; "
            "font-weight: bold; padding: 8px 16px; border-radius: 4px; }"
            "QPushButton:hover { background-color: #d32f2f; }"
            "QPushButton:disabled { background-color: #555; color: #999; }"
        )
        self._stop_button.clicked.connect(self._on_stop_run)
        controls_layout.addWidget(self._stop_button)

        # Active run status label
        self._status_label = QLabel("No active run")
        self._status_label.setFont(QFont("", 10))
        controls_layout.addWidget(self._status_label, stretch=1)

        layout.addWidget(controls_group)

        # --- Filter Section ---
        filter_group = QGroupBox("Filter Run Log")
        filter_layout = QHBoxLayout(filter_group)

        # Name filter
        filter_layout.addWidget(QLabel("Name:"))
        self._name_filter = QLineEdit()
        self._name_filter.setPlaceholderText("Search by name...")
        self._name_filter.setMinimumHeight(MIN_TOUCH_TARGET_PX)
        self._name_filter.textChanged.connect(self._on_filter_changed)
        filter_layout.addWidget(self._name_filter)

        # Date range filter
        filter_layout.addWidget(QLabel("From:"))
        self._date_from = QDateEdit()
        self._date_from.setCalendarPopup(True)
        self._date_from.setMinimumHeight(MIN_TOUCH_TARGET_PX)
        self._date_from.setDate(QDate.currentDate().addMonths(-1))
        self._date_from.setSpecialValueText("Any")
        self._date_from.dateChanged.connect(self._on_filter_changed)
        filter_layout.addWidget(self._date_from)

        filter_layout.addWidget(QLabel("To:"))
        self._date_to = QDateEdit()
        self._date_to.setCalendarPopup(True)
        self._date_to.setMinimumHeight(MIN_TOUCH_TARGET_PX)
        self._date_to.setDate(QDate.currentDate())
        self._date_to.setSpecialValueText("Any")
        self._date_to.dateChanged.connect(self._on_filter_changed)
        filter_layout.addWidget(self._date_to)

        # Tag filter
        filter_layout.addWidget(QLabel("Tag:"))
        self._tag_filter = QLineEdit()
        self._tag_filter.setPlaceholderText("Filter by tag...")
        self._tag_filter.setMinimumHeight(MIN_TOUCH_TARGET_PX)
        self._tag_filter.textChanged.connect(self._on_filter_changed)
        filter_layout.addWidget(self._tag_filter)

        # Refresh button
        self._refresh_button = QPushButton("Refresh")
        self._refresh_button.setMinimumSize(MIN_TOUCH_TARGET_PX, MIN_TOUCH_TARGET_PX)
        self._refresh_button.clicked.connect(self._refresh_run_log)
        filter_layout.addWidget(self._refresh_button)

        layout.addWidget(filter_group)

        # --- Run Log Table ---
        self._run_table = QTableWidget()
        self._run_table.setColumnCount(6)
        self._run_table.setHorizontalHeaderLabels(
            ["Name", "Date", "Duration", "Notes", "Tags", "Run ID"]
        )
        self._run_table.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.Stretch
        )
        self._run_table.setSelectionBehavior(
            QTableWidget.SelectionBehavior.SelectRows
        )
        self._run_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._run_table.verticalHeader().setDefaultSectionSize(MIN_TOUCH_TARGET_PX)
        layout.addWidget(self._run_table, stretch=1)

        # --- Tag Management Section ---
        tag_group = QGroupBox("Tag Management")
        tag_layout = QHBoxLayout(tag_group)

        tag_layout.addWidget(QLabel("Add Tag:"))
        self._tag_input = QLineEdit()
        self._tag_input.setMaxLength(50)
        self._tag_input.setPlaceholderText("Tag (up to 50 chars)...")
        self._tag_input.setMinimumHeight(MIN_TOUCH_TARGET_PX)
        tag_layout.addWidget(self._tag_input)

        self._add_tag_button = QPushButton("Add Tag")
        self._add_tag_button.setMinimumSize(MIN_TOUCH_TARGET_PX, MIN_TOUCH_TARGET_PX)
        self._add_tag_button.clicked.connect(self._on_add_tag)
        tag_layout.addWidget(self._add_tag_button)

        layout.addWidget(tag_group)

    def _on_start_run(self) -> None:
        """Handle Start Run button click - show dialog and start run."""
        dialog = RunStartDialog(self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            name = dialog.run_name
            notes = dialog.run_notes
            try:
                self._run_manager.start_run(name, notes)
                self._start_button.setEnabled(False)
                self._stop_button.setEnabled(True)
                self._status_label.setText(f"Active run: {name}")
            except Exception as e:
                QMessageBox.critical(
                    self, "Error Starting Run", str(e)
                )

    def _on_stop_run(self) -> None:
        """Handle Stop Run button click - stop the active run."""
        try:
            self._run_manager.stop_run()
            self._start_button.setEnabled(True)
            self._stop_button.setEnabled(False)
            self._status_label.setText("No active run")
            self._refresh_run_log()
        except Exception as e:
            QMessageBox.critical(
                self, "Error Stopping Run", str(e)
            )

    def _on_filter_changed(self) -> None:
        """Handle filter input changes - refresh the run log with filters."""
        self._refresh_run_log()

    def _refresh_run_log(self) -> None:
        """Refresh the run log table with current filters applied."""
        filters = self._build_filters()
        runs = self._run_manager.get_run_log(filters)

        self._run_table.setRowCount(len(runs))
        for row, run in enumerate(runs):
            self._run_table.setItem(row, 0, QTableWidgetItem(run.name))
            self._run_table.setItem(
                row, 1, QTableWidgetItem(run.start_time.strftime("%Y-%m-%d %H:%M"))
            )
            duration_str = self._format_duration(run.duration_seconds)
            self._run_table.setItem(row, 2, QTableWidgetItem(duration_str))
            # Truncate notes for display
            notes_display = run.notes[:80] + "..." if len(run.notes) > 80 else run.notes
            self._run_table.setItem(row, 3, QTableWidgetItem(notes_display))
            self._run_table.setItem(
                row, 4, QTableWidgetItem(", ".join(run.tags))
            )
            self._run_table.setItem(row, 5, QTableWidgetItem(run.run_id))

    def _build_filters(self) -> RunFilters:
        """Build RunFilters from the current filter inputs."""
        name_sub = self._name_filter.text().strip() or None

        # Date range
        start_date: Optional[datetime] = None
        end_date: Optional[datetime] = None

        from_date = self._date_from.date()
        to_date = self._date_to.date()

        if from_date.isValid():
            start_date = datetime(from_date.year(), from_date.month(), from_date.day())
        if to_date.isValid():
            end_date = datetime(
                to_date.year(), to_date.month(), to_date.day(), 23, 59, 59
            )

        # Tags
        tag_text = self._tag_filter.text().strip()
        tags = [t.strip() for t in tag_text.split(",") if t.strip()] or None

        return RunFilters(
            name_substring=name_sub,
            start_date=start_date,
            end_date=end_date,
            tags=tags,
        )

    def _on_add_tag(self) -> None:
        """Handle Add Tag button click - add tag to selected run."""
        tag = self._tag_input.text().strip()
        if not tag:
            QMessageBox.warning(self, "Validation Error", "Tag must not be empty.")
            return

        # Get selected run
        selected_rows = self._run_table.selectionModel().selectedRows()
        if not selected_rows:
            QMessageBox.warning(
                self, "No Selection", "Please select a run to tag."
            )
            return

        row = selected_rows[0].row()
        run_id_item = self._run_table.item(row, 5)
        if run_id_item is None:
            return

        run_id = run_id_item.text()
        try:
            self._run_manager.tag_run(run_id, [tag])
            self._tag_input.clear()
            self._refresh_run_log()
        except Exception as e:
            QMessageBox.critical(self, "Error Adding Tag", str(e))

    @staticmethod
    def _format_duration(seconds: float) -> str:
        """Format duration in seconds to a human-readable string."""
        total_secs = int(seconds)
        hours = total_secs // 3600
        minutes = (total_secs % 3600) // 60
        secs = total_secs % 60
        if hours > 0:
            return f"{hours}h {minutes}m {secs}s"
        elif minutes > 0:
            return f"{minutes}m {secs}s"
        else:
            return f"{secs}s"

    @property
    def start_button(self) -> QPushButton:
        """Access the start button (for testing)."""
        return self._start_button

    @property
    def stop_button(self) -> QPushButton:
        """Access the stop button (for testing)."""
        return self._stop_button

    @property
    def run_table(self) -> QTableWidget:
        """Access the run log table (for testing)."""
        return self._run_table

    @property
    def name_filter(self) -> QLineEdit:
        """Access the name filter input (for testing)."""
        return self._name_filter

    @property
    def tag_filter(self) -> QLineEdit:
        """Access the tag filter input (for testing)."""
        return self._tag_filter

    @property
    def run_manager(self) -> RunManager:
        """Access the run manager (for testing)."""
        return self._run_manager
