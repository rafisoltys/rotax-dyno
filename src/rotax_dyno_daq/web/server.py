"""FastAPI web server for remote monitoring and historical data browsing.

Implements:
- WebSocket live data streaming (Requirement 8)
- REST API for historical data browsing (Requirement 9)
- Static HTML5 frontend for remote monitoring (Requirement 8.1, 8.3, 8.7)
"""

from __future__ import annotations

import asyncio
import csv
import json
import logging
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from fastapi import FastAPI, Query, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.websockets import WebSocketState

from rotax_dyno_daq.core.data_bus import DataBus, Sample, SubscriptionId
from rotax_dyno_daq.core.enums import AlarmSeverity
from rotax_dyno_daq.core.models import RunSummary
from rotax_dyno_daq.storage.run_manager import RunFilters, RunManager, RunNotFoundError

logger = logging.getLogger(__name__)

# --- Constants ---

#: Maximum simultaneous WebSocket connections (default, configurable via SystemConfig).
MAX_CONNECTIONS: int = 3

#: WebSocket close code for capacity rejection (Try Again Later).
WS_CLOSE_CODE_TRY_AGAIN_LATER: int = 1013

#: Broadcast interval in seconds (minimum 1 Hz updates, ≤ 2s latency).
BROADCAST_INTERVAL_S: float = 0.5

#: Stale threshold in seconds.
STALE_THRESHOLD_S: float = 3.0

#: Path to static files directory.
STATIC_DIR = Path(__file__).parent / "static"

app = FastAPI(title="Rotax Dyno DAQ", version="0.1.0")


# --- WebSocket Connection Manager ---


class ConnectionManager:
    """Manages active WebSocket connections with a maximum limit.

    Enforces the configured maximum simultaneous connections (Requirement 8.4, 8.5).
    Rejects excess connections with WebSocket close code 1013 (Try Again Later).
    Thread-safe connection counting via internal lock.
    """

    def __init__(self, max_connections: int = MAX_CONNECTIONS) -> None:
        self._max_connections = max_connections
        self._active_connections: list[WebSocket] = []
        import threading
        self._lock = threading.Lock()

    @property
    def max_connections(self) -> int:
        """Maximum allowed simultaneous connections."""
        return self._max_connections

    @max_connections.setter
    def max_connections(self, value: int) -> None:
        """Update the maximum allowed simultaneous connections."""
        self._max_connections = value

    @property
    def active_count(self) -> int:
        """Number of currently active connections."""
        with self._lock:
            return len(self._active_connections)

    @property
    def is_full(self) -> bool:
        """Whether the connection limit has been reached."""
        return self.active_count >= self._max_connections

    async def connect(self, websocket: WebSocket) -> bool:
        """Accept a WebSocket connection if capacity allows.

        If at capacity, accepts the connection briefly to send a rejection
        message, then closes with code 1013 (Try Again Later) indicating
        the maximum number of monitoring sessions is active.

        Returns:
            True if connection was accepted, False if rejected.
        """
        with self._lock:
            if len(self._active_connections) >= self._max_connections:
                # Must release lock before async operations
                pass
            else:
                self._active_connections.append(websocket)
                await websocket.accept()
                return True

        # At capacity - accept, send rejection message, then close with 1013
        await websocket.accept()
        await websocket.send_json({
            "type": "error",
            "error": "Maximum number of monitoring sessions reached",
            "message": "Maximum number of monitoring sessions reached",
            "max_connections": self._max_connections,
        })
        await websocket.close(
            code=WS_CLOSE_CODE_TRY_AGAIN_LATER,
            reason="Maximum number of monitoring sessions reached",
        )
        return False

    def disconnect(self, websocket: WebSocket) -> None:
        """Remove a WebSocket connection from the active list, freeing the slot."""
        with self._lock:
            if websocket in self._active_connections:
                self._active_connections.remove(websocket)

    async def broadcast(self, message: str) -> None:
        """Send a JSON string to all active connections.

        Automatically removes connections that have disconnected.
        """
        with self._lock:
            connections = list(self._active_connections)

        disconnected: list[WebSocket] = []
        for connection in connections:
            try:
                if connection.client_state == WebSocketState.CONNECTED:
                    await connection.send_text(message)
            except Exception:
                disconnected.append(connection)
        for conn in disconnected:
            self.disconnect(conn)


