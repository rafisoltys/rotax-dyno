"""Unit tests for the web-based remote monitoring frontend (task 13.3).

Tests:
- Static HTML frontend is served at root URL
- WebSocket /ws/live endpoint streams live data
- Connection manager enforces max connections
- Auto-reconnect behavior (frontend-side, tested via WebSocket lifecycle)
- Broadcast payload includes alarm severity and stale indicators
"""

import asyncio
import json
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from rotax_dyno_daq.core.data_bus import DataBus
from rotax_dyno_daq.core.enums import AlarmSeverity, SampleValidity
from rotax_dyno_daq.core.models import ActiveAlarm, CalibratedSample
from rotax_dyno_daq.web.server import (
    ConnectionManager,
    STATIC_DIR,
    _latest_readings,
    app,
    configure_data_bus,
    connection_manager,
    get_broadcast_payload,
    set_acquisition_active,
    set_alarm_manager,
)


@pytest.fixture(autouse=True)
def reset_state():
    """Reset module-level state between tests."""
    _latest_readings.clear()
    set_acquisition_active(False)
    set_alarm_manager(None)
    # Reset connection manager
    connection_manager._active_connections.clear()
    yield
    _latest_readings.clear()


@pytest.fixture
def client():
    """Create a FastAPI test client."""
    return TestClient(app)


class TestStaticFrontend:
    """Tests for serving the static HTML5 frontend."""

    def test_index_html_served_at_root(self, client):
        """The root URL serves the index.html file (Requirement 8.1)."""
        response = client.get("/")
        assert response.status_code == 200
        assert "text/html" in response.headers["content-type"]
        # Verify it contains key elements of our frontend
        assert "Rotax 912 ULS" in response.text
        assert "Remote Monitor" in response.text

    def test_index_html_contains_websocket_connection(self, client):
        """Frontend includes WebSocket connection to /ws/live."""
        response = client.get("/")
        assert "/ws/live" in response.text

    def test_index_html_contains_engine_overlay(self, client):
        """Frontend includes engine overlay view with sensor positions."""
        response = client.get("/")
        assert "engineOverlay" in response.text
        # Check for sensor channel IDs
        assert "EGT1" in response.text
        assert "EGT2" in response.text
        assert "EGT3" in response.text
        assert "EGT4" in response.text
        assert "CLT" in response.text
        assert "OilTemp" in response.text
        assert "RPM" in response.text
        assert "AFR1" in response.text

    def test_index_html_contains_auto_reconnect(self, client):
        """Frontend includes auto-reconnect logic with 5-second interval."""
        response = client.get("/")
        assert "RECONNECT_INTERVAL_MS = 5000" in response.text
        assert "scheduleReconnect" in response.text

    def test_index_html_contains_connection_status_indicator(self, client):
        """Frontend includes connection status indicator."""
        response = client.get("/")
        assert "connectionStatus" in response.text
        assert "Disconnected" in response.text

    def test_index_html_contains_acquisition_status_indicator(self, client):
        """Frontend includes acquisition status indicator."""
        response = client.get("/")
        assert "acquisitionStatus" in response.text
        assert "Inactive" in response.text

    def test_index_html_no_plugins_required(self, client):
        """Frontend uses pure HTML5/JS/CSS with no external plugins."""
        response = client.get("/")
        # Should not reference any external JS frameworks or plugins
        assert "<script src=" not in response.text
        assert "<link rel=\"stylesheet\" href=\"http" not in response.text
        # Should be self-contained
        assert "<style>" in response.text
        assert "<script>" in response.text

    def test_index_html_contains_stale_indicator(self, client):
        """Frontend includes stale data indicator logic."""
        response = client.get("/")
        assert "STALE_THRESHOLD_S" in response.text
        assert "stale" in response.text

    def test_index_html_contains_severity_colors(self, client):
        """Frontend includes color-coded severity indicators."""
        response = client.get("/")
        # Check for severity CSS classes
        assert "normal" in response.text
        assert "warning" in response.text
        assert "critical" in response.text

    def test_index_html_responsive_layout(self, client):
        """Frontend includes responsive CSS for different screen sizes."""
        response = client.get("/")
        assert "@media" in response.text


