"""Unit tests for the CsvLogger class."""

import csv
from datetime import datetime
from pathlib import Path

import pytest

from rotax_dyno_daq.core.enums import SampleValidity, UploadStatus
from rotax_dyno_daq.core.models import CalibratedSample, RunInfo
from rotax_dyno_daq.storage.csv_logger import CSV_COLUMNS, CSV_DATA_COLUMNS, CsvLogger


@pytest.fixture
def tmp_csv_dir(tmp_path: Path) -> Path:
    """Create a temporary directory for CSV files."""
    csv_dir = tmp_path / "csv_data"
    csv_dir.mkdir()
    return csv_dir


@pytest.fixture
def fallback_dir(tmp_path: Path) -> Path:
    """Create a temporary fallback directory."""
    fb_dir = tmp_path / "fallback"
    fb_dir.mkdir()
    return fb_dir


@pytest.fixture
def csv_logger(tmp_csv_dir: Path) -> CsvLogger:
    """Create a CsvLogger instance with a temporary directory."""
    return CsvLogger(csv_directory=tmp_csv_dir)


@pytest.fixture
def run_info() -> RunInfo:
    """Create a sample RunInfo for testing."""
    return RunInfo(
        name="Test Run 1",
        notes="Testing the CSV logger",
        tags=["test", "unit"],
        operator="Tester",
    )


@pytest.fixture
def sample_data() -> list[CalibratedSample]:
    """Create sample calibrated data for testing."""
    return [
        CalibratedSample(
            channel_id="EGT1",
            timestamp_ms=0.0,
            raw_value=2.5,
            calibrated_value=650.0,
            unit="°C",
            validity=SampleValidity.VALID,
        ),
        CalibratedSample(
            channel_id="EGT1",
            timestamp_ms=200.0,
            raw_value=2.6,
            calibrated_value=660.0,
            unit="°C",
            validity=SampleValidity.VALID,
        ),
        CalibratedSample(
            channel_id="OilP",
            timestamp_ms=100.0,
            raw_value=1.8,
            calibrated_value=3.5,
            unit="bar",
            validity=SampleValidity.VALID,
        ),
        CalibratedSample(
            channel_id="OilP",
            timestamp_ms=200.0,
            raw_value=1.9,
            calibrated_value=3.7,
            unit="bar",
            validity=SampleValidity.VALID,
        ),
    ]


class TestCsvLoggerStartRun:
    """Tests for start_run functionality."""

    def test_creates_csv_file_with_correct_name_pattern(
        self, csv_logger: CsvLogger, run_info: RunInfo
    ) -> None:
        """CSV file should be named log_YYYYMMDD_HHMMSS.csv."""
        csv_logger.start_run(run_info)

        assert csv_logger.csv_path is not None
        filename = csv_logger.csv_path.name
        # Should start with "log_" prefix
        assert filename.startswith("log_")
        # Should end with .csv
        assert filename.endswith(".csv")
        # Should follow log_YYYYMMDD_HHMMSS.csv pattern
        # Strip prefix and suffix: "YYYYMMDD_HHMMSS"
        stem = filename[len("log_"):-len(".csv")]
        parts = stem.split("_")
        assert len(parts) == 2
        assert len(parts[0]) == 8  # YYYYMMDD
        assert len(parts[1]) == 6  # HHMMSS

        csv_logger.stop_run()

    def test_csv_file_contains_fixed_column_header(
        self, csv_logger: CsvLogger, run_info: RunInfo
    ) -> None:
        """Header should be the fixed CSV_COLUMNS row."""
        csv_logger.start_run(run_info)
        csv_logger.stop_run()

        with open(csv_logger.csv_path, "r", encoding="utf-8") as f:
            reader = csv.reader(f)
            header = next(reader)

        assert header == CSV_COLUMNS

    def test_raises_if_run_already_active(
        self, csv_logger: CsvLogger, run_info: RunInfo
    ) -> None:
        """Should raise RuntimeError if start_run called while run is active."""
        csv_logger.start_run(run_info)
        with pytest.raises(RuntimeError, match="already active"):
            csv_logger.start_run(run_info)
        csv_logger.stop_run()

    def test_is_active_property(
        self, csv_logger: CsvLogger, run_info: RunInfo
    ) -> None:
        """is_active should reflect run state."""
        assert not csv_logger.is_active
        csv_logger.start_run(run_info)
        assert csv_logger.is_active
        csv_logger.stop_run()
        assert not csv_logger.is_active


