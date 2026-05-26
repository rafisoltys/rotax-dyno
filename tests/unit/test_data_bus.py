"""Unit tests for the DataBus pub/sub implementation."""

import threading
from unittest.mock import MagicMock

import pytest

from rotax_dyno_daq.core.data_bus import DataBus


class TestDataBusSubscribe:
    """Tests for subscribe functionality."""

    def test_subscribe_returns_unique_ids(self) -> None:
        bus = DataBus()
        id1 = bus.subscribe("temp", lambda s: None)
        id2 = bus.subscribe("temp", lambda s: None)
        id3 = bus.subscribe("pressure", lambda s: None)
        assert id1 != id2
        assert id2 != id3

    def test_subscribe_to_same_topic_multiple_times(self) -> None:
        bus = DataBus()
        callbacks_called: list[int] = []
        bus.subscribe("temp", lambda s: callbacks_called.append(1))
        bus.subscribe("temp", lambda s: callbacks_called.append(2))
        bus.publish("temp", {"value": 100})
        assert callbacks_called == [1, 2]


class TestDataBusPublish:
    """Tests for publish functionality."""

    def test_publish_delivers_to_topic_subscribers(self) -> None:
        bus = DataBus()
        received: list = []
        bus.subscribe("egt1", lambda s: received.append(s))
        sample = {"channel_id": "egt1", "value": 650.0}
        bus.publish("egt1", sample)
        assert received == [sample]

    def test_publish_does_not_deliver_to_other_topics(self) -> None:
        bus = DataBus()
        received: list = []
        bus.subscribe("egt1", lambda s: received.append(s))
        bus.publish("oil_pressure", {"value": 3.5})
        assert received == []

    def test_publish_to_topic_with_no_subscribers(self) -> None:
        bus = DataBus()
        # Should not raise
        bus.publish("nonexistent", {"value": 42})

    def test_wildcard_subscriber_receives_all_topics(self) -> None:
        bus = DataBus()
        received: list = []
        bus.subscribe("*", lambda s: received.append(s))
        bus.publish("egt1", {"value": 1})
        bus.publish("oil_pressure", {"value": 2})
        bus.publish("rpm", {"value": 3})
        assert len(received) == 3

    def test_wildcard_and_topic_subscriber_both_receive(self) -> None:
        bus = DataBus()
        topic_received: list = []
        wildcard_received: list = []
        bus.subscribe("egt1", lambda s: topic_received.append(s))
        bus.subscribe("*", lambda s: wildcard_received.append(s))
        sample = {"value": 650}
        bus.publish("egt1", sample)
        assert topic_received == [sample]
        assert wildcard_received == [sample]

    def test_publish_to_wildcard_topic_only_delivers_to_wildcard_subs(self) -> None:
        """Publishing to '*' topic should only go to '*' subscribers, not all."""
        bus = DataBus()
        wildcard_received: list = []
        topic_received: list = []
        bus.subscribe("*", lambda s: wildcard_received.append(s))
        bus.subscribe("egt1", lambda s: topic_received.append(s))
        bus.publish("*", {"value": 99})
        assert wildcard_received == [{"value": 99}]
        assert topic_received == []


class TestDataBusUnsubscribe:
    """Tests for unsubscribe functionality."""

    def test_unsubscribe_stops_delivery(self) -> None:
        bus = DataBus()
        received: list = []
        sub_id = bus.subscribe("temp", lambda s: received.append(s))
        bus.publish("temp", {"value": 1})
        bus.unsubscribe(sub_id)
        bus.publish("temp", {"value": 2})
        assert received == [{"value": 1}]

    def test_unsubscribe_nonexistent_id_is_noop(self) -> None:
        bus = DataBus()
        # Should not raise
        bus.unsubscribe(9999)

    def test_unsubscribe_only_removes_target(self) -> None:
        bus = DataBus()
        received_a: list = []
        received_b: list = []
        sub_a = bus.subscribe("temp", lambda s: received_a.append(s))
        bus.subscribe("temp", lambda s: received_b.append(s))
        bus.unsubscribe(sub_a)
        bus.publish("temp", {"value": 42})
        assert received_a == []
        assert received_b == [{"value": 42}]

    def test_unsubscribe_during_publish_is_safe(self) -> None:
        """Unsubscribing while publish is iterating should not cause errors."""
        bus = DataBus()
        received: list = []

        def callback_that_unsubscribes(sample: object) -> None:
            received.append(sample)
            bus.unsubscribe(sub_id)

        sub_id = bus.subscribe("temp", callback_that_unsubscribes)
        bus.subscribe("temp", lambda s: received.append("second"))

        # Should not raise even though first callback unsubscribes itself
        bus.publish("temp", {"value": 1})
        assert {"value": 1} in received


class TestDataBusThreadSafety:
    """Tests for thread-safe operation."""

    def test_concurrent_publish_and_subscribe(self) -> None:
        bus = DataBus()
        received: list = []
        lock = threading.Lock()

        def safe_append(s: object) -> None:
            with lock:
                received.append(s)

        bus.subscribe("data", safe_append)

        def publisher() -> None:
            for i in range(100):
                bus.publish("data", {"i": i})

        def subscriber() -> None:
            for i in range(50):
                bus.subscribe("data", safe_append)

        threads = [
            threading.Thread(target=publisher),
            threading.Thread(target=publisher),
            threading.Thread(target=subscriber),
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # The initial subscriber should have received all 200 publishes
        # Additional subscribers may receive fewer depending on timing
        assert len(received) >= 200

    def test_concurrent_unsubscribe_during_publish(self) -> None:
        bus = DataBus()
        sub_ids: list[int] = []

        for _ in range(50):
            sub_ids.append(bus.subscribe("ch", lambda s: None))

        def unsubscriber() -> None:
            for sid in sub_ids:
                bus.unsubscribe(sid)

        def publisher() -> None:
            for _ in range(100):
                bus.publish("ch", {"v": 1})

        threads = [
            threading.Thread(target=unsubscriber),
            threading.Thread(target=publisher),
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        # Should complete without errors
