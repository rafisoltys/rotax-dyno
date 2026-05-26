"""CSV Logger for recording sensor data during dyno runs.

Handles file creation, buffered writing with periodic flush,
disk space monitoring, and fallback directory switching on write errors.
"""

import csv
import io
import logging
import shutil
import threading
import uuid
from datetime import datetime
from pathlib import Path
from typing import Callable, Optional

from rotax_dyno_daq.core.enums import SampleValidity, UploadStatus
from rotax_dyno_daq.core.models import CalibratedSample, RunInfo, RunSummary

logger = logging.getLogger(__name__)


class CsvLogger:
    """Manages CSV file creation, writing, and flushing during runs.

    Features:
    - Creates timestamped CSV files with header metadata
    - Buffers samples and flushes at least once per second
    - Monitors disk space and alerts when below threshold
    - Switches to fallback directory on write errors
    """

    def __init__(
        self,
        csv_directory: Path,
        fallback_csv_directory: Optional[Path] = None,
        disk_space_warning_mb: int = 50,
        on_disk_space_warning: Optional[Callable[[int], None]] = None,
        on_write_error: Optional[Callable[[str], None]] = None,
    ) -> None:
        """Initialize the CSV Logger.

        Args:
            csv_directory: Primary directory for CSV file storage.
            fallback_csv_directory: Secondary directory used when primary fails.
            disk_space_warning_mb: Threshold in MB to trigger disk space warning.
            on_disk_space_warning: Callback invoked with remaining MB when space is low.
            on_write_error: Callback invoked with error message on write failures.
        """
        self._csv_directory = csv_directory
        self._fallback_csv_directory = fallback_csv_directory
        self._disk_space_warning_mb = disk_space_warning_mb
        self._on_disk_space_warning = on_disk_space_warning
        self._on_write_error = on_write_error

        # Run state
        self._file: Optional[io.TextIOWrapper] = None
        self._writer: Optional[csv.writer] = None
        self._buffer: list[CalibratedSample] = []
        self._buffer_lock = threading.Lock()
        self._run_info: Optional[RunInfo] = None
        self._start_time: Optional[datetime] = None
        self._csv_path: Optional[Path] = None
        self._active = False

        # Statistics tracking per channel
        self._sample_counts: dict[str, int] = {}
        self._min_values: dict[str, float] = {}
        self._max_values: dict[str, float] = {}
        self._sum_values: dict[str, float] = {}

        # Disk space monitoring
        self._last_disk_check: Optional[datetime] = None
        self._disk_check_interval_seconds = 10
        self._using_fallback = False

    @property
    def is_active(self) -> bool:
        """Whether a run is currently being recorded."""
        return self._active

    @property
    def csv_path(self) -> Optional[Path]:
        """Path to the current CSV file, if a run is active."""
        return self._csv_path

    def start_run(self, run_info: RunInfo) -> None:
        """Create a new CSV file and write the header section.

        Args:
            run_info: Metadata for the run being started.

        Raises:
            RuntimeError: If a run is already active.
            OSError: If the CSV file cannot be created in either directory.
        """
        if self._active:
            raise RuntimeError("A run is already active. Stop the current run first.")

        self._run_info = run_info
        self._start_time = datetime.now()
        self._buffer = []
        self._sample_counts = {}
        self._min_values = {}
        self._max_values = {}
        self._sum_values = {}
        self._using_fallback = False

        # Generate filename: YYYYMMDD_HHMMSS_{run_name}.csv
        timestamp_str = self._start_time.strftime("%Y%m%d_%H%M%S")
        # Sanitize run name for filesystem
        safe_name = self._sanitize_filename(run_info.name)
        filename = f"{timestamp_str}_{safe_name}.csv"

        # Try primary directory first, then fallback
        self._csv_path = self._open_csv_file(filename)
        self._active = True

        # Write header section
        self._write_header(run_info)

    def write_sample(self, sample: CalibratedSample) -> None:
        """Buffer a calibrated sample for writing.

        Args:
            sample: The calibrated sample to record.

        Raises:
            RuntimeError: If no run is currently active.
        """
        if not self._active:
            raise RuntimeError("No active run. Call start_run() first.")

        with self._buffer_lock:
            self._buffer.append(sample)

        # Update statistics
        self._update_statistics(sample)

    def flush(self) -> None:
        """Flush all buffered samples to disk.

        This should be called at least once per second to limit data loss
        to ≤ 1 second of samples on abnormal termination.
        """
        if not self._active or self._file is None:
            return

        # Grab buffered samples
        with self._buffer_lock:
            samples = self._buffer[:]
            self._buffer.clear()

        if not samples:
            # Still check disk space periodically even with no samples
            self._check_disk_space()
            return

        # Write samples to CSV
        try:
            for sample in samples:
                self._writer.writerow([
                    f"{sample.timestamp_ms:.3f}",
                    sample.channel_id,
                    f"{sample.calibrated_value:.6g}",
                    sample.unit,
                    sample.validity.value,
                ])
            self._file.flush()
        except OSError as e:
            error_msg = f"Write error to {self._csv_path}: {e}"
            logger.error(error_msg)
            if self._on_write_error:
                self._on_write_error(error_msg)

            # Attempt to switch to fallback directory
            if not self._using_fallback and self._fallback_csv_directory:
                self._switch_to_fallback(samples)
            else:
                logger.error("No fallback directory available or already using fallback.")

        # Periodic disk space check
        self._check_disk_space()

    def stop_run(self) -> RunSummary:
        """Close the CSV file and append summary metadata.

        Returns:
            RunSummary with statistics for the completed run.

        Raises:
            RuntimeError: If no run is currently active.
        """
        if not self._active:
            raise RuntimeError("No active run to stop.")

        # Flush remaining buffered samples
        self.flush()

        end_time = datetime.now()
        duration_seconds = (end_time - self._start_time).total_seconds()

        # Compute mean values
        mean_values: dict[str, float] = {}
        for channel_id, count in self._sample_counts.items():
            if count > 0:
                mean_values[channel_id] = self._sum_values[channel_id] / count

        # Write summary metadata as comments at end of file
        self._write_summary(end_time, duration_seconds, mean_values)

        # Close the file
        if self._file:
            self._file.close()
            self._file = None
            self._writer = None

        # Build RunSummary
        summary = RunSummary(
            run_id=str(uuid.uuid4()),
            name=self._run_info.name,
            start_time=self._start_time,
            end_time=end_time,
            duration_seconds=duration_seconds,
            sample_counts=dict(self._sample_counts),
            min_values=dict(self._min_values),
            max_values=dict(self._max_values),
            mean_values=mean_values,
            notes=self._run_info.notes,
            tags=list(self._run_info.tags),
            csv_path=self._csv_path,
            upload_status=UploadStatus.PENDING,
        )

        # Reset state
        self._active = False
        self._run_info = None
        self._start_time = None

        return summary

    def _open_csv_file(self, filename: str) -> Path:
        """Open a CSV file in the primary or fallback directory.

        Args:
            filename: The CSV filename to create.

        Returns:
            Path to the opened CSV file.

        Raises:
            OSError: If the file cannot be created in either directory.
        """
        # Try primary directory
        primary_path = self._csv_directory / filename
        try:
            self._csv_directory.mkdir(parents=True, exist_ok=True)
            self._file = open(primary_path, "w", newline="", encoding="utf-8")
            self._writer = csv.writer(self._file)
            return primary_path
        except OSError as e:
            logger.warning(f"Cannot write to primary directory: {e}")
            if self._on_write_error:
                self._on_write_error(
                    f"Cannot write to primary directory {self._csv_directory}: {e}"
                )

        # Try fallback directory
        if self._fallback_csv_directory:
            fallback_path = self._fallback_csv_directory / filename
            try:
                self._fallback_csv_directory.mkdir(parents=True, exist_ok=True)
                self._file = open(fallback_path, "w", newline="", encoding="utf-8")
                self._writer = csv.writer(self._file)
                self._using_fallback = True
                return fallback_path
            except OSError as e2:
                raise OSError(
                    f"Cannot write to primary ({self._csv_directory}) "
                    f"or fallback ({self._fallback_csv_directory}) directory: {e2}"
                ) from e2

        raise OSError(
            f"Cannot write to primary directory ({self._csv_directory}) "
            f"and no fallback directory configured."
        )

    def _write_header(self, run_info: RunInfo) -> None:
        """Write the CSV header section with run metadata.

        Args:
            run_info: Run metadata to include in the header.
        """
        if self._writer is None:
            return

        # Write metadata as comment rows
        self._writer.writerow(["# Run Name", run_info.name])
        self._writer.writerow(["# Start Time", self._start_time.isoformat()])
        self._writer.writerow(["# Operator", run_info.operator])
        self._writer.writerow(["# Notes", run_info.notes])
        if run_info.tags:
            self._writer.writerow(["# Tags", ";".join(run_info.tags)])

        # Write column headers
        self._writer.writerow([
            "timestamp_ms",
            "channel_id",
            "calibrated_value",
            "unit",
            "validity",
        ])
        self._file.flush()

    def _write_summary(
        self,
        end_time: datetime,
        duration_seconds: float,
        mean_values: dict[str, float],
    ) -> None:
        """Append summary metadata at the end of the CSV file.

        Args:
            end_time: When the run ended.
            duration_seconds: Total run duration in seconds.
            mean_values: Mean values per channel.
        """
        if self._writer is None:
            return

        self._writer.writerow([])
        self._writer.writerow(["# --- Run Summary ---"])
        self._writer.writerow(["# End Time", end_time.isoformat()])
        self._writer.writerow(["# Duration (s)", f"{duration_seconds:.3f}"])

        for channel_id in sorted(self._sample_counts.keys()):
            count = self._sample_counts[channel_id]
            min_val = self._min_values.get(channel_id, "N/A")
            max_val = self._max_values.get(channel_id, "N/A")
            mean_val = mean_values.get(channel_id, "N/A")
            self._writer.writerow([
                f"# Channel: {channel_id}",
                f"samples={count}",
                f"min={min_val}",
                f"max={max_val}",
                f"mean={mean_val}",
            ])

        self._file.flush()

    def _update_statistics(self, sample: CalibratedSample) -> None:
        """Update running statistics for a sample.

        Args:
            sample: The sample to include in statistics.
        """
        channel_id = sample.channel_id
        value = sample.calibrated_value

        if channel_id not in self._sample_counts:
            self._sample_counts[channel_id] = 0
            self._min_values[channel_id] = float("inf")
            self._max_values[channel_id] = float("-inf")
            self._sum_values[channel_id] = 0.0

        self._sample_counts[channel_id] += 1
        self._sum_values[channel_id] += value

        if value < self._min_values[channel_id]:
            self._min_values[channel_id] = value
        if value > self._max_values[channel_id]:
            self._max_values[channel_id] = value

    def _check_disk_space(self) -> None:
        """Check available disk space and alert if below threshold."""
        now = datetime.now()
        if (
            self._last_disk_check is not None
            and (now - self._last_disk_check).total_seconds()
            < self._disk_check_interval_seconds
        ):
            return

        self._last_disk_check = now

        try:
            target_dir = (
                self._fallback_csv_directory
                if self._using_fallback
                else self._csv_directory
            )
            if target_dir and target_dir.exists():
                usage = shutil.disk_usage(target_dir)
                free_mb = usage.free // (1024 * 1024)

                if free_mb < self._disk_space_warning_mb:
                    logger.warning(
                        f"Low disk space: {free_mb} MB remaining "
                        f"(threshold: {self._disk_space_warning_mb} MB)"
                    )
                    if self._on_disk_space_warning:
                        self._on_disk_space_warning(int(free_mb))
        except OSError as e:
            logger.warning(f"Could not check disk space: {e}")

    def _switch_to_fallback(self, pending_samples: list[CalibratedSample]) -> None:
        """Switch to the fallback directory after a write error.

        Args:
            pending_samples: Samples that failed to write to the primary file.
        """
        if not self._fallback_csv_directory:
            return

        logger.warning(f"Switching to fallback directory: {self._fallback_csv_directory}")

        # Close the current file if open
        if self._file:
            try:
                self._file.close()
            except OSError:
                pass

        # Generate new filename in fallback directory
        timestamp_str = self._start_time.strftime("%Y%m%d_%H%M%S")
        safe_name = self._sanitize_filename(self._run_info.name)
        filename = f"{timestamp_str}_{safe_name}.csv"
        fallback_path = self._fallback_csv_directory / filename

        try:
            self._fallback_csv_directory.mkdir(parents=True, exist_ok=True)
            self._file = open(fallback_path, "w", newline="", encoding="utf-8")
            self._writer = csv.writer(self._file)
            self._csv_path = fallback_path
            self._using_fallback = True

            # Re-write header
            self._write_header(self._run_info)

            # Write the pending samples that failed
            for sample in pending_samples:
                self._writer.writerow([
                    f"{sample.timestamp_ms:.3f}",
                    sample.channel_id,
                    f"{sample.calibrated_value:.6g}",
                    sample.unit,
                    sample.validity.value,
                ])
            self._file.flush()

        except OSError as e:
            error_msg = f"Fallback directory also failed: {e}"
            logger.error(error_msg)
            if self._on_write_error:
                self._on_write_error(error_msg)
            self._file = None
            self._writer = None

    @staticmethod
    def _sanitize_filename(name: str) -> str:
        """Sanitize a string for use in a filename.

        Args:
            name: The raw name to sanitize.

        Returns:
            A filesystem-safe version of the name.
        """
        # Replace common problematic characters with underscores
        invalid_chars = '<>:"/\\|?*'
        result = name
        for char in invalid_chars:
            result = result.replace(char, "_")
        # Replace spaces with underscores for cleaner filenames
        result = result.replace(" ", "_")
        # Remove leading/trailing whitespace and dots
        result = result.strip(". ")
        # Limit length to avoid filesystem issues
        if len(result) > 100:
            result = result[:100]
        return result if result else "unnamed"
