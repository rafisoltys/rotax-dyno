"""Acquisition layer - HAT reader threads for MCC 134 and MCC 118, GPIO RPM, serial AFR."""

from rotax_dyno_daq.acquisition.hat_reader import HatReader, ThermocoupleReader
from rotax_dyno_daq.acquisition.analog_voltage_reader import AnalogVoltageReader
from rotax_dyno_daq.acquisition.rpm_counter import RpmCounter, GPIO_AVAILABLE
from rotax_dyno_daq.acquisition.serial_afr_reader import SerialAfrReader, SERIAL_AVAILABLE

__all__ = [
    "HatReader",
    "ThermocoupleReader",
    "AnalogVoltageReader",
    "RpmCounter",
    "GPIO_AVAILABLE",
    "SerialAfrReader",
    "SERIAL_AVAILABLE",
]
