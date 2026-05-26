"""Run Manager - Manages run lifecycle, metadata, and the run log.

Implements Requirements 13.1, 13.2, 13.3, 13.4, 13.5, 13.6.
"""

from __future__ import annotations

import csv
import json
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional, Protocol

from rotax_dyno_daq.core.enums import UploadStatus
from rotax_dyno_daq.core.models import RunInfo, RunSummary


# --- Filter Model ---


@dataclass
class RunFilters:
    """Filters for querying the run log."""

    name_substring: Optional[str] = None
    start_date: Optional[datetime] = None
    end_date: Optional[datetime] = None
    tags: Optional[list[str]] = None
    page: int = 1
    page_size: int = 50


# --- Protocols for dependencies ---


class CsvLoggerProtocol(Protocol):
    """Protocol for CSV logger dependency."""

    def start_run(self, run_info: RunInfo) -> None: ...
    def stop_run(self) -> RunSummary: ...


class CloudUploaderProtocol(Protocol):
    """Protocol for cloud uploader dependency."""

    def queue_upload(self, file_path: Path) -> None: ...


# --- Run Manager ---


class RunManagerError(Exception):
    """Base exception for RunManager errors."""

    pass


class RunValidationError(RunManagerError):
    """Raised when run metadata validation fails."""

    pass


class RunNotFoundError(RunManagerError):
    """Raised when a run_id is not found in the run log."""

    pass


class NoActiveRunError(RunManagerError):
    """Raised when stop_run is called with no active run."""

    pass


class RunAlreadyActiveError(RunManagerError):
    """Raised when start_run is called while a run is already active."""

    pass