# --- Shared State ---

connection_manager = ConnectionManager()

# Latest channel readings for broadcasting
_latest_readings: dict[str, dict[str, Any]] = {}
_acquisition_active: bool = False
_data_bus: Optional[DataBus] = None
_subscription_id: Optional[SubscriptionId] = None
_alarm_manager: Any = None  # Optional AlarmManager reference


def configure_server(
    max_connections: int = MAX_CONNECTIONS,
    port: int = 8080,
) -> None:
    """Configure the server with system settings.

    Args:
        max_connections: Maximum simultaneous WebSocket connections
            (from SystemConfig.max_remote_connections).
        port: Web server port (from SystemConfig.web_server_port).
    """
    connection_manager.max_connections = max_connections


def _on_sample_received(sample: Sample) -> None:
    """Handle incoming sample from the DataBus for WebSocket broadcasting."""
    channel_id = getattr(sample, "channel_id", None)
    calibrated_value = getattr(sample, "calibrated_value", None)
    unit = getattr(sample, "unit", "")
    validity = getattr(sample, "validity", None)
    timestamp_ms = getattr(sample, "timestamp_ms", None)

    if channel_id is None or calibrated_value is None:
        return

    validity_str = validity.value if validity else "valid"

    _latest_readings[channel_id] = {
        "channel_id": channel_id,
        "value": calibrated_value,
        "unit": unit,
        "validity": validity_str,
        "timestamp_ms": timestamp_ms if timestamp_ms is not None else time.time() * 1000,
        "timestamp": time.time(),
    }


def configure_data_bus(data_bus: DataBus) -> None:
    """Configure the server to subscribe to a DataBus for live data.

    Args:
        data_bus: The DataBus instance to subscribe to.
    """
    global _data_bus, _subscription_id
    _data_bus = data_bus
    _subscription_id = data_bus.subscribe("*", _on_sample_received)


def set_alarm_manager(alarm_manager: Any) -> None:
    """Set the AlarmManager instance for severity information.

    Args:
        alarm_manager: The AlarmManager instance.
    """
    global _alarm_manager
    _alarm_manager = alarm_manager


def set_acquisition_active(active: bool) -> None:
    """Set the acquisition status.

    Args:
        active: Whether data acquisition is currently active.
    """
    global _acquisition_active
    _acquisition_active = active


def _get_alarm_severities() -> dict[str, str]:
    """Get current alarm severity for each channel from AlarmManager."""
    severities: dict[str, str] = {}
    if _alarm_manager is None:
        return severities
    try:
        active_alarms = _alarm_manager.get_active_alarms()
        for alarm in active_alarms:
            channel_id = alarm.channel_id
            severity = alarm.severity.value if hasattr(alarm.severity, "value") else str(alarm.severity)
            # Use highest severity if multiple alarms for same channel
            if channel_id in severities:
                if severity == "critical":
                    severities[channel_id] = "critical"
            else:
                severities[channel_id] = severity
    except Exception:
        pass
    return severities