class TestCsvLoggerWriteSample:
    """Tests for write_sample functionality."""

    def test_buffers_samples_until_flush(
        self, csv_logger: CsvLogger, run_info: RunInfo, sample_data: list
    ) -> None:
        """Samples should be buffered until flush is called."""
        csv_logger.start_run(run_info)
        for sample in sample_data:
            csv_logger.write_sample(sample)

        # Before flush, file should only have header
        content = csv_logger.csv_path.read_text(encoding="utf-8")
        assert "650" not in content

        csv_logger.flush()

        # After flush, values should be written
        content = csv_logger.csv_path.read_text(encoding="utf-8")
        assert "660" in content  # Latest EGT1 value

        csv_logger.stop_run()

    def test_raises_if_no_active_run(self, csv_logger: CsvLogger) -> None:
        """Should raise RuntimeError if write_sample called without active run."""
        sample = CalibratedSample(
            channel_id="EGT1",
            timestamp_ms=0.0,
            raw_value=2.5,
            calibrated_value=650.0,
            unit="°C",
        )
        with pytest.raises(RuntimeError, match="No active run"):
            csv_logger.write_sample(sample)


class TestCsvLoggerFlush:
    """Tests for flush functionality."""

    def test_writes_one_row_per_flush(
        self, csv_logger: CsvLogger, run_info: RunInfo, sample_data: list
    ) -> None:
        """Each flush should write exactly one row with latest channel values."""
        csv_logger.start_run(run_info)
        for sample in sample_data:
            csv_logger.write_sample(sample)

        csv_logger.flush()

        # Parse the CSV and verify data rows
        with open(csv_logger.csv_path, "r", encoding="utf-8") as f:
            reader = csv.reader(f)
            rows = list(reader)

        # First row is header, second row is data
        assert len(rows) == 2  # header + 1 data row
        data_row = rows[1]
        # Should have Date, Time, then channel values
        assert len(data_row) == len(CSV_COLUMNS)

        csv_logger.stop_run()

    def test_row_contains_latest_values(
        self, csv_logger: CsvLogger, run_info: RunInfo
    ) -> None:
        """Flush row should contain the latest value for each channel."""
        csv_logger.start_run(run_info)

        # Write two EGT1 samples — only latest should appear
        csv_logger.write_sample(CalibratedSample(
            channel_id="EGT1", timestamp_ms=0.0, raw_value=2.5,
            calibrated_value=650.0, unit="°C", validity=SampleValidity.VALID,
        ))
        csv_logger.write_sample(CalibratedSample(
            channel_id="EGT1", timestamp_ms=100.0, raw_value=2.6,
            calibrated_value=660.0, unit="°C", validity=SampleValidity.VALID,
        ))

        csv_logger.flush()

        with open(csv_logger.csv_path, "r", encoding="utf-8") as f:
            reader = csv.reader(f)
            rows = list(reader)

        data_row = rows[1]
        # EGT1 is at index 10 in CSV_COLUMNS (Date, Time, RPM, OILT, OILP, CLT, IAT, Charge, EGT1...)
        egt1_idx = CSV_COLUMNS.index("EGT1")
        assert data_row[egt1_idx] == "660.00"  # Latest value

        csv_logger.stop_run()

    def test_keeps_values_between_flushes(
        self, csv_logger: CsvLogger, run_info: RunInfo
    ) -> None:
        """Values should persist between flushes (not cleared)."""
        csv_logger.start_run(run_info)

        csv_logger.write_sample(CalibratedSample(
            channel_id="EGT1", timestamp_ms=0.0, raw_value=2.5,
            calibrated_value=650.0, unit="°C", validity=SampleValidity.VALID,
        ))
        csv_logger.flush()

        # Second flush without new EGT1 data — should still have EGT1 value
        csv_logger.write_sample(CalibratedSample(
            channel_id="RPM", timestamp_ms=100.0, raw_value=3.0,
            calibrated_value=4500.0, unit="RPM", validity=SampleValidity.VALID,
        ))
        csv_logger.flush()

        with open(csv_logger.csv_path, "r", encoding="utf-8") as f:
            reader = csv.reader(f)
            rows = list(reader)

        # Should have header + 2 data rows
        assert len(rows) == 3
        second_data_row = rows[2]
        egt1_idx = CSV_COLUMNS.index("EGT1")
        rpm_idx = CSV_COLUMNS.index("RPM")
        assert second_data_row[egt1_idx] == "650.00"  # Persisted from first flush
        assert second_data_row[rpm_idx] == "4500.00"

        csv_logger.stop_run()

    def test_flush_no_op_when_no_data(
        self, csv_logger: CsvLogger, run_info: RunInfo
    ) -> None:
        """Flush should not write a row if no data has been received."""
        csv_logger.start_run(run_info)
        csv_logger.flush()

        with open(csv_logger.csv_path, "r", encoding="utf-8") as f:
            reader = csv.reader(f)
            rows = list(reader)

        # Only header row
        assert len(rows) == 1

        csv_logger.stop_run()

    def test_flush_no_op_when_no_active_run(self, csv_logger: CsvLogger) -> None:
        """Flush should do nothing if no run is active."""
        csv_logger.flush()  # Should not raise


