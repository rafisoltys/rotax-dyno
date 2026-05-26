"""Unit tests for the AlarmManager class."""

import pytest

from rotax_dyno_daq.alarms.manager import AlarmManager
from rotax_dyno_daq.core.data_bus import DataBus
from rotax_dyno_daq.core.enums import AlarmSeverity, AlarmState
from rotax_dyno_daq.core.models import (
    ActiveAlarm,
    AlarmConfig,
    AlarmThreshold,
    CalibratedSample,
)


@pytest.fixture
def alarm_manager() -> AlarmManager:
    """Create an AlarmManager without DataBus subscription."""
    return AlarmManager()


@pytest.fixture
def alarm_manager_with_bus() -> AlarmManager:
    """Create an AlarmManager with DataBus subscription."""
    bus = DataBus()
    return AlarmManager(data_bus=bus)


@pytest.fixture
def basic_config() -> AlarmConfig:
    """Create a basic alarm config with all thresholds set."""
    return AlarmConfig(
        channel_id="egt1",
        thresholds=AlarmThreshold(
            low_warning=200.0,
            low_critical=100.0,
            high_warning=800.0,
            high_critical=900.0,
            deadband=10.0,
        ),
        enabled=True,
    )


class TestConfigureThreshold:
    """Tests for configure_threshold method."""

    def test_configure_threshold_stores_config(
        self, alarm_manager: AlarmManager, basic_config: AlarmConfig
    ) -> None:
        alarm_manager.configure_threshold("egt1", basic_config)
        # Verify by evaluating - a normal value should return INACTIVE
        state = alarm_manager.evaluate("egt1", 500.0)
        assert state == AlarmState.INACTIVE

    def test_configure_threshold_overwrites_existing(
        self, alarm_manager: AlarmManager
    ) -> None:
        config1 = AlarmConfig(
            channel_id="egt1",
            thresholds=AlarmThreshold(high_warning=800.0, deadband=5.0),
            enabled=True,
        )
        config2 = AlarmConfig(
            channel_id="egt1",
            thresholds=AlarmThreshold(high_warning=900.0, deadband=5.0),
            enabled=True,
        )
        alarm_manager.configure_threshold("egt1", config1)
        alarm_manager.configure_threshold("egt1", config2)

        # 850 should not trigger with new threshold of 900
        state = alarm_manager.evaluate("egt1", 850.0)
        assert state == AlarmState.INACTIVE