def get_broadcast_payload() -> dict[str, Any]:
    """Build the broadcast payload with current readings and status.

    When acquisition is active, returns:
    {
        "type": "live_data",
        "acquisition_active": true,
        "channels": {"EGT1": {"value": 650.0, "unit": "°C", "validity": "valid", "stale": false, "severity": "normal"}, ...},
        "timestamp": float
    }

    When acquisition is inactive, returns:
    {
        "type": "live_data",
        "acquisition_active": false,
        "status": "inactive",
        "channels": {"EGT1": {"value": 650.0, "unit": "°C", "validity": "valid"}, ...},
        "last_values": {"EGT1": {"value": 650.0, "unit": "°C", "validity": "valid"}, ...}
    }

    Requirement 8.2: Updates at minimum 1 Hz with max 2 seconds latency.
    Requirement 8.6: Shows acquisition-inactive status and last known values.
    """
    current_time = time.time()
    alarm_severities = _get_alarm_severities()

    if not _acquisition_active:
        # Acquisition inactive: send status with last known values (Requirement 8.6)
        last_values: dict[str, Any] = {}
        for channel_id, reading in _latest_readings.items():
            elapsed = current_time - reading["timestamp"]
            severity = alarm_severities.get(channel_id, "normal")
            last_values[channel_id] = {
                "value": reading["value"],
                "unit": reading["unit"],
                "validity": reading["validity"],
                "timestamp_ms": reading.get("timestamp_ms", reading["timestamp"] * 1000),
                "stale": elapsed >= STALE_THRESHOLD_S,
                "severity": severity,
            }
        return {
            "type": "live_data",
            "acquisition_active": False,
            "status": "inactive",
            "channels": last_values,
            "last_values": last_values,
        }

    # Acquisition active: send channel data with stale detection
    channels: dict[str, Any] = {}
    for channel_id, reading in _latest_readings.items():
        elapsed = current_time - reading["timestamp"]
        severity = alarm_severities.get(channel_id, "normal")
        channels[channel_id] = {
            "value": reading["value"],
            "unit": reading["unit"],
            "validity": reading["validity"],
            "timestamp_ms": reading.get("timestamp_ms", reading["timestamp"] * 1000),
            "stale": elapsed >= STALE_THRESHOLD_S,
            "severity": severity,
        }

    return {
        "type": "live_data",
        "acquisition_active": True,
        "channels": channels,
        "timestamp": current_time,
    }


# --- WebSocket Endpoint ---


@app.websocket("/ws/live")
async def live_data_stream(websocket: WebSocket) -> None:
    """Stream live channel data to connected clients.

    Streams at BROADCAST_INTERVAL_S intervals (≥1 Hz, ≤2s latency).
    Rejects connections when MAX_CONNECTIONS is reached (Requirement 8.4, 8.5).
    Shows acquisition-inactive status when not acquiring (Requirement 8.6).
    Handles client disconnection gracefully (Requirement 8.7).
    """
    accepted = await connection_manager.connect(websocket)
    if not accepted:
        return

    try:
        # Send initial state immediately
        payload = get_broadcast_payload()
        await websocket.send_text(json.dumps(payload))

        # Stream updates at >= 1 Hz
        while True:
            await asyncio.sleep(BROADCAST_INTERVAL_S)
            payload = get_broadcast_payload()
            await websocket.send_text(json.dumps(payload))
    except WebSocketDisconnect:
        pass
    except Exception:
        pass
    finally:
        connection_manager.disconnect(websocket)


# Module-level reference to RunManager, set during application startup
_run_manager: Optional[RunManager] = None


def set_run_manager(run_manager: RunManager) -> None:
    """Set the RunManager instance used by the API endpoints.

    Args:
        run_manager: The RunManager instance to use for data access.
    """
    global _run_manager
    _run_manager = run_manager


def get_run_manager() -> RunManager:
    """Get the RunManager instance.

    Raises:
        RuntimeError: If RunManager has not been configured.
    """
    if _run_manager is None:
        raise RuntimeError("RunManager not configured. Call set_run_manager() first.")
    return _run_manager


# --- Response Models ---


def _run_summary_to_dict(run: RunSummary) -> dict[str, Any]:
    """Convert a RunSummary to a JSON-serializable dict for API responses."""
    return {
        "run_id": run.run_id,
        "name": run.name,
        "start_time": run.start_time.isoformat(),
        "end_time": run.end_time.isoformat(),
        "duration_seconds": run.duration_seconds,
        "notes": run.notes,
        "tags": run.tags,
    }


