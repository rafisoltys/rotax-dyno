"""Alarm Manager - evaluates channel values against thresholds and manages alarm state.

Implements a state machine for alarm conditions:
    INACTIVE → ACTIVE (on threshold crossing)
    ACTIVE → ACKNOWLEDGED (on operator acknowledgment)
    ACTIVE → INACTIVE (on deadband clearing)
    ACKNOWLEDGED → INACTIVE (on deadband clearing)

The AlarmManager can optionally subscribe to the DataBus to auto-evaluate
incoming samples as they arrive.
"""

from __future__ import annotations

import threading
import uuid
from datetime import datetime, timezone
from typing import Optional

from rotax_dyno_daq.core.data_bus import DataBus, Sample, SubscriptionId
from rotax_dyno_daq.core.enums import AlarmSeverity, AlarmState
from rotax_dyno_daq.core.models import ActiveAlarm, AlarmConfig, AlarmThreshold


class AlarmManager:
    """Evaluates channel values against configured thresholds and manages alarm state.

    Supports:
    - Configuring high/low warning/critical thresholds per channel
    - Threshold crossing detection with correct severity assignment
    - Deadband-based clearing (alarm clears only when value returns within
      threshold by at least the deadband amount)
    - Acknowledgment (silences audible, keeps visual, still clears via deadband)
    - Optional DataBus subscription for automatic evaluation of incoming samples
    """

    def __init__(self, data_bus: Optional[DataBus] = None) -> None:
        """Initialize the AlarmManager.

        Args:
            data_bus: Optional DataBus instance. If provided, the manager will
                subscribe to all topics and auto-evaluate incoming samples.
        """
        self._lock = threading.Lock()
        self._configs: dict[str, AlarmConfig] = {}
        self._active_alarms: dict[str, ActiveAlarm] = {}  # alarm_id -> ActiveAlarm
        # Track which channel has which active alarm(s) for quick lookup
        self._channel_alarms: dict[str, str] = {}  # channel_id -> alarm_id

        self._data_bus = data_bus
        self._subscription_id: Optional[SubscriptionId] = None

        if data_bus is not None:
            self._subscribe_to_bus(data_bus)

    def _subscribe_to_bus(self, data_bus: DataBus) -> None:
        """Subscribe to the DataBus wildcard topic to auto-evaluate samples."""
        self._subscription_id = data_bus.subscribe("*", self._on_sample)

    def _on_sample(self, sample: Sample) -> None:
        """Callback for DataBus samples. Evaluates if the sample has channel_id and calibrated_value."""
        # Accept CalibratedSample objects (duck-typed)
        channel_id = getattr(sample, "channel_id", None)
        value = getattr(sample, "calibrated_value", None)
        if channel_id is not None and value is not None:
            self.evaluate(channel_id, value)

    def configure_threshold(self, channel_id: str, config: AlarmConfig) -> None:
        """Set alarm thresholds for a channel.

        Args:
            channel_id: The channel identifier.
            config: The alarm configuration including thresholds and enabled flag.
        """
        with self._lock:
            self._configs[channel_id] = config

    def evaluate(self, channel_id: str, value: float) -> AlarmState:
        """Evaluate a value against configured thresholds, applying deadband logic.

        Threshold crossing detection:
        - If value > high_critical → ACTIVE with CRITICAL severity
        - If value > high_warning (and not above critical) → ACTIVE with WARNING severity
        - If value < low_critical → ACTIVE with CRITICAL severity
        - If value < low_warning (and not below critical) → ACTIVE with WARNING severity

        Deadband clearing:
        - For high alarm: clears when value < (threshold - deadband)
        - For low alarm: clears when value > (threshold + deadband)

        Args:
            channel_id: The channel identifier.
            value: The current calibrated value to evaluate.

        Returns:
            The resulting AlarmState for this channel after evaluation.
        """
        with self._lock:
            config = self._configs.get(channel_id)
            if config is None or not config.enabled:
                return AlarmState.INACTIVE

            thresholds = config.thresholds
            existing_alarm_id = self._channel_alarms.get(channel_id)
            existing_alarm = (
                self._active_alarms.get(existing_alarm_id)
                if existing_alarm_id
                else None
            )

            # Determine if value currently crosses any threshold
            new_severity, threshold_crossed = self._check_threshold_crossing(
                value, thresholds
            )

            if existing_alarm is not None:
                # There's an existing alarm - check if it should clear or escalate
                if new_severity is not None:
                    # Value still in alarm zone - check if severity changed (escalation)
                    if new_severity != existing_alarm.severity:
                        # Severity changed - update the alarm
                        existing_alarm.severity = new_severity
                        existing_alarm.value = value
                        existing_alarm.threshold_crossed = threshold_crossed
                    else:
                        # Same severity - just update value
                        existing_alarm.value = value
                    return existing_alarm.state
                else:
                    # Value is within normal range - check deadband for clearing
                    if self._should_clear(
                        value, existing_alarm, thresholds
                    ):
                        # Clear the alarm
                        alarm_id = existing_alarm.alarm_id
                        del self._active_alarms[alarm_id]
                        del self._channel_alarms[channel_id]
                        return AlarmState.INACTIVE
                    else:
                        # Deadband not satisfied - alarm remains
                        existing_alarm.value = value
                        return existing_alarm.state
            else:
                # No existing alarm - check if we should trigger one
                if new_severity is not None:
                    alarm_id = str(uuid.uuid4())
                    alarm = ActiveAlarm(
                        alarm_id=alarm_id,
                        channel_id=channel_id,
                        severity=new_severity,
                        triggered_at=datetime.now(timezone.utc),
                        value=value,
                        threshold_crossed=threshold_crossed,
                        state=AlarmState.ACTIVE,
                    )
                    self._active_alarms[alarm_id] = alarm
                    self._channel_alarms[channel_id] = alarm_id
                    return AlarmState.ACTIVE
                else:
                    return AlarmState.INACTIVE

    def acknowledge(self, alarm_id: str) -> None:
        """Acknowledge an active alarm (silences audible, keeps visual).

        Transitions an ACTIVE alarm to ACKNOWLEDGED state. An ACKNOWLEDGED
        alarm still clears via deadband logic.

        Args:
            alarm_id: The unique identifier of the alarm to acknowledge.

        Raises:
            KeyError: If the alarm_id does not exist in active alarms.
            ValueError: If the alarm is not in ACTIVE state.
        """
        with self._lock:
            alarm = self._active_alarms.get(alarm_id)
            if alarm is None:
                raise KeyError(f"Alarm '{alarm_id}' not found in active alarms")
            if alarm.state != AlarmState.ACTIVE:
                raise ValueError(
                    f"Cannot acknowledge alarm in state '{alarm.state.value}'; "
                    f"only ACTIVE alarms can be acknowledged"
                )
            alarm.state = AlarmState.ACKNOWLEDGED

    def get_active_alarms(self) -> list[ActiveAlarm]:
        """Return all currently active alarm conditions.

        Returns both ACTIVE and ACKNOWLEDGED alarms (anything not INACTIVE).

        Returns:
            A list of all active alarm conditions.
        """
        with self._lock:
            return list(self._active_alarms.values())

    def unsubscribe(self) -> None:
        """Unsubscribe from the DataBus if previously subscribed."""
        if self._data_bus is not None and self._subscription_id is not None:
            self._data_bus.unsubscribe(self._subscription_id)
            self._subscription_id = None

    def _check_threshold_crossing(
        self, value: float, thresholds: AlarmThreshold
    ) -> tuple[Optional[AlarmSeverity], float]:
        """Check if a value crosses any configured threshold.

        Priority: critical thresholds are checked before warning thresholds.

        Returns:
            A tuple of (severity, threshold_value) if a threshold is crossed,
            or (None, 0.0) if the value is within normal range.
        """
        # Check high critical first (highest priority)
        if thresholds.high_critical is not None and value > thresholds.high_critical:
            return AlarmSeverity.CRITICAL, thresholds.high_critical

        # Check low critical
        if thresholds.low_critical is not None and value < thresholds.low_critical:
            return AlarmSeverity.CRITICAL, thresholds.low_critical

        # Check high warning
        if thresholds.high_warning is not None and value > thresholds.high_warning:
            return AlarmSeverity.WARNING, thresholds.high_warning

        # Check low warning
        if thresholds.low_warning is not None and value < thresholds.low_warning:
            return AlarmSeverity.WARNING, thresholds.low_warning

        return None, 0.0

    def _should_clear(
        self,
        value: float,
        alarm: ActiveAlarm,
        thresholds: AlarmThreshold,
    ) -> bool:
        """Determine if an alarm should clear based on deadband logic.

        For high alarm: clears when value < (threshold_crossed - deadband)
        For low alarm: clears when value > (threshold_crossed + deadband)

        Args:
            value: The current channel value.
            alarm: The active alarm to check for clearing.
            thresholds: The threshold configuration (contains deadband).

        Returns:
            True if the alarm should be cleared, False otherwise.
        """
        deadband = thresholds.deadband
        threshold_crossed = alarm.threshold_crossed

        # Determine if this was a high or low alarm based on the threshold crossed
        is_high_alarm = self._is_high_alarm(threshold_crossed, thresholds)

        if is_high_alarm:
            # High alarm clears when value < (threshold - deadband)
            return value < (threshold_crossed - deadband)
        else:
            # Low alarm clears when value > (threshold + deadband)
            return value > (threshold_crossed + deadband)

    def _is_high_alarm(
        self, threshold_crossed: float, thresholds: AlarmThreshold
    ) -> bool:
        """Determine if the alarm was triggered by a high or low threshold.

        Args:
            threshold_crossed: The threshold value that was crossed.
            thresholds: The full threshold configuration.

        Returns:
            True if it's a high alarm, False if it's a low alarm.
        """
        high_thresholds = []
        if thresholds.high_warning is not None:
            high_thresholds.append(thresholds.high_warning)
        if thresholds.high_critical is not None:
            high_thresholds.append(thresholds.high_critical)

        return threshold_crossed in high_thresholds