class TestCsvLoggerStopRun:
    """Tests for stop_run functionality."""

    def test_returns_run_summary(
        self, csv_logger: CsvLogger, run_info: RunInfo, sample_data: list
    ) -> None:
        """stop_run should return a RunSummary with correct statistics."""
        csv_logger.start_run(run_info)
        for sample in sample_data:
            csv_logger.write_sample(sample)

        summary = csv_logger.stop_run()

        assert summary.name == "Test Run 1"
        assert summary.notes == "Testing the CSV logger"
        assert summary.tags == ["test", "unit"]
        assert summary.duration_seconds >= 0
        assert summary.sample_counts == {"EGT1": 2, "OilP": 2}
        assert summary.min_values["EGT1"] == 650.0
        assert summary.max_values["EGT1"] == 660.0
        assert summary.min_values["OilP"] == 3.5
        assert summary.max_values["OilP"] == 3.7
        assert summary.mean_values["EGT1"] == pytest.approx(655.0)
        assert summary.mean_values["OilP"] == pytest.approx(3.6)
        assert summary.upload_status == UploadStatus.PENDING
        assert summary.csv_path is not None

    def test_no_summary_metadata_in_csv(
        self, csv_logger: CsvLogger, run_info: RunInfo, sample_data: list
    ) -> None:
        """stop_run should NOT append summary metadata to the CSV file."""
        csv_logger.start_run(run_info)
        for sample in sample_data:
            csv_logger.write_sample(sample)

        summary = csv_logger.stop_run()

        content = summary.csv_path.read_text(encoding="utf-8")
        # No summary comments in the file
        assert "# --- Run Summary ---" not in content
        assert "# End Time" not in content
        assert "# Duration" not in content

    def test_flushes_remaining_samples(
        self, csv_logger: CsvLogger, run_info: RunInfo
    ) -> None:
        """stop_run should flush any remaining buffered samples."""
        csv_logger.start_run(run_info)
        sample = CalibratedSample(
            channel_id="RPM",
            timestamp_ms=500.0,
            raw_value=3.0,
            calibrated_value=4500.0,
            unit="RPM",
        )
        csv_logger.write_sample(sample)

        # Don't call flush manually
        summary = csv_logger.stop_run()

        content = summary.csv_path.read_text(encoding="utf-8")
        assert "4500" in content

    def test_raises_if_no_active_run(self, csv_logger: CsvLogger) -> None:
        """Should raise RuntimeError if stop_run called without active run."""
        with pytest.raises(RuntimeError, match="No active run"):
            csv_logger.stop_run()


class TestCsvLoggerDiskSpace:
    """Tests for disk space monitoring."""

    def test_disk_space_warning_callback(self, tmp_csv_dir: Path) -> None:
        """Should invoke callback when disk space is below threshold."""
        warnings: list[int] = []

        # Set an impossibly high threshold to trigger warning
        logger_instance = CsvLogger(
            csv_directory=tmp_csv_dir,
            disk_space_warning_mb=999_999_999,  # Will always trigger
            on_disk_space_warning=lambda mb: warnings.append(mb),
        )

        run_info = RunInfo(name="disk_test")
        logger_instance.start_run(run_info)
        sample = CalibratedSample(
            channel_id="EGT1",
            timestamp_ms=0.0,
            raw_value=2.5,
            calibrated_value=650.0,
            unit="°C",
        )
        logger_instance.write_sample(sample)
        logger_instance.flush()

        assert len(warnings) > 0
        assert warnings[0] >= 0  # Should report actual free MB

        logger_instance.stop_run()