class TestEvaluateThresholdCrossing:
    """Tests for threshold crossing detection."""

    def test_value_within_normal_range(
        self, alarm_manager: AlarmManager, basic_config: AlarmConfig
    ) -> None:
        alarm_manager.configure_threshold("egt1", basic_config)
        state = alarm_manager.evaluate("egt1", 500.0)
        assert state == AlarmState.INACTIVE

    def test_high_warning_crossing(
        self, alarm_manager: AlarmManager, basic_config: AlarmConfig
    ) -> None:
        alarm_manager.configure_threshold("egt1", basic_config)
        state = alarm_manager.evaluate("egt1", 850.0)
        assert state == AlarmState.ACTIVE

        alarms = alarm_manager.get_active_alarms()
        assert len(alarms) == 1
        assert alarms[0].severity == AlarmSeverity.WARNING
        assert alarms[0].threshold_crossed == 800.0

    def test_high_critical_crossing(
        self, alarm_manager: AlarmManager, basic_config: AlarmConfig
    ) -> None:
        alarm_manager.configure_threshold("egt1", basic_config)
        state = alarm_manager.evaluate("egt1", 950.0)
        assert state == AlarmState.ACTIVE

        alarms = alarm_manager.get_active_alarms()
        assert len(alarms) == 1
        assert alarms[0].severity == AlarmSeverity.CRITICAL
        assert alarms[0].threshold_crossed == 900.0

    def test_low_warning_crossing(
        self, alarm_manager: AlarmManager, basic_config: AlarmConfig
    ) -> None:
        alarm_manager.configure_threshold("egt1", basic_config)
        state = alarm_manager.evaluate("egt1", 150.0)
        assert state == AlarmState.ACTIVE

        alarms = alarm_manager.get_active_alarms()
        assert len(alarms) == 1
        assert alarms[0].severity == AlarmSeverity.WARNING
        assert alarms[0].threshold_crossed == 200.0

    def test_low_critical_crossing(
        self, alarm_manager: AlarmManager, basic_config: AlarmConfig
    ) -> None:
        alarm_manager.configure_threshold("egt1", basic_config)
        state = alarm_manager.evaluate("egt1", 50.0)
        assert state == AlarmState.ACTIVE

        alarms = alarm_manager.get_active_alarms()
        assert len(alarms) == 1
        assert alarms[0].severity == AlarmSeverity.CRITICAL
        assert alarms[0].threshold_crossed == 100.0

    def test_critical_takes_priority_over_warning_high(
        self, alarm_manager: AlarmManager, basic_config: AlarmConfig
    ) -> None:
        """Value above both high_warning and high_critical should be CRITICAL."""
        alarm_manager.configure_threshold("egt1", basic_config)
        state = alarm_manager.evaluate("egt1", 950.0)
        assert state == AlarmState.ACTIVE

        alarms = alarm_manager.get_active_alarms()
        assert alarms[0].severity == AlarmSeverity.CRITICAL

    def test_critical_takes_priority_over_warning_low(
        self, alarm_manager: AlarmManager, basic_config: AlarmConfig
    ) -> None:
        """Value below both low_warning and low_critical should be CRITICAL."""
        alarm_manager.configure_threshold("egt1", basic_config)
        state = alarm_manager.evaluate("egt1", 50.0)
        assert state == AlarmState.ACTIVE

        alarms = alarm_manager.get_active_alarms()
        assert alarms[0].severity == AlarmSeverity.CRITICAL

    def test_unconfigured_channel_returns_inactive(
        self, alarm_manager: AlarmManager
    ) -> None:
        state = alarm_manager.evaluate("unknown_channel", 999.0)
        assert state == AlarmState.INACTIVE

    def test_disabled_config_returns_inactive(
        self, alarm_manager: AlarmManager
    ) -> None:
        config = AlarmConfig(
            channel_id="egt1",
            thresholds=AlarmThreshold(high_warning=800.0, deadband=5.0),
            enabled=False,
        )
        alarm_manager.configure_threshold("egt1", config)
        state = alarm_manager.evaluate("egt1", 900.0)
        assert state == AlarmState.INACTIVE

    def test_alarm_gets_unique_id(
        self, alarm_manager: AlarmManager, basic_config: AlarmConfig
    ) -> None:
        alarm_manager.configure_threshold("egt1", basic_config)
        alarm_manager.evaluate("egt1", 850.0)

        alarms = alarm_manager.get_active_alarms()
        assert len(alarms) == 1
        assert alarms[0].alarm_id  # UUID string, non-empty

    def test_only_one_alarm_per_channel(
        self, alarm_manager: AlarmManager, basic_config: AlarmConfig
    ) -> None:
        """Repeated evaluations above threshold should not create multiple alarms."""
        alarm_manager.configure_threshold("egt1", basic_config)
        alarm_manager.evaluate("egt1", 850.0)
        alarm_manager.evaluate("egt1", 860.0)
        alarm_manager.evaluate("egt1", 870.0)

        alarms = alarm_manager.get_active_alarms()
        assert len(alarms) == 1


