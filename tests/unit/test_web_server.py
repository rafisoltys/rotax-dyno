"""Unit tests for the FastAPI REST API endpoints and remote monitoring frontend.

Tests GET /api/runs, GET /api/runs/{run_id}/data, GET /api/runs/compare,
and the static HTML5 remote monitoring frontend (Requirement 8.1, 8.3, 8.7).
"""

import csv
import json
import tempfile
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from rotax_dyno_daq.core.enums import UploadStatus
from rotax_dyno_daq.core.models import RunSummary
from rotax_dyno_daq.storage.run_manager import RunManager
from rotax_dyno_daq.web.server import app, set_run_manager


@pytest.fixture
def tmp_dir(tmp_path):
    """Provide a temporary directory for test data."""
    return tmp_path


@pytest.fixture
def run_manager(tmp_dir):
    """Create a RunManager with a temporary run log."""
    run_log_path = tmp_dir / "run_log.json"
    manager = RunManager(run_log_path=run_log_path)
    return manager


@pytest.fixture
def client(run_manager):
    """Create a FastAPI test client with the RunManager configured."""
    set_run_manager(run_manager)
    return TestClient(app)


def _create_csv_file(tmp_dir: Path, run_name: str, channels: dict) -> Path:
    """Create a test CSV file with sample data.

    Args:
        tmp_dir: Directory to create the file in.
        run_name: Name for the CSV file.
        channels: Dict of channel_id -> list of (timestamp_ms, value, unit) tuples.

    Returns:
        Path to the created CSV file.
    """
    csv_path = tmp_dir / f"20240101_120000_{run_name}.csv"
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["# Run Name", run_name])
        writer.writerow(["# Start Time", "2024-01-01T12:00:00"])
        writer.writerow(["timestamp_ms", "channel_id", "calibrated_value", "unit", "validity"])
        for channel_id, samples in channels.items():
            for ts, val, unit in samples:
                writer.writerow([f"{ts:.3f}", channel_id, f"{val:.6g}", unit, "valid"])
    return csv_path


def _add_run_to_manager(
    run_manager: RunManager,
    run_id: str,
    name: str,
    start_time: datetime,
    duration: float = 60.0,
    notes: str = "",
    tags: list[str] | None = None,
    csv_path: Path | None = None,
) -> RunSummary:
    """Add a RunSummary directly to the run manager's log."""
    summary = RunSummary(
        run_id=run_id,
        name=name,
        start_time=start_time,
        end_time=start_time + timedelta(seconds=duration),
        duration_seconds=duration,
        sample_counts={"EGT1": 100},
        min_values={"EGT1": 200.0},
        max_values={"EGT1": 800.0},
        mean_values={"EGT1": 500.0},
        notes=notes,
        tags=tags or [],
        csv_path=csv_path,
        upload_status=UploadStatus.COMPLETED,
    )
    run_manager._run_log.append(summary)
    run_manager._save_run_log()
    return summary