# --- REST API Endpoints ---


@app.get("/api/runs")
async def list_runs(
    page: int = Query(default=1, ge=1, description="Page number (1-indexed)"),
    page_size: int = Query(
        default=50, ge=1, le=50, description="Number of runs per page (max 50)"
    ),
    start_date: Optional[str] = Query(
        default=None, description="Filter runs from this date (ISO 8601)"
    ),
    end_date: Optional[str] = Query(
        default=None, description="Filter runs up to this date (ISO 8601)"
    ),
    tags: Optional[str] = Query(
        default=None, description="Comma-separated list of tags to filter by"
    ),
) -> JSONResponse:
    """Paginated list of all runs with metadata, sorted by date descending.

    Implements Requirement 9.1, 9.4.
    """
    run_manager = get_run_manager()

    # Parse date filters
    parsed_start_date: Optional[datetime] = None
    parsed_end_date: Optional[datetime] = None

    if start_date:
        try:
            parsed_start_date = datetime.fromisoformat(start_date)
        except ValueError:
            return JSONResponse(
                status_code=400,
                content={"error": f"Invalid start_date format: '{start_date}'. Use ISO 8601."},
            )

    if end_date:
        try:
            parsed_end_date = datetime.fromisoformat(end_date)
        except ValueError:
            return JSONResponse(
                status_code=400,
                content={"error": f"Invalid end_date format: '{end_date}'. Use ISO 8601."},
            )

    # Parse tags filter
    parsed_tags: Optional[list[str]] = None
    if tags:
        parsed_tags = [t.strip() for t in tags.split(",") if t.strip()]

    # Build filters
    filters = RunFilters(
        start_date=parsed_start_date,
        end_date=parsed_end_date,
        tags=parsed_tags,
        page=page,
        page_size=page_size,
    )

    # Query run log
    runs = run_manager.get_run_log(filters=filters)

    # Get total count for pagination metadata (query without pagination)
    count_filters = RunFilters(
        start_date=parsed_start_date,
        end_date=parsed_end_date,
        tags=parsed_tags,
        page=1,
        page_size=999999,  # Large number to get all results
    )
    total_runs = len(run_manager.get_run_log(filters=count_filters))
    total_pages = max(1, (total_runs + page_size - 1) // page_size)

    return JSONResponse(
        status_code=200,
        content={
            "runs": [_run_summary_to_dict(r) for r in runs],
            "total": total_runs,
            "page": page,
            "page_size": page_size,
            "total_pages": total_pages,
        },
    )


@app.get("/api/runs/compare")
async def compare_runs(
    run_ids: str = Query(
        description="Comma-separated list of 2-5 run IDs to compare"
    ),
) -> JSONResponse:
    """Compare 2-5 runs aligned by elapsed time from run start.

    Implements Requirement 9.3, 9.5.
    """
    run_manager = get_run_manager()

    # Parse and validate run_ids
    ids = [rid.strip() for rid in run_ids.split(",") if rid.strip()]

    if len(ids) < 2:
        return JSONResponse(
            status_code=400,
            content={"error": "At least 2 run IDs are required for comparison."},
        )

    if len(ids) > 5:
        return JSONResponse(
            status_code=400,
            content={"error": "At most 5 run IDs can be compared at once."},
        )

    # Load data for each run
    results: list[dict[str, Any]] = []
    errors: list[dict[str, str]] = []

    for run_id in ids:
        try:
            run = run_manager._find_run(run_id)
        except RunNotFoundError:
            errors.append({"run_id": run_id, "error": f"Run '{run_id}' not found."})
            continue

        # Load time-series data from CSV
        run_data = _load_run_data(run)
        if run_data is None:
            errors.append(
                {"run_id": run_id, "error": f"Data unavailable for run '{run_id}'."}
            )
            continue

        # Align timestamps to elapsed time from run start (already relative in CSV)
        results.append({
            "run_id": run_id,
            "name": run.name,
            "channels": run_data,
        })

    # If no runs could be loaded at all, return 404
    if not results:
        return JSONResponse(
            status_code=404,
            content={
                "error": "No run data could be loaded for comparison.",
                "errors": errors,
            },
        )

    response: dict[str, Any] = {"runs": results}
    if errors:
        response["errors"] = errors

    return JSONResponse(status_code=200, content=response)


@app.get("/api/runs/{run_id}/data")
async def get_run_data(run_id: str) -> JSONResponse:
    """Retrieve historical run time-series data for charting.

    Implements Requirement 9.2, 9.5.
    Returns time-series data within 5 seconds of request.
    """
    run_manager = get_run_manager()

    # Find the run
    try:
        run = run_manager._find_run(run_id)
    except RunNotFoundError:
        return JSONResponse(
            status_code=404,
            content={"error": "Run not found"},
        )

    # Load time-series data from CSV
    run_data = _load_run_data(run)
    if run_data is None:
        return JSONResponse(
            status_code=404,
            content={"error": "Run data unavailable"},
        )

    return JSONResponse(
        status_code=200,
        content={
            "run_id": run_id,
            "channels": run_data,
        },
    )


# --- Data Loading Helpers ---


def _load_run_data(run: RunSummary) -> Optional[dict[str, Any]]:
    """Load time-series data from a run's CSV file.

    Returns channel data in the format:
    {
        "channel_id": {
            "timestamps_ms": [...],
            "values": [...],
            "unit": "°C"
        },
        ...
    }

    Returns None if the CSV file is unavailable.
    """
    if run.csv_path is None or not Path(run.csv_path).exists():
        return None

    channels: dict[str, dict[str, Any]] = {}

    try:
        with open(run.csv_path, "r", newline="", encoding="utf-8") as f:
            reader = csv.reader(f)
            for row in reader:
                if not row:
                    continue

                # Skip comment/metadata rows
                if row[0].startswith("#"):
                    continue

                # Skip header row
                if row[0] == "timestamp_ms":
                    continue

                # Parse data rows: timestamp_ms, channel_id, calibrated_value, unit, validity
                if len(row) < 5:
                    continue

                try:
                    timestamp_ms = float(row[0])
                    channel_id = row[1]
                    calibrated_value = float(row[2])
                    unit = row[3]
                    # validity = row[4]  # Could filter out invalid samples if needed
                except (ValueError, IndexError):
                    continue

                if channel_id not in channels:
                    channels[channel_id] = {
                        "timestamps_ms": [],
                        "values": [],
                        "unit": unit,
                    }

                channels[channel_id]["timestamps_ms"].append(timestamp_ms)
                channels[channel_id]["values"].append(calibrated_value)

    except OSError as e:
        logger.error(f"Error reading CSV file for run '{run.run_id}': {e}")
        return None

    return channels


# --- Status Endpoint ---


@app.get("/api/status")
async def get_status() -> JSONResponse:
    """Get current system status including connection and acquisition info."""
    return JSONResponse(
        status_code=200,
        content={
            "acquisition_active": _acquisition_active,
            "connected_clients": connection_manager.active_count,
            "max_clients": MAX_CONNECTIONS,
            "channels_count": len(_latest_readings),
        },
    )


# --- Static File Serving ---


@app.get("/", response_class=HTMLResponse)
async def serve_index() -> HTMLResponse:
    """Serve the main remote monitoring page.

    Implements Requirement 8.1 - web-based interface accessible without plugins.
    """
    index_path = STATIC_DIR / "index.html"
    if index_path.exists():
        return HTMLResponse(content=index_path.read_text(encoding="utf-8"))
    return HTMLResponse(
        content="<h1>Remote monitoring frontend not found</h1>",
        status_code=404,
    )


# Mount static files (CSS, JS, images) at /static/
if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