class TestDeadbandClearing:
    """Tests for deadband-based alarm clearing."""

    def test_high_alarm_clears_with_deadband(
        self, alarm_manager: AlarmManager, basic_config: AlarmConfig
    ) -> None:
        """High alarm (threshold=800, deadband=10) clears when value < 790."""
        alarm_manager.configure_threshold("egt1", basic_config)
        alarm_manager.evaluate("egt1", 850.0)  # Trigger high warning

        # Value back below threshold but not below deadband
        state = alarm_manager.evaluate("egt1", 795.0)
        assert state == AlarmState.ACTIVE  # Still active

        # Value below threshold - deadband (800 - 10 = 790)
        state = alarm_manager.evaluate("egt1", 789.0)
        assert state == AlarmState.INACTIVE

        alarms = alarm_manager.get_active_alarms()
        assert len(alarms) == 0

    def test_low_alarm_clears_with_deadband(
        self, alarm_manager: AlarmManager, basic_config: AlarmConfig
    ) -> None:
        """Low alarm (threshold=200, deadband=10) clears when value > 210."""
        alarm_manager.configure_threshold("egt1", basic_config)
        alarm_manager.evaluate("egt1", 150.0)  # Trigger low warning

        # Value back above threshold but not above deadband
        state = alarm_manager.evaluate("egt1", 205.0)
        assert state == AlarmState.ACTIVE  # Still active

        # Value above threshold + deadband (200 + 10 = 210)
        state = alarm_manager.evaluate("egt1", 211.0)
        assert state == AlarmState.INACTIVE

    def test_zero_deadband_clears_immediately(
        self, alarm_manager: AlarmManager
    ) -> None:
        """With deadband=0, alarm clears as soon as value is below threshold."""
        config = AlarmConfig(
            channel_id="egt1",
            thresholds=AlarmThreshold(high_warning=800.0, deadband=0.0),
            enabled=True,
        )
        alarm_manager.configure_threshold("egt1", config)
        alarm_manager.evaluate("egt1", 850.0)  # Trigger

        # Value just below threshold
        state = alarm_manager.evaluate("egt1", 799.0)
        assert state == AlarmState.INACTIVE

    def test_acknowledged_alarm_clears_via_deadband(
        self, alarm_manager: AlarmManager, basic_config: AlarmConfig
    ) -> None:
        """An ACKNOWLEDGED alarm still clears via deadband logic."""
        alarm_manager.configure_threshold("egt1", basic_config)
        alarm_manager.evaluate("egt1", 850.0)  # Trigger

        alarms = alarm_manager.get_active_alarms()
        alarm_manager.acknowledge(alarms[0].alarm_id)

        # Clear via deadband
        state = alarm_manager.evaluate("egt1", 789.0)
        assert state == AlarmState.INACTIVE
        assert len(alarm_manager.get_active_alarms()) == 0


class TestAcknowledge:
    """Tests for alarm acknowledgment."""

    def test_acknowledge_active_alarm(
        self, alarm_manager: AlarmManager, basic_config: AlarmConfig
    ) -> None:
        alarm_manager.configure_threshold("egt1", basic_config)
        alarm_manager.evaluate("egt1", 850.0)

        alarms = alarm_manager.get_active_alarms()
        alarm_id = alarms[0].alarm_id

        alarm_manager.acknowledge(alarm_id)

        alarms = alarm_manager.get_active_alarms()
        assert len(alarms) == 1
        assert alarms[0].state == AlarmState.ACKNOWLEDGED

    def test_acknowledge_nonexistent_alarm_raises(
        self, alarm_manager: AlarmManager
    ) -> None:
        with pytest.raises(KeyError):
            alarm_manager.acknowledge("nonexistent-id")

    def test_acknowledge_already_acknowledged_raises(
        self, alarm_manager: AlarmManager, basic_config: AlarmConfig
    ) -> None:
        alarm_manager.configure_threshold("egt1", basic_config)
        alarm_manager.evaluate("egt1", 850.0)

        alarms = alarm_manager.get_active_alarms()
        alarm_id = alarms[0].alarm_id

        alarm_manager.acknowledge(alarm_id)

        with pytest.raises(ValueError):
            alarm_manager.acknowledge(alarm_id)

    def test_acknowledged_alarm_remains_in_active_list(
        self, alarm_manager: AlarmManager, basic_config: AlarmConfig
    ) -> None:
        """Acknowledged alarms are still returned by get_active_alarms."""
        alarm_manager.configure_threshold("egt1", basic_config)
        alarm_manager.evaluate("egt1", 850.0)

        alarms = alarm_manager.get_active_alarms()
        alarm_manager.acknowledge(alarms[0].alarm_id)

        # Still in active alarms list
        alarms = alarm_manager.get_active_alarms()
        assert len(alarms) == 1
        assert alarms[0].state == AlarmState.ACKNOWLEDGED


class TestGetActiveAlarms:
    """Tests for get_active_alarms method."""

    def test_empty_when_no_alarms(self, alarm_manager: AlarmManager) -> None:
        assert alarm_manager.get_active_alarms() == []

    def test_returns_multiple_channel_alarms(
        self, alarm_manager: AlarmManager
    ) -> None:
        config1 = AlarmConfig(
            channel_id="egt1",
            thresholds=AlarmThreshold(high_warning=800.0, deadband=5.0),
            enabled=True,
        )
        config2 = AlarmConfig(
            channel_id="egt2",
            thresholds=AlarmThreshold(high_warning=800.0, deadband=5.0),
            enabled=True,
        )
        alarm_manager.configure_threshold("egt1", config1)
        alarm_manager.configure_threshold("egt2", config2)

        alarm_manager.evaluate("egt1", 850.0)
        alarm_manager.evaluate("egt2", 860.0)

        alarms = alarm_manager.get_active_alarms()
        assert len(alarms) == 2
        channel_ids = {a.channel_id for a in alarms}
        assert channel_ids == {"egt1", "egt2"}


