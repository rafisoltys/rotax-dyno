"""Unit tests for the FastAPI WebSocket server for live data streaming.

Tests /ws/live endpoint, connection limiting, disconnection handling,
and acquisition-inactive status.
"""

import json
import time

import pytest
from fastapi.testclient import TestClient

from rotax_dyno_daq.core.data_bus import DataBus
from rotax_dyno_daq.core.enums import SampleValidity
from rotax_dyno_daq.core.models import CalibratedSample
from rotax_dyno_daq.web.server import (
    ConnectionManager,
    app,
    configure_data_bus,
    connection_manager,
    get_broadcast_payload,
    set_acquisition_active,
    _latest_readings,
)


@pytest.fixture(autouse=True)
def reset_state():
    """Reset module-level state before each test."""
    _latest_readings.clear()
    set_acquisition_active(False)
    # Reset connection manager's internal state
    connection_manager._active_connections.clear()
    yield
    _latest_readings.clear()
    set_acquisition_active(False)
    connection_manager._active_connections.clear()


@pytest.fixture
def data_bus():
    """Create a fresh DataBus instance."""
    return DataBus()


@pytest.fixture
def client():
    """Create a FastAPI test client."""
    return TestClient(app)


class TestConnectionManager:
    """Tests for the ConnectionManager class."""

    def test_initial_state(self):
        """ConnectionManager starts with no connections."""
        mgr = ConnectionManager(max_connections=3)
        assert mgr.active_count == 0
        assert mgr.is_full is False
        assert mgr.max_connections == 3

    def test_max_connections_property(self):
        """max_connections reflects configured limit."""
        mgr = ConnectionManager(max_connections=5)
        assert mgr.max_connections == 5

    def test_is_full_at_capacity(self, client):
        """is_full returns True when at max connections."""
        # Use the module-level connection_manager with max 3
        set_acquisition_active(True)

        connections = []
        for _ in range(3):
            ws = client.websocket_connect("/ws/live")
            ctx = ws.__enter__()
            ctx.receive_json()  # consume initial message
            connections.append((ws, ctx))

        assert connection_manager.active_count == 3
        assert connection_manager.is_full is True

        # Clean up
        for ws, ctx in connections:
            ws.__exit__(None, None, None)


