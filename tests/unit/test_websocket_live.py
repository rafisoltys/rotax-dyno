"""Unit tests for the WebSocket /ws/live endpoint and ConnectionManager.

Tests connection limiting, message format, graceful disconnection,
and acquisition-inactive status behavior.

Requirements: 8.1, 8.2, 8.4, 8.5, 8.6, 8.7
"""

from __future__ import annotations

import json
import time
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from rotax_dyno_daq.core.enums import SampleValidity
from rotax_dyno_daq.core.models import CalibratedSample
from rotax_dyno_daq.web.server import (
    ConnectionManager,
    WS_CLOSE_CODE_TRY_AGAIN_LATER,
    app,
    connection_manager,
    configure_server,
    get_broadcast_payload,
    set_acquisition_active,
    _latest_readings,
    _on_sample_received,
)


@pytest.fixture(autouse=True)
def reset_server_state():
    """Reset server state between tests."""
    _latest_readings.clear()
    set_acquisition_active(False)
    # Reset connection manager
    connection_manager._active_connections.clear()
    connection_manager.max_connections = 3
    yield
    _latest_readings.clear()
    set_acquisition_active(False)
    connection_manager._active_connections.clear()
    connection_manager.max_connections = 3


@pytest.fixture
def client():
    """Create a FastAPI test client."""
    return TestClient(app)


class TestConnectionManager:
    """Tests for the ConnectionManager class."""

    def test_initial_state(self):
        """ConnectionManager starts with zero active connections."""
        mgr = ConnectionManager(max_connections=3)
        assert mgr.active_count == 0
        assert mgr.is_full is False
        assert mgr.max_connections == 3

    def test_max_connections_configurable(self):
        """Max connections can be set via configure_server."""
        configure_server(max_connections=5)
        assert connection_manager.max_connections == 5

    def test_max_connections_setter(self):
        """Max connections can be updated via property setter."""
        mgr = ConnectionManager(max_connections=3)
        mgr.max_connections = 5
        assert mgr.max_connections == 5


class TestWebSocketLiveEndpoint:
    """Tests for the /ws/live WebSocket endpoint."""

    def test_connect_and_receive_initial_message(self, client):
        """Client receives an initial message immediately on connect."""
        with client.websocket_connect("/ws/live") as ws:
            data = ws.receive_json()
            # When acquisition is inactive, should get live_data message with inactive status
            assert data["type"] == "live_data"
            assert data["acquisition_active"] is False

    def test_message_format_acquisition_inactive(self, client):
        """When acquisition is inactive, message has type=live_data with last_values."""
        # Add some readings to simulate last known values
        _latest_readings["EGT1"] = {
            "channel_id": "EGT1",
            "value": 650.0,
            "unit": "°C",
            "validity": "valid",
            "timestamp_ms": 1234567890.123,
            "timestamp": time.time(),
        }
        set_acquisition_active(False)

        with client.websocket_connect("/ws/live") as ws:
            data = ws.receive_json()
            assert data["type"] == "live_data"
            assert data["acquisition_active"] is False
            assert "last_values" in data
            assert "EGT1" in data["last_values"]
            egt1 = data["last_values"]["EGT1"]
            assert egt1["value"] == 650.0
            assert egt1["unit"] == "°C"
            assert egt1["validity"] == "valid"

    def test_message_format_acquisition_active(self, client):
        """When acquisition is active, message has type=live_data with channels and timestamp."""
        _latest_readings["EGT1"] = {
            "channel_id": "EGT1",
            "value": 650.0,
            "unit": "°C",
            "validity": "valid",
            "timestamp_ms": 1234567890.123,
            "timestamp": time.time(),
        }
        set_acquisition_active(True)

        with client.websocket_connect("/ws/live") as ws:
            data = ws.receive_json()
            assert data["type"] == "live_data"
            assert "channels" in data
            assert "timestamp" in data
            assert "EGT1" in data["channels"]
            egt1 = data["channels"]["EGT1"]
            assert egt1["value"] == 650.0
            assert egt1["unit"] == "°C"
            assert egt1["validity"] == "valid"

    def test_connection_limit_rejects_fourth_connection(self, client):
        """Fourth connection is rejected when max is 3."""
        connection_manager.max_connections = 3

        # Open 3 connections
        with client.websocket_connect("/ws/live") as ws1:
            ws1.receive_json()  # consume initial message
            with client.websocket_connect("/ws/live") as ws2:
                ws2.receive_json()
                with client.websocket_connect("/ws/live") as ws3:
                    ws3.receive_json()

                    # Fourth connection should be rejected
                    with client.websocket_connect("/ws/live") as ws4:
                        # Should receive error message before close
                        data = ws4.receive_json()
                        assert data["type"] == "error"
                        assert "Maximum" in data["message"]
                        assert data["max_connections"] == 3

    def test_connection_slot_freed_on_disconnect(self, client):
        """After a client disconnects, the slot is freed for new connections."""
        connection_manager.max_connections = 1

        # First connection
        with client.websocket_connect("/ws/live") as ws1:
            ws1.receive_json()
            assert connection_manager.active_count == 1

        # After disconnect, slot should be free
        assert connection_manager.active_count == 0

        # New connection should succeed
        with client.websocket_connect("/ws/live") as ws2:
            data = ws2.receive_json()
            assert data["type"] == "live_data"  # Not an error

    def test_graceful_disconnect_removes_from_active(self, client):
        """Client disconnection removes it from active connections list."""
        with client.websocket_connect("/ws/live") as ws:
            ws.receive_json()
            assert connection_manager.active_count == 1

        # After context manager exits (disconnect), count should be 0
        assert connection_manager.active_count == 0

    def test_multiple_channels_in_broadcast(self, client):
        """Multiple channels are included in the broadcast payload."""
        now = time.time()
        _latest_readings["EGT1"] = {
            "channel_id": "EGT1",
            "value": 650.0,
            "unit": "°C",
            "validity": "valid",
            "timestamp_ms": 1000.0,
            "timestamp": now,
        }
        _latest_readings["OilP"] = {
            "channel_id": "OilP",
            "value": 3.5,
            "unit": "bar",
            "validity": "valid",
            "timestamp_ms": 1000.0,
            "timestamp": now,
        }
        set_acquisition_active(True)

        with client.websocket_connect("/ws/live") as ws:
            data = ws.receive_json()
            assert "EGT1" in data["channels"]
            assert "OilP" in data["channels"]
            assert data["channels"]["EGT1"]["value"] == 650.0
            assert data["channels"]["OilP"]["value"] == 3.5


