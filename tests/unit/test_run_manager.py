"""Unit tests for RunManager.

Tests Requirements 13.1, 13.2, 13.3, 13.4, 13.5, 13.6.
"""

import csv
import json
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from rotax_dyno_daq.core.enums import UploadStatus
from rotax_dyno_daq.core.models import RunInfo, RunSummary
from rotax_dyno_daq.storage.run_manager import (
    NoActiveRunError,
    RunAlreadyActiveError,
    RunFilters,
    RunManager,
    RunManagerError,
    RunNotFoundError,
    RunValidationError,
)


@pytest.fixture
def run_log_path(tmp_path: Path) -> Path:
    """Provide a temporary path for the run log JSON file."""
    return tmp_path / "run_log.json"


@pytest.fixture
def manager(run_log_path: Path) -> RunManager:
    """Create a RunManager with no dependencies."""
    return RunManager(run_log_path=run_log_path)


# --- start_run validation tests ---


class TestStartRunValidation:
    """Tests for start_run name and notes validation (Req 13.1, 13.2)."""

    def test_valid_name_and_notes(self, manager: RunManager) -> None:
        """A valid name and notes should succeed."""
        run_info = manager.start_run("Test Run 1", notes="Some notes")
        assert run_info.name == "Test Run 1"
        assert run_info.notes == "Some notes"

    def test_empty_name_rejected(self, manager: RunManager) -> None:
        """Empty name should be rejected."""
        with pytest.raises(RunValidationError, match="must not be empty"):
            manager.start_run("")

    def test_whitespace_only_name_rejected(self, manager: RunManager) -> None:
        """Whitespace-only name should be rejected."""
        with pytest.raises(RunValidationError, match="must not be empty"):
            manager.start_run("   ")

    def test_name_too_long_rejected(self, manager: RunManager) -> None:
        """Name over 100 characters should be rejected."""
        with pytest.raises(RunValidationError, match="1-100 characters"):
            manager.start_run("x" * 101)

    def test_name_exactly_100_chars_accepted(self, manager: RunManager) -> None:
        """Name of exactly 100 characters should be accepted."""
        run_info = manager.start_run("x" * 100)
        assert run_info.name == "x" * 100

    def test_name_exactly_1_char_accepted(self, manager: RunManager) -> None:
        """Name of exactly 1 character should be accepted."""
        run_info = manager.start_run("A")
        assert run_info.name == "A"

    def test_duplicate_name_rejected(self, manager: RunManager) -> None:
        """Duplicate run name should be rejected."""
        manager.start_run("My Run")
        manager.stop_run()
        with pytest.raises(RunValidationError, match="already exists"):
            manager.start_run("My Run")

    def test_notes_too_long_rejected(self, manager: RunManager) -> None:
        """Notes over 1000 characters should be rejected."""
        with pytest.raises(RunValidationError, match="up to 1000 characters"):
            manager.start_run("Run", notes="n" * 1001)

    def test_notes_exactly_1000_chars_accepted(self, manager: RunManager) -> None:
        """Notes of exactly 1000 characters should be accepted."""
        run_info = manager.start_run("Run", notes="n" * 1000)
        assert run_info.notes == "n" * 1000

    def test_cannot_start_while_active(self, manager: RunManager) -> None:
        """Starting a run while one is active should raise."""
        manager.start_run("Run 1")
        with pytest.raises(RunAlreadyActiveError):
            manager.start_run("Run 2")


# --- stop_run tests ---


class TestStopRun:
    """Tests for stop_run lifecycle (Req 13.3)."""

    def test_stop_returns_summary(self, manager: RunManager) -> None:
        """Stopping a run should return a RunSummary."""
        manager.start_run("Test Run")
        summary = manager.stop_run()
        assert summary.name == "Test Run"
        assert summary.run_id
        assert summary.duration_seconds >= 0
        assert summary.start_time <= summary.end_time

    def test_stop_without_active_raises(self, manager: RunManager) -> None:
        """Stopping without an active run should raise."""
        with pytest.raises(NoActiveRunError):
            manager.stop_run()

    def test_stop_adds_to_run_log(self, manager: RunManager) -> None:
        """Stopping a run should add it to the run log."""
        manager.start_run("Run A")
        manager.stop_run()
        log = manager.get_run_log()
        assert len(log) == 1
        assert log[0].name == "Run A"

    def test_stop_triggers_cloud_upload(self, run_log_path: Path, tmp_path: Path) -> None:
        """Stopping a run should queue cloud upload if uploader is available."""
        mock_logger = MagicMock()
        csv_path = tmp_path / "test.csv"
        csv_path.touch()
        mock_logger.stop_run.return_value = RunSummary(
            run_id="test-id",
            name="Run",
            start_time=datetime.now(),
            end_time=datetime.now(),
            duration_seconds=10.0,
            sample_counts={},
            min_values={},
            max_values={},
            mean_values={},
            csv_path=csv_path,
        )

        mock_uploader = MagicMock()

        mgr = RunManager(
            run_log_path=run_log_path,
            csv_logger=mock_logger,
            cloud_uploader=mock_uploader,
        )
        mgr.start_run("Upload Run")
        mgr.stop_run()

        mock_uploader.queue_upload.assert_called_once_with(csv_path)


