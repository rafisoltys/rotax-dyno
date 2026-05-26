"""Unit tests for the CsvLogger class."""

import csv
from datetime import datetime
from pathlib import Path

import pytest

from rotax_dyno_daq.core.enums import SampleValidity, UploadStatus
from rotax_dyno_daq.core.models import CalibratedSample, RunInfo
from rotax_dyno_daq.storage.csv_logger import CsvLogger


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
        """CSV file should be named YYYYMMDD_HHMMSS_{run_name}.csv."""
        csv_logger.start_run(run_info)

        assert csv_logger.csv_path is not None
        filename = csv_logger.csv_path.name
        # Should contain the sanitized run name
        assert "Test_Run_1" in filename
        # Should end with .csv
        assert filename.endswith(".csv")
        # Should start with date pattern (YYYYMMDD_HHMMSS)
        parts = filename.split("_", 2)
        assert len(parts[0]) == 8  # YYYYMMDD
        assert len(parts[1]) == 6  # HHMMSS

        csv_logger.stop_run()

    def test_csv_file_contains_header_metadata(
        self, csv_logger: CsvLogger, run_info: RunInfo
    ) -> None:
        """Header should contain run name, start time, operator, notes."""
        csv_logger.start_run(run_info)
        csv_logger.stop_run()

        content = csv_logger.csv_path.read_text(encoding="utf-8")
        assert "Test Run 1" in content
        assert "Tester" in content
        assert "Testing the CSV logger" in content
        assert "test;unit" in content

    def test_csv_file_contains_column_headers(
        self, csv_logger: CsvLogger, run_info: RunInfo
    ) -> None:
        """CSV should have column headers for data rows."""
        csv_logger.start_run(run_info)
        csv_logger.stop_run()

        content = csv_logger.csv_path.read_text(encoding="utf-8")
        assert "timestamp_ms" in content
        assert "channel_id" in content
        assert "calibrated_value" in content
        assert "unit" in content
        assert "validity" in content

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

    def test_buffers_samples(
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

        # After flush, samples should be written
        content = csv_logger.csv_path.read_text(encoding="utf-8")
        assert "650" in content

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

    def test_writes_buffered_samples_to_disk(
        self, csv_logger: CsvLogger, run_info: RunInfo, sample_data: list
    ) -> None:
        """Flush should write all buffered samples to the CSV file."""
        csv_logger.start_run(run_info)
        for sample in sample_data:
            csv_logger.write_sample(sample)

        csv_logger.flush()

        # Parse the CSV and verify data rows
        with open(csv_logger.csv_path, "r", encoding="utf-8") as f:
            reader = csv.reader(f)
            rows = list(reader)

        # Find data rows (after header)
        data_rows = [
            r for r in rows
            if r and not r[0].startswith("#") and r[0] != "timestamp_ms"
        ]
        assert len(data_rows) == 4

        csv_logger.stop_run()

    def test_flush_clears_buffer(
        self, csv_logger: CsvLogger, run_info: RunInfo
    ) -> None:
        """After flush, buffer should be empty."""
        csv_logger.start_run(run_info)
        sample = CalibratedSample(
            channel_id="EGT1",
            timestamp_ms=0.0,
            raw_value=2.5,
            calibrated_value=650.0,
            unit="°C",
        )
        csv_logger.write_sample(sample)
        csv_logger.flush()

        # Second flush should not write additional rows
        csv_logger.flush()

        with open(csv_logger.csv_path, "r", encoding="utf-8") as f:
            reader = csv.reader(f)
            rows = list(reader)

        data_rows = [
            r for r in rows
            if r and not r[0].startswith("#") and r[0] != "timestamp_ms"
        ]
        assert len(data_rows) == 1

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

    def test_appends_summary_metadata_to_file(
        self, csv_logger: CsvLogger, run_info: RunInfo, sample_data: list
    ) -> None:
        """stop_run should append summary metadata at end of CSV."""
        csv_logger.start_run(run_info)
        for sample in sample_data:
            csv_logger.write_sample(sample)

        summary = csv_logger.stop_run()

        content = summary.csv_path.read_text(encoding="utf-8")
        assert "Run Summary" in content
        assert "Duration" in content
        assert "EGT1" in content
        assert "OilP" in content

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


class TestCsvLoggerSampleFormat:
    """Tests for CSV sample row format."""

    def test_sample_row_format(
        self, csv_logger: CsvLogger, run_info: RunInfo
    ) -> None:
        """Each row should have: timestamp_ms, channel_id, calibrated_value, unit, validity."""
        csv_logger.start_run(run_info)
        sample = CalibratedSample(
            channel_id="EGT1",
            timestamp_ms=1234.567,
            raw_value=2.5,
            calibrated_value=650.123,
            unit="°C",
            validity=SampleValidity.VALID,
        )
        csv_logger.write_sample(sample)
        csv_logger.flush()
        csv_logger.stop_run()

        with open(csv_logger.csv_path, "r", encoding="utf-8") as f:
            reader = csv.reader(f)
            rows = list(reader)

        data_rows = [
            r for r in rows
            if r and not r[0].startswith("#") and r[0] != "timestamp_ms" and r[0] != ""
        ]
        assert len(data_rows) == 1
        row = data_rows[0]
        assert row[0] == "1234.567"  # timestamp_ms
        assert row[1] == "EGT1"  # channel_id
        assert "650.123" in row[2]  # calibrated_value
        assert row[3] == "°C"  # unit
        assert row[4] == "valid"  # validity

    def test_invalid_sample_validity_recorded(
        self, csv_logger: CsvLogger, run_info: RunInfo
    ) -> None:
        """Invalid samples should have their validity recorded correctly."""
        csv_logger.start_run(run_info)
        sample = CalibratedSample(
            channel_id="EGT1",
            timestamp_ms=0.0,
            raw_value=0.0,
            calibrated_value=0.0,
            unit="°C",
            validity=SampleValidity.INVALID,
        )
        csv_logger.write_sample(sample)
        csv_logger.flush()
        csv_logger.stop_run()

        content = csv_logger.csv_path.read_text(encoding="utf-8")
        assert "invalid" in content


class TestCsvLoggerFilenameHandling:
    """Tests for filename sanitization."""

    def test_sanitizes_special_characters(
        self, tmp_csv_dir: Path
    ) -> None:
        """Special characters in run name should be replaced."""
        logger_instance = CsvLogger(csv_directory=tmp_csv_dir)
        run_info = RunInfo(name='Test/Run:With"Special<Chars>')
        logger_instance.start_run(run_info)

        filename = logger_instance.csv_path.name
        assert "/" not in filename
        assert ":" not in filename
        assert '"' not in filename
        assert "<" not in filename
        assert ">" not in filename

        logger_instance.stop_run()

    def test_spaces_replaced_with_underscores(
        self, tmp_csv_dir: Path
    ) -> None:
        """Spaces in run name should become underscores in filename."""
        logger_instance = CsvLogger(csv_directory=tmp_csv_dir)
        run_info = RunInfo(name="My Test Run")
        logger_instance.start_run(run_info)

        filename = logger_instance.csv_path.name
        assert "My_Test_Run" in filename

        logger_instance.stop_run()