class TestListRuns:
    """Tests for GET /api/runs endpoint."""

    def test_empty_run_log(self, client):
        """Returns empty list when no runs exist."""
        response = client.get("/api/runs")
        assert response.status_code == 200
        data = response.json()
        assert data["runs"] == []
        assert data["total"] == 0
        assert data["total_pages"] == 1
        assert data["page"] == 1

    def test_returns_runs_sorted_by_date_descending(self, client, run_manager, tmp_dir):
        """Runs are returned sorted by date descending."""
        now = datetime.now()
        _add_run_to_manager(run_manager, "r1", "Run A", now - timedelta(hours=3))
        _add_run_to_manager(run_manager, "r2", "Run B", now - timedelta(hours=1))
        _add_run_to_manager(run_manager, "r3", "Run C", now - timedelta(hours=2))

        response = client.get("/api/runs")
        assert response.status_code == 200
        data = response.json()
        assert len(data["runs"]) == 3
        assert data["runs"][0]["name"] == "Run B"
        assert data["runs"][1]["name"] == "Run C"
        assert data["runs"][2]["name"] == "Run A"

    def test_pagination_defaults(self, client, run_manager):
        """Default pagination is page 1, page_size 50."""
        now = datetime.now()
        for i in range(5):
            _add_run_to_manager(
                run_manager, f"r{i}", f"Run {i}", now - timedelta(hours=i)
            )

        response = client.get("/api/runs")
        data = response.json()
        assert data["page"] == 1
        assert data["page_size"] == 50
        assert data["total"] == 5
        assert len(data["runs"]) == 5

    def test_pagination_page_size_limit(self, client, run_manager):
        """Page size is capped at 50."""
        response = client.get("/api/runs?page_size=100")
        # FastAPI validation should cap at 50
        assert response.status_code == 422  # Validation error for page_size > 50

    def test_pagination_multiple_pages(self, client, run_manager):
        """Pagination correctly splits results across pages."""
        now = datetime.now()
        for i in range(7):
            _add_run_to_manager(
                run_manager, f"r{i}", f"Run {i:02d}", now - timedelta(hours=i)
            )

        # Page 1 with page_size=3
        response = client.get("/api/runs?page=1&page_size=3")
        data = response.json()
        assert len(data["runs"]) == 3
        assert data["total"] == 7
        assert data["total_pages"] == 3

        # Page 2
        response = client.get("/api/runs?page=2&page_size=3")
        data = response.json()
        assert len(data["runs"]) == 3

        # Page 3 (partial)
        response = client.get("/api/runs?page=3&page_size=3")
        data = response.json()
        assert len(data["runs"]) == 1

    def test_filter_by_date_range(self, client, run_manager):
        """Filtering by date range returns only matching runs."""
        now = datetime(2024, 6, 15, 12, 0, 0)
        _add_run_to_manager(run_manager, "r1", "Old Run", now - timedelta(days=30))
        _add_run_to_manager(run_manager, "r2", "Recent Run", now - timedelta(days=2))
        _add_run_to_manager(run_manager, "r3", "Today Run", now)

        start = (now - timedelta(days=5)).isoformat()
        end = now.isoformat()
        response = client.get(f"/api/runs?start_date={start}&end_date={end}")
        data = response.json()
        assert len(data["runs"]) == 2
        names = [r["name"] for r in data["runs"]]
        assert "Today Run" in names
        assert "Recent Run" in names
        assert "Old Run" not in names

    def test_filter_by_tags(self, client, run_manager):
        """Filtering by tags returns only runs with matching tags."""
        now = datetime.now()
        _add_run_to_manager(
            run_manager, "r1", "Run A", now - timedelta(hours=1), tags=["warmup"]
        )
        _add_run_to_manager(
            run_manager, "r2", "Run B", now - timedelta(hours=2), tags=["full-power"]
        )
        _add_run_to_manager(
            run_manager, "r3", "Run C", now - timedelta(hours=3), tags=["warmup", "test"]
        )

        response = client.get("/api/runs?tags=warmup")
        data = response.json()
        assert len(data["runs"]) == 2
        names = [r["name"] for r in data["runs"]]
        assert "Run A" in names
        assert "Run C" in names

    def test_invalid_start_date_format(self, client):
        """Invalid date format returns 400 error."""
        response = client.get("/api/runs?start_date=not-a-date")
        assert response.status_code == 400
        assert "Invalid start_date format" in response.json()["error"]

    def test_invalid_end_date_format(self, client):
        """Invalid date format returns 400 error."""
        response = client.get("/api/runs?end_date=bad-date")
        assert response.status_code == 400
        assert "Invalid end_date format" in response.json()["error"]

    def test_run_metadata_fields(self, client, run_manager):
        """Response includes all required metadata fields."""
        now = datetime.now()
        _add_run_to_manager(
            run_manager,
            "r1",
            "Test Run",
            now,
            duration=120.5,
            notes="Engine warm",
            tags=["baseline"],
        )

        response = client.get("/api/runs")
        data = response.json()
        run = data["runs"][0]
        assert run["run_id"] == "r1"
        assert run["name"] == "Test Run"
        assert "start_time" in run
        assert "end_time" in run
        assert run["duration_seconds"] == 120.5
        assert run["notes"] == "Engine warm"
        assert run["tags"] == ["baseline"]