# --- get_run_log tests ---


class TestGetRunLog:
    """Tests for get_run_log with filtering (Req 13.3, 13.5)."""

    def _populate_runs(self, manager: RunManager) -> list[RunSummary]:
        """Create several runs for testing."""
        summaries = []
        for i in range(5):
            manager.start_run(f"Run {i}")
            summary = manager.stop_run()
            summaries.append(summary)
        return summaries

    def test_returns_all_runs_sorted_descending(self, manager: RunManager) -> None:
        """Run log should be sorted by date descending."""
        self._populate_runs(manager)
        log = manager.get_run_log()
        assert len(log) == 5
        for i in range(len(log) - 1):
            assert log[i].start_time >= log[i + 1].start_time

    def test_filter_by_name_substring(self, manager: RunManager) -> None:
        """Filtering by name substring should match case-insensitively."""
        manager.start_run("Alpha Test")
        manager.stop_run()
        manager.start_run("Beta Run")
        manager.stop_run()
        manager.start_run("Alpha Run 2")
        manager.stop_run()

        results = manager.get_run_log(RunFilters(name_substring="alpha"))
        assert len(results) == 2
        assert all("alpha" in r.name.lower() for r in results)

    def test_filter_by_date_range(self, run_log_path: Path) -> None:
        """Filtering by date range should include only matching runs."""
        mgr = RunManager(run_log_path=run_log_path)

        # Manually add runs with specific dates
        now = datetime.now()
        for i in range(3):
            mgr._run_log.append(
                RunSummary(
                    run_id=f"id-{i}",
                    name=f"Run {i}",
                    start_time=now - timedelta(days=i * 2),
                    end_time=now - timedelta(days=i * 2) + timedelta(minutes=10),
                    duration_seconds=600,
                    sample_counts={},
                    min_values={},
                    max_values={},
                    mean_values={},
                )
            )

        # Filter for runs in the last day
        results = mgr.get_run_log(
            RunFilters(start_date=now - timedelta(days=1))
        )
        assert len(results) == 1
        assert results[0].name == "Run 0"

    def test_filter_by_tags(self, manager: RunManager) -> None:
        """Filtering by tags should match runs with any of the specified tags."""
        manager.start_run("Run A")
        summary_a = manager.stop_run()
        manager.tag_run(summary_a.run_id, ["engine", "warm-up"])

        manager.start_run("Run B")
        summary_b = manager.stop_run()
        manager.tag_run(summary_b.run_id, ["cooldown"])

        results = manager.get_run_log(RunFilters(tags=["engine"]))
        assert len(results) == 1
        assert results[0].name == "Run A"

    def test_pagination(self, run_log_path: Path) -> None:
        """Pagination should return correct page of results."""
        mgr = RunManager(run_log_path=run_log_path)

        now = datetime.now()
        for i in range(10):
            mgr._run_log.append(
                RunSummary(
                    run_id=f"id-{i}",
                    name=f"Run {i}",
                    start_time=now - timedelta(minutes=i),
                    end_time=now - timedelta(minutes=i) + timedelta(seconds=30),
                    duration_seconds=30,
                    sample_counts={},
                    min_values={},
                    max_values={},
                    mean_values={},
                )
            )

        page1 = mgr.get_run_log(RunFilters(page=1, page_size=3))
        page2 = mgr.get_run_log(RunFilters(page=2, page_size=3))
        page4 = mgr.get_run_log(RunFilters(page=4, page_size=3))

        assert len(page1) == 3
        assert len(page2) == 3
        assert len(page4) == 1  # 10 runs, 3 per page, page 4 has 1


# --- tag_run tests ---


class TestTagRun:
    """Tests for tag_run validation (Req 13.4)."""

    def test_add_tags_to_run(self, manager: RunManager) -> None:
        """Adding valid tags should succeed."""
        manager.start_run("Tagged Run")
        summary = manager.stop_run()
        manager.tag_run(summary.run_id, ["engine", "test"])

        log = manager.get_run_log()
        assert "engine" in log[0].tags
        assert "test" in log[0].tags

    def test_tag_nonexistent_run_raises(self, manager: RunManager) -> None:
        """Tagging a non-existent run should raise."""
        with pytest.raises(RunNotFoundError):
            manager.tag_run("nonexistent-id", ["tag"])

    def test_too_many_tags_rejected(self, manager: RunManager) -> None:
        """More than 10 tags total should be rejected."""
        manager.start_run("Run")
        summary = manager.stop_run()
        manager.tag_run(summary.run_id, [f"tag{i}" for i in range(10)])

        with pytest.raises(RunValidationError, match="at most 10 tags"):
            manager.tag_run(summary.run_id, ["one-too-many"])

    def test_tag_too_long_rejected(self, manager: RunManager) -> None:
        """Tags over 50 characters should be rejected."""
        manager.start_run("Run")
        summary = manager.stop_run()

        with pytest.raises(RunValidationError, match="up to 50 characters"):
            manager.tag_run(summary.run_id, ["x" * 51])

    def test_empty_tag_rejected(self, manager: RunManager) -> None:
        """Empty tags should be rejected."""
        manager.start_run("Run")
        summary = manager.stop_run()

        with pytest.raises(RunValidationError, match="must not be empty"):
            manager.tag_run(summary.run_id, [""])

    def test_duplicate_tags_not_added_twice(self, manager: RunManager) -> None:
        """Adding a duplicate tag should not create duplicates."""
        manager.start_run("Run")
        summary = manager.stop_run()
        manager.tag_run(summary.run_id, ["engine"])
        manager.tag_run(summary.run_id, ["engine"])

        log = manager.get_run_log()
        assert log[0].tags.count("engine") == 1