class TestDataBusSubscription:
    """Tests for DataBus integration."""

    def test_auto_evaluates_on_bus_publish(self) -> None:
        """AlarmManager should auto-evaluate when samples arrive via DataBus."""
        bus = DataBus()
        manager = AlarmManager(data_bus=bus)

        config = AlarmConfig(
            channel_id="egt1",
            thresholds=AlarmThreshold(high_warning=800.0, deadband=5.0),
            enabled=True,
        )
        manager.configure_threshold("egt1", config)

        # Publish a sample that crosses the threshold
        sample = CalibratedSample(
            channel_id="egt1",
            timestamp_ms=1000.0,
            raw_value=4.5,
            calibrated_value=850.0,
            unit="°C",
        )
        bus.publish("egt1", sample)

        alarms = manager.get_active_alarms()
        assert len(alarms) == 1
        assert alarms[0].severity == AlarmSeverity.WARNING

    def test_ignores_samples_without_calibrated_value(self) -> None:
        """Samples without calibrated_value attribute should be ignored."""
        bus = DataBus()
        manager = AlarmManager(data_bus=bus)

        config = AlarmConfig(
            channel_id="egt1",
            thresholds=AlarmThreshold(high_warning=800.0, deadband=5.0),
            enabled=True,
        )
        manager.configure_threshold("egt1", config)

        # Publish something that doesn't have calibrated_value
        bus.publish("egt1", {"channel_id": "egt1", "value": 850.0})

        alarms = manager.get_active_alarms()
        assert len(alarms) == 0

    def test_unsubscribe_stops_auto_evaluation(self) -> None:
        """After unsubscribe, DataBus samples should not trigger evaluation."""
        bus = DataBus()
        manager = AlarmManager(data_bus=bus)

        config = AlarmConfig(
            channel_id="egt1",
            thresholds=AlarmThreshold(high_warning=800.0, deadband=5.0),
            enabled=True,
        )
        manager.configure_threshold("egt1", config)
        manager.unsubscribe()

        sample = CalibratedSample(
            channel_id="egt1",
            timestamp_ms=1000.0,
            raw_value=4.5,
            calibrated_value=850.0,
            unit="°C",
        )
        bus.publish("egt1", sample)

        alarms = manager.get_active_alarms()
        assert len(alarms) == 0


class TestSeverityEscalation:
    """Tests for alarm severity changes."""

    def test_escalation_from_warning_to_critical(
        self, alarm_manager: AlarmManager, basic_config: AlarmConfig
    ) -> None:
        """If value goes from warning zone to critical zone, severity updates."""
        alarm_manager.configure_threshold("egt1", basic_config)

        # First trigger at warning level
        alarm_manager.evaluate("egt1", 850.0)
        alarms = alarm_manager.get_active_alarms()
        assert alarms[0].severity == AlarmSeverity.WARNING

        # Escalate to critical
        alarm_manager.evaluate("egt1", 950.0)
        alarms = alarm_manager.get_active_alarms()
        assert len(alarms) == 1
        assert alarms[0].severity == AlarmSeverity.CRITICAL

    def test_deescalation_from_critical_to_warning(
        self, alarm_manager: AlarmManager, basic_config: AlarmConfig
    ) -> None:
        """If value drops from critical zone to warning zone, severity updates."""
        alarm_manager.configure_threshold("egt1", basic_config)

        # First trigger at critical level
        alarm_manager.evaluate("egt1", 950.0)
        alarms = alarm_manager.get_active_alarms()
        assert alarms[0].severity == AlarmSeverity.CRITICAL

        # De-escalate to warning
        alarm_manager.evaluate("egt1", 850.0)
        alarms = alarm_manager.get_active_alarms()
        assert len(alarms) == 1
        assert alarms[0].severity == AlarmSeverity.WARNING