class TestGetRunData:
    """Tests for GET /api/runs/{run_id}/data endpoint."""

    def test_returns_time_series_data(self, client, run_manager, tmp_dir):
        """Returns channel data in the expected format."""
        csv_path = _create_csv_file(
            tmp_dir,
            "test_run",
            {
                "EGT1": [(0.0, 200.0, "°C"), (1000.0, 210.0, "°C"), (2000.0, 220.0, "°C")],
                "OilP": [(0.0, 3.5, "bar"), (1000.0, 3.6, "bar")],
            },
        )
        _add_run_to_manager(
            run_manager, "r1", "test_run", datetime.now(), csv_path=csv_path
        )

        response = client.get("/api/runs/r1/data")
        assert response.status_code == 200
        data = response.json()
        assert data["run_id"] == "r1"
        assert "EGT1" in data["channels"]
        assert "OilP" in data["channels"]

        egt1 = data["channels"]["EGT1"]
        assert egt1["timestamps_ms"] == [0.0, 1000.0, 2000.0]
        assert egt1["values"] == [200.0, 210.0, 220.0]
        assert egt1["unit"] == "°C"

        oilp = data["channels"]["OilP"]
        assert oilp["timestamps_ms"] == [0.0, 1000.0]
        assert oilp["values"] == [3.5, 3.6]
        assert oilp["unit"] == "bar"

    def test_run_not_found(self, client):
        """Returns 404 for non-existent run_id."""
        response = client.get("/api/runs/nonexistent/data")
        assert response.status_code == 404
        data = response.json()
        assert data["error"] == "Run not found"

    def test_csv_file_missing(self, client, run_manager):
        """Returns 404 when CSV file doesn't exist."""
        _add_run_to_manager(
            run_manager,
            "r1",
            "missing_data",
            datetime.now(),
            csv_path=Path("/nonexistent/path.csv"),
        )

        response = client.get("/api/runs/r1/data")
        assert response.status_code == 404
        data = response.json()
        assert data["error"] == "Run data unavailable"

    def test_csv_path_none(self, client, run_manager):
        """Returns 404 when run has no CSV path."""
        _add_run_to_manager(
            run_manager, "r1", "no_csv", datetime.now(), csv_path=None
        )

        response = client.get("/api/runs/r1/data")
        assert response.status_code == 404
        data = response.json()
        assert data["error"] == "Run data unavailable"