# --- export_run tests ---


class TestExportRun:
    """Tests for export_run CSV export (Req 13.6)."""

    def test_export_creates_csv_with_iso_timestamps(
        self, run_log_path: Path, tmp_path: Path
    ) -> None:
        """Export should create a CSV with ISO 8601 timestamps."""
        # Create a source CSV file
        source_csv = tmp_path / "source.csv"
        start_time = datetime(2024, 1, 15, 10, 30, 0)

        with open(source_csv, "w", newline="") as f:
            f.write("# Run: Test Export\n")
            f.write("# Start: 2024-01-15T10:30:00\n")
            writer = csv.writer(f)
            writer.writerow(["timestamp_ms", "EGT1", "OilP"])
            writer.writerow(["0.0", "450.5", "3.2"])
            writer.writerow(["1000.0", "455.0", "3.3"])
            writer.writerow(["2000.0", "460.1", "3.1"])

        # Set up manager with a pre-populated run log
        mgr = RunManager(run_log_path=run_log_path)
        mgr._run_log.append(
            RunSummary(
                run_id="export-test-id",
                name="Test Export",
                start_time=start_time,
                end_time=start_time + timedelta(seconds=2),
                duration_seconds=2.0,
                sample_counts={"EGT1": 3, "OilP": 3},
                min_values={"EGT1": 450.5, "OilP": 3.1},
                max_values={"EGT1": 460.1, "OilP": 3.3},
                mean_values={"EGT1": 455.2, "OilP": 3.2},
                csv_path=source_csv,
            )
        )

        output_csv = tmp_path / "exported.csv"
        mgr.export_run("export-test-id", output_csv)

        assert output_csv.exists()

        with open(output_csv, "r") as f:
            reader = csv.reader(f)
            header = next(reader)
            assert header == ["timestamp_iso8601", "EGT1", "OilP"]

            rows = list(reader)
            assert len(rows) == 3
            # Verify ISO 8601 format in first column
            for row in rows:
                # Should be parseable as ISO datetime
                datetime.fromisoformat(row[0])

    def test_export_nonexistent_run_raises(self, manager: RunManager) -> None:
        """Exporting a non-existent run should raise."""
        with pytest.raises(RunNotFoundError):
            manager.export_run("nonexistent", Path("/tmp/out.csv"))

    def test_export_run_without_csv_raises(
        self, run_log_path: Path, tmp_path: Path
    ) -> None:
        """Exporting a run with no CSV path should raise."""
        mgr = RunManager(run_log_path=run_log_path)
        mgr._run_log.append(
            RunSummary(
                run_id="no-csv-id",
                name="No CSV",
                start_time=datetime.now(),
                end_time=datetime.now(),
                duration_seconds=0,
                sample_counts={},
                min_values={},
                max_values={},
                mean_values={},
                csv_path=None,
            )
        )

        with pytest.raises(RunManagerError, match="No CSV data"):
            mgr.export_run("no-csv-id", tmp_path / "out.csv")


# --- Persistence tests ---


class TestPersistence:
    """Tests for run log persistence."""

    def test_run_log_persists_across_instances(self, run_log_path: Path) -> None:
        """Run log should be loadable by a new RunManager instance."""
        mgr1 = RunManager(run_log_path=run_log_path)
        mgr1.start_run("Persistent Run")
        mgr1.stop_run()

        mgr2 = RunManager(run_log_path=run_log_path)
        log = mgr2.get_run_log()
        assert len(log) == 1
        assert log[0].name == "Persistent Run"

    def test_corrupted_log_file_handled(self, run_log_path: Path) -> None:
        """A corrupted log file should result in an empty run log."""
        run_log_path.parent.mkdir(parents=True, exist_ok=True)
        run_log_path.write_text("not valid json {{{", encoding="utf-8")

        mgr = RunManager(run_log_path=run_log_path)
        assert mgr.get_run_log() == []

    def test_missing_log_file_handled(self, run_log_path: Path) -> None:
        """A missing log file should result in an empty run log."""
        mgr = RunManager(run_log_path=run_log_path)
        assert mgr.get_run_log() == []
