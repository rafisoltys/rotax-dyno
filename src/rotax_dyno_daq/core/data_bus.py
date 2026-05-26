"""Thread-safe publish-subscribe data bus for sensor data and events."""

from __future__ import annotations

import threading
from typing import Any, Callable

# Type aliases
Sample = Any  # Can be RawSample, CalibratedSample, or any event payload
SubscriptionId = int


class DataBus:
    """Thread-safe publish-subscribe bus for sensor data and events.

    Decouples producers (HAT readers, calibration engine) from consumers
    (Dashboard, CSV logger, alarm manager, WebSocket broadcaster).

    Supports:
    - Multiple subscribers per topic
    - A wildcard topic ("*") that receives all published messages
    - Safe unsubscription during publish (snapshot-based iteration)
    - Thread-safe access via threading.Lock
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._subscribers: dict[str, dict[SubscriptionId, Callable[[Sample], None]]] = {}
        self._next_id: SubscriptionId = 0

    def publish(self, topic: str, sample: Sample) -> None:
        """Publish a sample to all subscribers of the topic.

        Subscribers registered for the exact topic and for the wildcard
        topic ("*") will both receive the sample. Callbacks are invoked
        outside the lock to avoid deadlocks, using a snapshot of current
        subscribers.

        Args:
            topic: The topic string (e.g. channel_id or event name).
            sample: The data payload to deliver to subscribers.
        """
        with self._lock:
            # Snapshot topic subscribers
            topic_callbacks = list(self._subscribers.get(topic, {}).values())
            # Snapshot wildcard subscribers
            wildcard_callbacks = (
                list(self._subscribers.get("*", {}).values()) if topic != "*" else []
            )

        # Invoke callbacks outside the lock to prevent deadlocks
        for callback in topic_callbacks:
            callback(sample)
        for callback in wildcard_callbacks:
            callback(sample)

    def subscribe(
        self, topic: str, callback: Callable[[Sample], None]
    ) -> SubscriptionId:
        """Subscribe to a topic with a callback.

        The callback will be invoked for every sample published to the
        given topic. Use topic "*" to subscribe to all topics (wildcard).

        Args:
            topic: The topic to subscribe to, or "*" for all topics.
            callback: A callable that accepts a Sample.

        Returns:
            A unique SubscriptionId that can be used to unsubscribe.
        """
        with self._lock:
            sub_id = self._next_id
            self._next_id += 1
            if topic not in self._subscribers:
                self._subscribers[topic] = {}
            self._subscribers[topic][sub_id] = callback
        return sub_id

    def unsubscribe(self, subscription_id: SubscriptionId) -> None:
        """Remove a subscription.

        If the subscription_id does not exist, this is a no-op.

        Args:
            subscription_id: The ID returned by subscribe().
        """
        with self._lock:
            for topic_subs in self._subscribers.values():
                if subscription_id in topic_subs:
                    del topic_subs[subscription_id]
                    return