class TestWebSocketLiveStream:
    """Tests for the /ws/live WebSocket endpoint."""

    def test_websocket_connection_accepted(self, client):
        """WebSocket connection is accepted when below max connections."""
        with client.websocket_connect("/ws/live") as ws:
            data = ws.receive_text()
            msg = json.loads(data)
            assert msg["type"] == "live_data"

    def test_websocket_sends_initial_state(self, client):
        """WebSocket sends initial state immediately on connection."""
        # Set up some readings
        _latest_readings["EGT1"] = {
            "channel_id": "EGT1",
            "value": 650.0,
            "unit": "°C",
            "validity": "valid",
            "timestamp": time.time(),
        }
        set_acquisition_active(True)

        with client.websocket_connect("/ws/live") as ws:
            data = ws.receive_text()
            msg = json.loads(data)
            assert msg["type"] == "live_data"
            assert msg["acquisition_active"] is True
            assert "EGT1" in msg["channels"]
            assert msg["channels"]["EGT1"]["value"] == 650.0
            assert msg["channels"]["EGT1"]["unit"] == "°C"

    def test_websocket_shows_acquisition_inactive(self, client):
        """WebSocket shows acquisition inactive status (Requirement 8.6)."""
        set_acquisition_active(False)

        with client.websocket_connect("/ws/live") as ws:
            data = ws.receive_text()
            msg = json.loads(data)
            assert msg["acquisition_active"] is False

    def test_websocket_includes_stale_indicator(self, client):
        """WebSocket payload marks stale channels (>3 seconds old)."""
        # Set a reading that's old (stale)
        _latest_readings["EGT1"] = {
            "channel_id": "EGT1",
            "value": 500.0,
            "unit": "°C",
            "validity": "valid",
            "timestamp": time.time() - 5.0,  # 5 seconds ago = stale
        }

        with client.websocket_connect("/ws/live") as ws:
            data = ws.receive_text()
            msg = json.loads(data)
            assert msg["channels"]["EGT1"]["stale"] is True

    def test_websocket_fresh_data_not_stale(self, client):
        """WebSocket payload marks fresh channels as not stale."""
        _latest_readings["EGT1"] = {
            "channel_id": "EGT1",
            "value": 500.0,
            "unit": "°C",
            "validity": "valid",
            "timestamp": time.time(),  # Just now
        }

        with client.websocket_connect("/ws/live") as ws:
            data = ws.receive_text()
            msg = json.loads(data)
            assert msg["channels"]["EGT1"]["stale"] is False

    def test_websocket_includes_alarm_severity(self, client):
        """WebSocket payload includes alarm severity from AlarmManager."""
        from datetime import datetime as dt

        _latest_readings["EGT1"] = {
            "channel_id": "EGT1",
            "value": 900.0,
            "unit": "°C",
            "validity": "valid",
            "timestamp": time.time(),
        }

        # Mock alarm manager
        mock_alarm_mgr = MagicMock()
        mock_alarm_mgr.get_active_alarms.return_value = [
            ActiveAlarm(
                alarm_id="a1",
                channel_id="EGT1",
                severity=AlarmSeverity.CRITICAL,
                triggered_at=dt.now(),
                value=900.0,
                threshold_crossed=850.0,
            )
        ]
        set_alarm_manager(mock_alarm_mgr)

        with client.websocket_connect("/ws/live") as ws:
            data = ws.receive_text()
            msg = json.loads(data)
            assert msg["channels"]["EGT1"]["severity"] == "critical"


class TestConnectionManager:
    """Tests for the WebSocket connection manager."""

    def test_max_connections_enforced(self, client):
        """Connections are rejected when max (3) is reached (Requirement 8.4, 8.5)."""
        # Open 3 connections (the max)
        ws_connections = []
        for _ in range(3):
            ws = client.websocket_connect("/ws/live")
            ws.__enter__()
            ws_connections.append(ws)
            # Consume initial message
            ws.receive_text()

        # 4th connection should be accepted but immediately get rejection message and close
        with client.websocket_connect("/ws/live") as ws4:
            data = ws4.receive_text()
            msg = json.loads(data)
            assert "error" in msg or "max_connections" in msg

        # Clean up
        for ws in ws_connections:
            ws.__exit__(None, None, None)

    def test_connection_freed_after_disconnect(self, client):
        """Disconnected clients free their slot for new connections."""
        # Open and close a connection
        with client.websocket_connect("/ws/live") as ws:
            ws.receive_text()

        # Should be able to connect again
        with client.websocket_connect("/ws/live") as ws:
            data = ws.receive_text()
            msg = json.loads(data)
            assert msg["type"] == "live_data"


class TestStatusEndpoint:
    """Tests for the /api/status endpoint."""

    def test_status_returns_system_info(self, client):
        """Status endpoint returns acquisition and connection info."""
        set_acquisition_active(True)
        _latest_readings["EGT1"] = {
            "channel_id": "EGT1",
            "value": 500.0,
            "unit": "°C",
            "validity": "valid",
            "timestamp": time.time(),
        }

        response = client.get("/api/status")
        assert response.status_code == 200
        data = response.json()
        assert data["acquisition_active"] is True
        assert data["channels_count"] == 1
        assert data["max_clients"] == 3
        assert "connected_clients" in data


class TestBroadcastPayload:
    """Tests for the broadcast payload generation."""

    def test_payload_structure(self):
        """Broadcast payload has correct structure."""
        _latest_readings["RPM"] = {
            "channel_id": "RPM",
            "value": 5500.0,
            "unit": "rpm",
            "validity": "valid",
            "timestamp": time.time(),
        }
        set_acquisition_active(True)

        payload = get_broadcast_payload()
        assert payload["type"] == "live_data"
        assert payload["acquisition_active"] is True
        assert "timestamp" in payload
        assert "channels" in payload
        assert "RPM" in payload["channels"]
        assert payload["channels"]["RPM"]["value"] == 5500.0
        assert payload["channels"]["RPM"]["unit"] == "rpm"
        assert "stale" in payload["channels"]["RPM"]
        assert "severity" in payload["channels"]["RPM"]

    def test_payload_default_severity_is_normal(self):
        """Channels without active alarms have 'normal' severity."""
        _latest_readings["OilP"] = {
            "channel_id": "OilP",
            "value": 4.0,
            "unit": "bar",
            "validity": "valid",
            "timestamp": time.time(),
        }

        payload = get_broadcast_payload()
        assert payload["channels"]["OilP"]["severity"] == "normal"

    def test_payload_empty_when_no_readings(self):
        """Payload has empty channels when no data received."""
        payload = get_broadcast_payload()
        assert payload["channels"] == {}
        assert payload["acquisition_active"] is False