class TestCsvLoggerFallback:
    """Tests for fallback directory switching."""

    def test_uses_fallback_when_primary_fails(
        self, tmp_path: Path, fallback_dir: Path
    ) -> None:
        """Should switch to fallback directory when primary is not writable."""
        import sys

        # Use a path that cannot be created on the current OS
        if sys.platform == "win32":
            bad_primary = Path("Z:\\nonexistent_drive_xyz\\csv_data")
        else:
            bad_primary = Path("/proc/nonexistent/csv_data")

        errors: list[str] = []
        logger_instance = CsvLogger(
            csv_directory=bad_primary,
            fallback_csv_directory=fallback_dir,
            on_write_error=lambda msg: errors.append(msg),
        )

        run_info = RunInfo(name="fallback_test")
        logger_instance.start_run(run_info)

        assert logger_instance.csv_path is not None
        assert fallback_dir in logger_instance.csv_path.parents

        logger_instance.stop_run()

    def test_raises_when_both_directories_fail(self, tmp_path: Path) -> None:
        """Should raise OSError when both primary and fallback fail."""
        import sys

        if sys.platform == "win32":
            bad_primary = Path("Z:\\nonexistent_drive_xyz\\csv_data")
            bad_fallback = Path("Z:\\another_nonexistent\\fallback")
        else:
            bad_primary = Path("/proc/nonexistent/csv_data")
            bad_fallback = Path("/proc/another_nonexistent/fallback")

        logger_instance = CsvLogger(
            csv_directory=bad_primary,
            fallback_csv_directory=bad_fallback,
        )

        run_info = RunInfo(name="fail_test")
        with pytest.raises(OSError):
            logger_instance.start_run(run_info)


class TestCsvLoggerRowFormat:
    """Tests for the fixed-column CSV row format."""

    def test_row_has_date_and_time_columns(
        self, csv_logger: CsvLogger, run_info: RunInfo
    ) -> None:
        """Each data row should start with Date (YYYY-MM-DD) and Time (HH:MM:SS.mmm)."""
        csv_logger.start_run(run_info)
        csv_logger.write_sample(CalibratedSample(
            channel_id="EGT1", timestamp_ms=0.0, raw_value=2.5,
            calibrated_value=650.0, unit="°C", validity=SampleValidity.VALID,
        ))
        csv_logger.flush()
        csv_logger.stop_run()

        with open(csv_logger.csv_path, "r", encoding="utf-8") as f:
            reader = csv.reader(f)
            rows = list(reader)

        data_row = rows[1]
        # Date format: YYYY-MM-DD
        date_parts = data_row[0].split("-")
        assert len(date_parts) == 3
        assert len(date_parts[0]) == 4  # Year
        assert len(date_parts[1]) == 2  # Month
        assert len(date_parts[2]) == 2  # Day

        # Time format: HH:MM:SS.mmm
        assert ":" in data_row[1]
        assert "." in data_row[1]

    def test_empty_columns_for_missing_channels(
        self, csv_logger: CsvLogger, run_info: RunInfo
    ) -> None:
        """Channels without data should have empty string in their column."""
        csv_logger.start_run(run_info)
        # Only write EGT1 — all other columns should be empty
        csv_logger.write_sample(CalibratedSample(
            channel_id="EGT1", timestamp_ms=0.0, raw_value=2.5,
            calibrated_value=650.0, unit="°C", validity=SampleValidity.VALID,
        ))
        csv_logger.flush()
        csv_logger.stop_run()

        with open(csv_logger.csv_path, "r", encoding="utf-8") as f:
            reader = csv.reader(f)
            rows = list(reader)

        data_row = rows[1]
        # RPM column should be empty
        rpm_idx = CSV_COLUMNS.index("RPM")
        assert data_row[rpm_idx] == ""
        # EGT1 should have value
        egt1_idx = CSV_COLUMNS.index("EGT1")
        assert data_row[egt1_idx] == "650.00"

    def test_channel_id_mapping(
        self, csv_logger: CsvLogger, run_info: RunInfo
    ) -> None:
        """Channel IDs like OilTemp should map to OILT column."""
        csv_logger.start_run(run_info)
        csv_logger.write_sample(CalibratedSample(
            channel_id="OilTemp", timestamp_ms=0.0, raw_value=2.0,
            calibrated_value=95.2, unit="°C", validity=SampleValidity.VALID,
        ))
        csv_logger.flush()
        csv_logger.stop_run()

        with open(csv_logger.csv_path, "r", encoding="utf-8") as f:
            reader = csv.reader(f)
            rows = list(reader)

        data_row = rows[1]
        oilt_idx = CSV_COLUMNS.index("OILT")
        assert data_row[oilt_idx] == "95.20"


class TestCsvLoggerFilenameHandling:
    """Tests for filename format (log_YYYYMMDD_HHMMSS.csv)."""

    def test_filename_does_not_contain_run_name(
        self, tmp_csv_dir: Path
    ) -> None:
        """Filename should use log_ prefix, not the run name."""
        logger_instance = CsvLogger(csv_directory=tmp_csv_dir)
        run_info = RunInfo(name='Test/Run:With"Special<Chars>')
        logger_instance.start_run(run_info)

        filename = logger_instance.csv_path.name
        assert filename.startswith("log_")
        assert filename.endswith(".csv")
        # Run name should NOT appear in filename
        assert "Test" not in filename.replace("log_", "")

        logger_instance.stop_run()