class TestWebSocketLiveEndpoint:
    """Tests for the /ws/live WebSocket endpoint."""

    def test_connect_and_receive_data_when_acquiring(self, client, data_bus):
        """Client connects and receives live channel data when acquiring."""
        configure_data_bus(data_bus)
        set_acquisition_active(True)

        sample = CalibratedSample(
            channel_id="EGT1",
            timestamp_ms=1000.0,
            raw_value=3.2,
            calibrated_value=650.0,
            unit="°C",
            validity=SampleValidity.VALID,
        )
        data_bus.publish("EGT1", sample)

        with client.websocket_connect("/ws/live") as ws:
            data = ws.receive_json()
            assert "channels" in data
            assert "timestamp" in data
            assert data["channels"]["EGT1"]["value"] == 650.0
            assert data["channels"]["EGT1"]["unit"] == "°C"
            assert data["channels"]["EGT1"]["validity"] == "valid"

    def test_inactive_status_when_not_acquiring(self, client, data_bus):
        """Client receives inactive status with last values when not acquiring."""
        configure_data_bus(data_bus)
        set_acquisition_active(False)

        sample = CalibratedSample(
            channel_id="OilP",
            timestamp_ms=2000.0,
            raw_value=2.5,
            calibrated_value=4.2,
            unit="bar",
            validity=SampleValidity.VALID,
        )
        data_bus.publish("OilP", sample)

        with client.websocket_connect("/ws/live") as ws:
            data = ws.receive_json()
            assert data["status"] == "inactive"
            assert "last_values" in data
            assert data["last_values"]["OilP"]["value"] == 4.2
            assert data["last_values"]["OilP"]["unit"] == "bar"

    def test_connection_rejected_at_capacity(self, client):
        """Fourth connection is rejected with capacity message when max is 3."""
        set_acquisition_active(True)

        # Open 3 connections
        connections = []
        for _ in range(3):
            ws = client.websocket_connect("/ws/live")
            ctx = ws.__enter__()
            ctx.receive_json()  # consume initial message
            connections.append((ws, ctx))

        assert connection_manager.active_count == 3

        # Fourth connection should get rejection message then close
        with client.websocket_connect("/ws/live") as ws4:
            data = ws4.receive_json()
            assert "error" in data
            assert "Maximum number of monitoring sessions reached" in data["error"]
            assert data["max_connections"] == 3

        # Clean up
        for ws, ctx in connections:
            ws.__exit__(None, None, None)

    def test_disconnection_frees_slot(self, client, data_bus):
        """Disconnecting a client frees the connection slot."""
        configure_data_bus(data_bus)
        set_acquisition_active(True)

        sample = CalibratedSample(
            channel_id="EGT1",
            timestamp_ms=1000.0,
            raw_value=3.2,
            calibrated_value=650.0,
            unit="°C",
            validity=SampleValidity.VALID,
        )
        data_bus.publish("EGT1", sample)

        # Connect and disconnect
        with client.websocket_connect("/ws/live") as ws:
            ws.receive_json()
            assert connection_manager.active_count == 1

        # After disconnect, slot should be freed
        assert connection_manager.active_count == 0

    def test_slot_freed_allows_new_connection(self, client, data_bus):
        """After a client disconnects, a new client can connect."""
        configure_data_bus(data_bus)
        set_acquisition_active(True)

        sample = CalibratedSample(
            channel_id="EGT1",
            timestamp_ms=1000.0,
            raw_value=3.2,
            calibrated_value=650.0,
            unit="°C",
            validity=SampleValidity.VALID,
        )
        data_bus.publish("EGT1", sample)

        # Fill all 3 slots
        connections = []
        for _ in range(3):
            ws = client.websocket_connect("/ws/live")
            ctx = ws.__enter__()
            ctx.receive_json()
            connections.append((ws, ctx))

        assert connection_manager.active_count == 3

        # Disconnect one
        ws, ctx = connections.pop()
        ws.__exit__(None, None, None)
        assert connection_manager.active_count == 2

        # New connection should succeed
        with client.websocket_connect("/ws/live") as ws_new:
            data = ws_new.receive_json()
            assert "channels" in data  # Not an error message

        # Clean up remaining
        for ws, ctx in connections:
            ws.__exit__(None, None, None)

    def test_connection_count_tracking(self, client, data_bus):
        """Connection count is accurately tracked through connect/disconnect."""
        configure_data_bus(data_bus)
        set_acquisition_active(True)

        sample = CalibratedSample(
            channel_id="EGT1",
            timestamp_ms=1000.0,
            raw_value=3.2,
            calibrated_value=650.0,
            unit="°C",
            validity=SampleValidity.VALID,
        )
        data_bus.publish("EGT1", sample)

        assert connection_manager.active_count == 0

        with client.websocket_connect("/ws/live") as ws1:
            ws1.receive_json()
            assert connection_manager.active_count == 1

            with client.websocket_connect("/ws/live") as ws2:
                ws2.receive_json()
                assert connection_manager.active_count == 2

            # ws2 disconnected
            assert connection_manager.active_count == 1

        # ws1 disconnected
        assert connection_manager.active_count == 0

    def test_multiple_channels_in_message(self, client, data_bus):
        """Multiple channels are included in the broadcast message."""
        configure_data_bus(data_bus)
        set_acquisition_active(True)

        for ch_id, value, unit in [
            ("EGT1", 650.0, "°C"),
            ("EGT2", 680.0, "°C"),
            ("OilP", 4.2, "bar"),
            ("RPM", 3500.0, "RPM"),
        ]:
            sample = CalibratedSample(
                channel_id=ch_id,
                timestamp_ms=1000.0,
                raw_value=1.0,
                calibrated_value=value,
                unit=unit,
                validity=SampleValidity.VALID,
            )
            data_bus.publish(ch_id, sample)

        with client.websocket_connect("/ws/live") as ws:
            data = ws.receive_json()
            assert len(data["channels"]) == 4
            assert data["channels"]["EGT1"]["value"] == 650.0
            assert data["channels"]["EGT2"]["value"] == 680.0
            assert data["channels"]["OilP"]["value"] == 4.2
            assert data["channels"]["RPM"]["value"] == 3500.0

    def test_non_calibrated_sample_ignored(self, client, data_bus):
        """Non-CalibratedSample messages are not included in broadcast."""
        configure_data_bus(data_bus)
        set_acquisition_active(True)

        # Publish a non-CalibratedSample
        data_bus.publish("some_topic", {"arbitrary": "data"})

        with client.websocket_connect("/ws/live") as ws:
            data = ws.receive_json()
            assert data["channels"] == {}

    def test_latest_value_overwrites_previous(self, client, data_bus):
        """Latest sample value overwrites previous for the same channel."""
        configure_data_bus(data_bus)
        set_acquisition_active(True)

        # First sample
        sample1 = CalibratedSample(
            channel_id="EGT1",
            timestamp_ms=1000.0,
            raw_value=3.0,
            calibrated_value=600.0,
            unit="°C",
            validity=SampleValidity.VALID,
        )
        data_bus.publish("EGT1", sample1)

        # Second sample overwrites
        sample2 = CalibratedSample(
            channel_id="EGT1",
            timestamp_ms=2000.0,
            raw_value=3.5,
            calibrated_value=700.0,
            unit="°C",
            validity=SampleValidity.VALID,
        )
        data_bus.publish("EGT1", sample2)

        with client.websocket_connect("/ws/live") as ws:
            data = ws.receive_json()
            assert data["channels"]["EGT1"]["value"] == 700.0


class TestGetBroadcastPayload:
    """Tests for the get_broadcast_payload function."""

    def test_acquiring_message_format(self, data_bus):
        """When acquiring, payload has channels and timestamp."""
        configure_data_bus(data_bus)
        set_acquisition_active(True)

        sample = CalibratedSample(
            channel_id="EGT1",
            timestamp_ms=1000.0,
            raw_value=3.2,
            calibrated_value=650.0,
            unit="°C",
            validity=SampleValidity.VALID,
        )
        data_bus.publish("EGT1", sample)

        payload = get_broadcast_payload()
        assert "channels" in payload
        assert "timestamp" in payload
        assert "status" not in payload
        assert payload["channels"]["EGT1"]["value"] == 650.0

    def test_inactive_message_format(self, data_bus):
        """When not acquiring, payload has status=inactive and last_values."""
        configure_data_bus(data_bus)
        set_acquisition_active(False)

        sample = CalibratedSample(
            channel_id="RPM",
            timestamp_ms=3000.0,
            raw_value=1.5,
            calibrated_value=3500.0,
            unit="RPM",
            validity=SampleValidity.VALID,
        )
        data_bus.publish("RPM", sample)

        payload = get_broadcast_payload()
        assert payload["status"] == "inactive"
        assert "last_values" in payload
        assert payload["last_values"]["RPM"]["value"] == 3500.0

    def test_empty_channels_when_no_data(self):
        """Payload has empty channels when no data received."""
        set_acquisition_active(True)
        payload = get_broadcast_payload()
        assert payload["channels"] == {}
        assert "timestamp" in payload