class RunManager:
    """Manages run lifecycle, metadata, and the run log.

    Orchestrates starting/stopping runs, maintains a persistent run log,
    supports filtering, tagging, and CSV export of run data.
    """

    def __init__(
        self,
        run_log_path: Path,
        csv_logger: Optional[CsvLoggerProtocol] = None,
        cloud_uploader: Optional[CloudUploaderProtocol] = None,
    ) -> None:
        """Initialize RunManager.

        Args:
            run_log_path: Path to the JSON file storing the run log.
            csv_logger: Optional CSV logger for recording run data.
            cloud_uploader: Optional cloud uploader for queuing uploads.
        """
        self._run_log_path = run_log_path
        self._csv_logger = csv_logger
        self._cloud_uploader = cloud_uploader
        self._active_run: Optional[_ActiveRun] = None
        self._run_log: list[RunSummary] = []
        self._load_run_log()

    # --- Public API ---

    def start_run(self, name: str, notes: str = "") -> RunInfo:
        """Start a new recording run.

        Args:
            name: Run name, 1-100 characters, must not duplicate existing names.
            notes: Optional notes, up to 1000 characters.

        Returns:
            RunInfo with the validated metadata and generated run_id.

        Raises:
            RunValidationError: If name or notes fail validation.
            RunAlreadyActiveError: If a run is already in progress.
        """
        if self._active_run is not None:
            raise RunAlreadyActiveError("A run is already active. Stop it before starting a new one.")

        self._validate_run_name(name)
        self._validate_notes(notes)

        run_id = str(uuid.uuid4())
        start_time = datetime.now()

        run_info = RunInfo(name=name, notes=notes)

        self._active_run = _ActiveRun(
            run_id=run_id,
            name=name,
            notes=notes,
            start_time=start_time,
        )

        if self._csv_logger is not None:
            self._csv_logger.start_run(run_info)

        return run_info

    def stop_run(self) -> RunSummary:
        """Stop the active run and finalize.

        Returns:
            RunSummary with run statistics.

        Raises:
            NoActiveRunError: If no run is currently active.
        """
        if self._active_run is None:
            raise NoActiveRunError("No active run to stop.")

        end_time = datetime.now()
        duration = (end_time - self._active_run.start_time).total_seconds()

        # Get summary from CSV logger if available
        csv_path: Optional[Path] = None
        sample_counts: dict[str, int] = {}
        min_values: dict[str, float] = {}
        max_values: dict[str, float] = {}
        mean_values: dict[str, float] = {}

        if self._csv_logger is not None:
            logger_summary = self._csv_logger.stop_run()
            csv_path = logger_summary.csv_path
            sample_counts = logger_summary.sample_counts
            min_values = logger_summary.min_values
            max_values = logger_summary.max_values
            mean_values = logger_summary.mean_values

        summary = RunSummary(
            run_id=self._active_run.run_id,
            name=self._active_run.name,
            start_time=self._active_run.start_time,
            end_time=end_time,
            duration_seconds=duration,
            sample_counts=sample_counts,
            min_values=min_values,
            max_values=max_values,
            mean_values=mean_values,
            notes=self._active_run.notes,
            tags=list(self._active_run.tags),
            csv_path=csv_path,
            upload_status=UploadStatus.PENDING,
        )

        self._run_log.append(summary)
        self._save_run_log()

        # Queue cloud upload if uploader and csv_path are available
        if self._cloud_uploader is not None and csv_path is not None:
            self._cloud_uploader.queue_upload(csv_path)

        self._active_run = None
        return summary

    def get_run_log(self, filters: Optional[RunFilters] = None) -> list[RunSummary]:
        """Query the run log with optional filters.

        Args:
            filters: Optional filters for name, date range, tags, and pagination.

        Returns:
            List of RunSummary objects matching filters, sorted by date descending.
        """
        results = list(self._run_log)

        if filters is not None:
            results = self._apply_filters(results, filters)

        # Sort by start_time descending
        results.sort(key=lambda r: r.start_time, reverse=True)

        # Apply pagination
        if filters is not None:
            page = max(1, filters.page)
            page_size = max(1, filters.page_size)
            start_idx = (page - 1) * page_size
            end_idx = start_idx + page_size
            results = results[start_idx:end_idx]

        return results

    def tag_run(self, run_id: str, tags: list[str]) -> None:
        """Add tags to a completed run.

        Args:
            run_id: The unique identifier of the run to tag.
            tags: List of tags to add (up to 10 total, each up to 50 chars).

        Raises:
            RunNotFoundError: If run_id is not found.
            RunValidationError: If tags fail validation.
        """
        run = self._find_run(run_id)
        self._validate_tags(tags, existing_tags=run.tags)

        # Add new tags (avoid duplicates)
        for tag in tags:
            if tag not in run.tags:
                run.tags.append(tag)

        self._save_run_log()

    def export_run(self, run_id: str, output_path: Path) -> None:
        """Export a run as a standardized CSV with ISO 8601 timestamps.

        Args:
            run_id: The unique identifier of the run to export.
            output_path: Path where the CSV file will be written.

        Raises:
            RunNotFoundError: If run_id is not found.
            RunManagerError: If the run has no CSV data to export.
        """
        run = self._find_run(run_id)

        if run.csv_path is None or not run.csv_path.exists():
            raise RunManagerError(
                f"No CSV data available for run '{run.name}' (id: {run_id})."
            )

        # Read source CSV and re-export with ISO 8601 timestamps
        self._export_csv_with_iso_timestamps(run.csv_path, output_path, run)

    @property
    def active_run(self) -> Optional["_ActiveRun"]:
        """Return the currently active run, or None."""
        return self._active_run

    # --- Validation ---

    def _validate_run_name(self, name: str) -> None:
        """Validate run name: 1-100 chars, no duplicates."""
        if not name or not name.strip():
            raise RunValidationError("Run name must not be empty.")

        if len(name) > 100:
            raise RunValidationError(
                f"Run name must be 1-100 characters (got {len(name)})."
            )

        # Check for duplicates in run log
        existing_names = {r.name for r in self._run_log}
        if name in existing_names:
            raise RunValidationError(
                f"Run name '{name}' already exists. Names must be unique."
            )

    def _validate_notes(self, notes: str) -> None:
        """Validate notes: up to 1000 characters."""
        if len(notes) > 1000:
            raise RunValidationError(
                f"Notes must be up to 1000 characters (got {len(notes)})."
            )

    def _validate_tags(self, new_tags: list[str], existing_tags: list[str]) -> None:
        """Validate tags: up to 10 total per run, each up to 50 chars."""
        # Count how many unique tags would result
        combined = set(existing_tags)
        for tag in new_tags:
            combined.add(tag)

        if len(combined) > 10:
            raise RunValidationError(
                f"A run can have at most 10 tags (would have {len(combined)})."
            )

        for tag in new_tags:
            if not tag or not tag.strip():
                raise RunValidationError("Tags must not be empty.")
            if len(tag) > 50:
                raise RunValidationError(
                    f"Each tag must be up to 50 characters (got {len(tag)})."
                )

    # --- Filtering ---

    def _apply_filters(
        self, runs: list[RunSummary], filters: RunFilters
    ) -> list[RunSummary]:
        """Apply filter criteria to a list of runs."""
        results = runs

        if filters.name_substring:
            substring = filters.name_substring.lower()
            results = [r for r in results if substring in r.name.lower()]

        if filters.start_date:
            results = [r for r in results if r.start_time >= filters.start_date]

        if filters.end_date:
            results = [r for r in results if r.start_time <= filters.end_date]

        if filters.tags:
            # Match runs that have ANY of the specified tags
            filter_tags = set(filters.tags)
            results = [r for r in results if filter_tags & set(r.tags)]

        return results

    # --- Persistence ---

    def _load_run_log(self) -> None:
        """Load the run log from the JSON file."""
        if not self._run_log_path.exists():
            self._run_log = []
            return

        try:
            data = json.loads(self._run_log_path.read_text(encoding="utf-8"))
            self._run_log = [self._deserialize_run_summary(entry) for entry in data]
        except (json.JSONDecodeError, KeyError, ValueError):
            self._run_log = []

    def _save_run_log(self) -> None:
        """Persist the run log to the JSON file."""
        self._run_log_path.parent.mkdir(parents=True, exist_ok=True)
        data = [self._serialize_run_summary(run) for run in self._run_log]
        self._run_log_path.write_text(
            json.dumps(data, indent=2, default=str), encoding="utf-8"
        )

    def _serialize_run_summary(self, run: RunSummary) -> dict:
        """Serialize a RunSummary to a JSON-compatible dict."""
        return {
            "run_id": run.run_id,
            "name": run.name,
            "start_time": run.start_time.isoformat(),
            "end_time": run.end_time.isoformat(),
            "duration_seconds": run.duration_seconds,
            "sample_counts": run.sample_counts,
            "min_values": run.min_values,
            "max_values": run.max_values,
            "mean_values": run.mean_values,
            "notes": run.notes,
            "tags": run.tags,
            "csv_path": str(run.csv_path) if run.csv_path else None,
            "upload_status": run.upload_status.value,
        }

    def _deserialize_run_summary(self, data: dict) -> RunSummary:
        """Deserialize a dict to a RunSummary."""
        return RunSummary(
            run_id=data["run_id"],
            name=data["name"],
            start_time=datetime.fromisoformat(data["start_time"]),
            end_time=datetime.fromisoformat(data["end_time"]),
            duration_seconds=data["duration_seconds"],
            sample_counts=data.get("sample_counts", {}),
            min_values=data.get("min_values", {}),
            max_values=data.get("max_values", {}),
            mean_values=data.get("mean_values", {}),
            notes=data.get("notes", ""),
            tags=data.get("tags", []),
            csv_path=Path(data["csv_path"]) if data.get("csv_path") else None,
            upload_status=UploadStatus(data.get("upload_status", "pending")),
        )

    # --- Helpers ---

    def _find_run(self, run_id: str) -> RunSummary:
        """Find a run by ID in the run log."""
        for run in self._run_log:
            if run.run_id == run_id:
                return run
        raise RunNotFoundError(f"Run with id '{run_id}' not found.")

    def _export_csv_with_iso_timestamps(
        self, source_path: Path, output_path: Path, run: RunSummary
    ) -> None:
        """Export run data as CSV with ISO 8601 timestamp column.

        Reads the source CSV (which has millisecond timestamps relative to run start)
        and writes a new CSV with an ISO 8601 timestamp column and one column per channel.
        """
        output_path.parent.mkdir(parents=True, exist_ok=True)

        with open(source_path, "r", newline="", encoding="utf-8") as infile:
            # Skip header metadata lines (lines starting with #)
            data_lines = []
            header_line = None
            for line in infile:
                if line.startswith("#"):
                    continue
                if header_line is None:
                    header_line = line.strip()
                    continue
                data_lines.append(line.strip())

        if header_line is None:
            raise RunManagerError(f"Source CSV '{source_path}' has no data header.")

        source_columns = header_line.split(",")

        # Find timestamp column (typically first column)
        timestamp_col_idx = 0
        for i, col in enumerate(source_columns):
            if "timestamp" in col.lower():
                timestamp_col_idx = i
                break

        # Build output columns: ISO timestamp + channel columns
        channel_columns = [
            col for i, col in enumerate(source_columns) if i != timestamp_col_idx
        ]
        output_columns = ["timestamp_iso8601"] + channel_columns

        with open(output_path, "w", newline="", encoding="utf-8") as outfile:
            writer = csv.writer(outfile)
            writer.writerow(output_columns)

            for line in data_lines:
                if not line:
                    continue
                values = line.split(",")
                if len(values) != len(source_columns):
                    continue

                # Convert ms timestamp to ISO 8601
                try:
                    timestamp_ms = float(values[timestamp_col_idx])
                    absolute_time = run.start_time.timestamp() + (timestamp_ms / 1000.0)
                    iso_timestamp = datetime.fromtimestamp(absolute_time).isoformat(
                        timespec="milliseconds"
                    )
                except (ValueError, OSError):
                    iso_timestamp = values[timestamp_col_idx]

                # Collect channel values
                channel_values = [
                    values[i]
                    for i in range(len(values))
                    if i != timestamp_col_idx
                ]

                writer.writerow([iso_timestamp] + channel_values)


# --- Internal Active Run State ---


@dataclass
class _ActiveRun:
    """Internal state for a currently active run."""

    run_id: str
    name: str
    notes: str
    start_time: datetime
    tags: list[str] = field(default_factory=list)