class TestOnSampleReceived:
    """Tests for the _on_sample_received callback."""

    def test_stores_calibrated_sample(self):
        """CalibratedSample is stored in _latest_readings."""
        sample = CalibratedSample(
            channel_id="EGT1",
            timestamp_ms=5000.0,
            raw_value=2.5,
            calibrated_value=650.0,
            unit="°C",
            validity=SampleValidity.VALID,
        )
        _on_sample_received(sample)

        assert "EGT1" in _latest_readings
        assert _latest_readings["EGT1"]["value"] == 650.0
        assert _latest_readings["EGT1"]["unit"] == "°C"
        assert _latest_readings["EGT1"]["validity"] == "valid"

    def test_updates_existing_channel(self):
        """New sample for same channel overwrites previous value."""
        sample1 = CalibratedSample(
            channel_id="EGT1",
            timestamp_ms=1000.0,
            raw_value=2.0,
            calibrated_value=600.0,
            unit="°C",
            validity=SampleValidity.VALID,
        )
        sample2 = CalibratedSample(
            channel_id="EGT1",
            timestamp_ms=2000.0,
            raw_value=2.5,
            calibrated_value=700.0,
            unit="°C",
            validity=SampleValidity.VALID,
        )
        _on_sample_received(sample1)
        _on_sample_received(sample2)

        assert _latest_readings["EGT1"]["value"] == 700.0

    def test_ignores_sample_without_channel_id(self):
        """Samples without channel_id are ignored."""
        class FakeSample:
            calibrated_value = 100.0
        
        _on_sample_received(FakeSample())
        assert len(_latest_readings) == 0

    def test_handles_invalid_validity(self):
        """Samples with non-VALID validity are stored with correct validity string."""
        sample = CalibratedSample(
            channel_id="OilP",
            timestamp_ms=1000.0,
            raw_value=0.1,
            calibrated_value=0.5,
            unit="bar",
            validity=SampleValidity.INVALID,
        )
        _on_sample_received(sample)

        assert _latest_readings["OilP"]["validity"] == "invalid"


class TestGetBroadcastPayload:
    """Tests for the get_broadcast_payload function."""

    def test_empty_readings_inactive(self):
        """Empty readings with inactive acquisition returns live_data message."""
        set_acquisition_active(False)
        payload = get_broadcast_payload()
        assert payload["type"] == "live_data"
        assert payload["acquisition_active"] is False
        assert payload["last_values"] == {}

    def test_empty_readings_active(self):
        """Empty readings with active acquisition returns live_data message."""
        set_acquisition_active(True)
        payload = get_broadcast_payload()
        assert payload["type"] == "live_data"
        assert payload["channels"] == {}
        assert "timestamp" in payload

    def test_stale_detection(self):
        """Channels not updated for > 3 seconds are marked stale."""
        _latest_readings["EGT1"] = {
            "channel_id": "EGT1",
            "value": 650.0,
            "unit": "°C",
            "validity": "valid",
            "timestamp_ms": 1000.0,
            "timestamp": time.time() - 4.0,  # 4 seconds ago = stale
        }
        set_acquisition_active(True)

        payload = get_broadcast_payload()
        assert payload["channels"]["EGT1"]["stale"] is True

    def test_fresh_data_not_stale(self):
        """Channels updated recently are not marked stale."""
        _latest_readings["EGT1"] = {
            "channel_id": "EGT1",
            "value": 650.0,
            "unit": "°C",
            "validity": "valid",
            "timestamp_ms": 1000.0,
            "timestamp": time.time(),  # just now
        }
        set_acquisition_active(True)

        payload = get_broadcast_payload()
        assert payload["channels"]["EGT1"]["stale"] is False


class TestConfigureServer:
    """Tests for the configure_server function."""

    def test_configure_max_connections(self):
        """configure_server updates the connection manager's max connections."""
        configure_server(max_connections=5)
        assert connection_manager.max_connections == 5

    def test_default_max_connections(self):
        """Default max connections is 3."""
        configure_server()
        assert connection_manager.max_connections == 3