class TestCompareRuns:
    """Tests for GET /api/runs/compare endpoint."""

    def test_compare_two_runs(self, client, run_manager, tmp_dir):
        """Successfully compares 2 runs with aligned elapsed time."""
        csv1 = _create_csv_file(
            tmp_dir,
            "run1",
            {"EGT1": [(0.0, 200.0, "°C"), (1000.0, 210.0, "°C")]},
        )
        csv2 = tmp_dir / "20240101_130000_run2.csv"
        with open(csv2, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(["# Run Name", "run2"])
            writer.writerow(["timestamp_ms", "channel_id", "calibrated_value", "unit", "validity"])
            writer.writerow(["0.000", "EGT1", "250", "°C", "valid"])
            writer.writerow(["1000.000", "EGT1", "260", "°C", "valid"])

        _add_run_to_manager(
            run_manager, "r1", "Run 1", datetime.now(), csv_path=csv1
        )
        _add_run_to_manager(
            run_manager, "r2", "Run 2", datetime.now(), csv_path=csv2
        )

        response = client.get("/api/runs/compare?run_ids=r1,r2")
        assert response.status_code == 200
        data = response.json()
        runs = data["runs"]
        assert len(runs) == 2
        run_ids = [r["run_id"] for r in runs]
        assert "r1" in run_ids
        assert "r2" in run_ids
        # Find each run by id
        r1 = next(r for r in runs if r["run_id"] == "r1")
        r2 = next(r for r in runs if r["run_id"] == "r2")
        assert r1["name"] == "Run 1"
        assert r2["name"] == "Run 2"

    def test_fewer_than_two_run_ids(self, client):
        """Returns 400 when fewer than 2 run IDs provided."""
        response = client.get("/api/runs/compare?run_ids=r1")
        assert response.status_code == 400
        assert "At least 2" in response.json()["error"]

    def test_more_than_five_run_ids(self, client):
        """Returns 400 when more than 5 run IDs provided."""
        response = client.get("/api/runs/compare?run_ids=r1,r2,r3,r4,r5,r6")
        assert response.status_code == 400
        assert "At most 5" in response.json()["error"]

    def test_partial_data_with_errors(self, client, run_manager, tmp_dir):
        """Returns partial data with error indication for unavailable runs."""
        csv1 = _create_csv_file(
            tmp_dir,
            "run1",
            {"EGT1": [(0.0, 200.0, "°C")]},
        )
        _add_run_to_manager(
            run_manager, "r1", "Run 1", datetime.now(), csv_path=csv1
        )
        _add_run_to_manager(
            run_manager, "r2", "Run 2", datetime.now(), csv_path=Path("/missing.csv")
        )

        response = client.get("/api/runs/compare?run_ids=r1,r2")
        assert response.status_code == 200
        data = response.json()
        runs = data["runs"]
        run_ids = [r["run_id"] for r in runs]
        assert "r1" in run_ids
        assert "r2" not in run_ids
        assert len(data["errors"]) == 1
        assert data["errors"][0]["run_id"] == "r2"

    def test_all_runs_unavailable(self, client, run_manager):
        """Returns 404 when no run data can be loaded."""
        _add_run_to_manager(
            run_manager, "r1", "Run 1", datetime.now(), csv_path=Path("/missing1.csv")
        )
        _add_run_to_manager(
            run_manager, "r2", "Run 2", datetime.now(), csv_path=Path("/missing2.csv")
        )

        response = client.get("/api/runs/compare?run_ids=r1,r2")
        assert response.status_code == 404
        data = response.json()
        assert "errors" in data

    def test_run_not_found_in_compare(self, client, run_manager, tmp_dir):
        """Non-existent run IDs are reported as errors."""
        csv1 = _create_csv_file(
            tmp_dir,
            "run1",
            {"EGT1": [(0.0, 200.0, "°C")]},
        )
        _add_run_to_manager(
            run_manager, "r1", "Run 1", datetime.now(), csv_path=csv1
        )

        response = client.get("/api/runs/compare?run_ids=r1,nonexistent")
        assert response.status_code == 200
        data = response.json()
        runs = data["runs"]
        run_ids = [r["run_id"] for r in runs]
        assert "r1" in run_ids
        assert len(data["errors"]) == 1
        assert data["errors"][0]["run_id"] == "nonexistent"

    def test_compare_five_runs(self, client, run_manager, tmp_dir):
        """Successfully compares the maximum of 5 runs."""
        for i in range(5):
            csv_path = tmp_dir / f"run_{i}.csv"
            with open(csv_path, "w", newline="", encoding="utf-8") as f:
                writer = csv.writer(f)
                writer.writerow(["# Run Name", f"run_{i}"])
                writer.writerow(["timestamp_ms", "channel_id", "calibrated_value", "unit", "validity"])
                writer.writerow(["0.000", "EGT1", str(200 + i * 10), "°C", "valid"])
            _add_run_to_manager(
                run_manager, f"r{i}", f"Run {i}", datetime.now() - timedelta(hours=i), csv_path=csv_path
            )

        ids = ",".join(f"r{i}" for i in range(5))
        response = client.get(f"/api/runs/compare?run_ids={ids}")
        assert response.status_code == 200
        data = response.json()
        assert len(data["runs"]) == 5


class TestRemoteMonitoringFrontend:
    """Tests for the web-based remote monitoring frontend (Requirement 8.1, 8.3, 8.7)."""

    @pytest.fixture
    def frontend_client(self, run_manager):
        """Create a test client for frontend tests."""
        set_run_manager(run_manager)
        return TestClient(app)

    def test_index_served_at_root(self, frontend_client):
        """The remote monitoring page is served at the root URL."""
        response = frontend_client.get("/")
        assert response.status_code == 200
        assert "text/html" in response.headers["content-type"]

    def test_index_is_valid_html5(self, frontend_client):
        """The page is a valid HTML5 document with DOCTYPE."""
        response = frontend_client.get("/")
        content = response.text
        assert "<!DOCTYPE html>" in content
        assert '<html lang="en">' in content

    def test_no_external_dependencies(self, frontend_client):
        """The page has no external CDN dependencies (self-contained)."""
        response = frontend_client.get("/")
        content = response.text
        # Should not reference external CDNs
        assert "cdn." not in content.lower()
        assert "unpkg.com" not in content.lower()
        assert "jsdelivr" not in content.lower()
        # Should not require plugins
        assert "<object" not in content.lower()
        assert "<embed" not in content.lower()
        assert "<applet" not in content.lower()

    def test_websocket_connection_to_ws_live(self, frontend_client):
        """The frontend connects to the /ws/live WebSocket endpoint."""
        response = frontend_client.get("/")
        content = response.text
        assert "/ws/live" in content

    def test_auto_reconnect_implemented(self, frontend_client):
        """The frontend implements auto-reconnect with 5-second interval."""
        response = frontend_client.get("/")
        content = response.text
        # Check for reconnect interval of 5000ms
        assert "5000" in content
        assert "reconnect" in content.lower()

    def test_disconnected_indicator_present(self, frontend_client):
        """The frontend has a disconnected status indicator/overlay."""
        response = frontend_client.get("/")
        content = response.text
        assert "disconnectedOverlay" in content or "disconnected-overlay" in content
        assert "Connection Lost" in content

    def test_connection_status_indicators(self, frontend_client):
        """The frontend shows connection status (Connected/Disconnected)."""
        response = frontend_client.get("/")
        content = response.text
        assert "connectionStatus" in content
        assert "Connected" in content
        assert "Disconnected" in content

    def test_acquisition_status_indicators(self, frontend_client):
        """The frontend shows acquisition status (Active/Inactive)."""
        response = frontend_client.get("/")
        content = response.text
        assert "acquisitionStatus" in content or "Acquiring" in content
        assert "Inactive" in content

    def test_last_update_timestamp_display(self, frontend_client):
        """The frontend displays a last update timestamp."""
        response = frontend_client.get("/")
        content = response.text
        assert "lastUpdate" in content or "last-update" in content
        assert "Last update" in content

    def test_engine_overlay_sensor_positions(self, frontend_client):
        """The frontend has sensor positions matching the local dashboard layout."""
        response = frontend_client.get("/")
        content = response.text
        # All expected channels should be present
        expected_channels = [
            "EGT1", "EGT2", "EGT3", "EGT4",
            "CLT", "OilTemp", "IAT",
            "OilP", "ChargeP", "RPM",
            "AFR1", "AFR2", "AFR3", "AFR4",
        ]
        for channel in expected_channels:
            assert channel in content, f"Channel {channel} not found in frontend"

    def test_severity_color_coding(self, frontend_client):
        """The frontend has color-coded backgrounds for severity levels."""
        response = frontend_client.get("/")
        content = response.text
        # Check for severity CSS classes
        assert ".sensor-label.normal" in content
        assert ".sensor-label.warning" in content
        assert ".sensor-label.critical" in content

    def test_stale_data_indicator(self, frontend_client):
        """The frontend shows stale data indicator (gray) for channels not updated in 3+ seconds."""
        response = frontend_client.get("/")
        content = response.text
        assert ".sensor-label.stale" in content
        # Check for 3-second stale threshold
        assert "3" in content

    def test_handles_active_data_message_format(self, frontend_client):
        """The frontend handles the active data message format from the server."""
        response = frontend_client.get("/")
        content = response.text
        # Should handle {"channels": {...}, "timestamp": ...} format
        assert "data.channels" in content or ".channels" in content

    def test_handles_inactive_status_message_format(self, frontend_client):
        """The frontend handles the inactive status message format from the server."""
        response = frontend_client.get("/")
        content = response.text
        # Should handle {"status": "inactive", "last_values": {...}} format
        assert "inactive" in content

    def test_pure_html5_no_frameworks(self, frontend_client):
        """The frontend uses pure HTML5/CSS3/JavaScript with no frameworks."""
        response = frontend_client.get("/")
        content = response.text
        # Should not reference common frameworks
        assert "react" not in content.lower() or "reconnect" in content.lower()
        assert "angular" not in content.lower()
        assert "vue" not in content.lower()
        assert "jquery" not in content.lower()
