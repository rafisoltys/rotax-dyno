"""Serial port reader for AFR (Air-Fuel Ratio) data.

Reads AFR values from a serial port (e.g., wideband lambda controller).
Parses incoming data lines and publishes CalibratedSample to DataBus.

Supports configurable:
- Serial port path (e.g., /dev/ttyUSB0, /dev/ttyAMA0)
- Baud rate
- Parse function (user-configurable for different controller protocols)
"""

import logging
import threading
import time
from typing import Callable, Optional

from rotax_dyno_daq.core.data_bus import DataBus
from rotax_dyno_daq.core.enums import SampleValidity
from rotax_dyno_daq.core.models import CalibratedSample

logger = logging.getLogger(__name__)

# Try to import serial
SERIAL_AVAILABLE = False
try:
    import serial
    SERIAL_AVAILABLE = True
except ImportError:
    pass

DEFAULT_PORT = "/dev/ttyUSB0"
DEFAULT_BAUDRATE = 9600
DEFAULT_TIMEOUT = 1.0


# Default parser: expects lines like "AFR1:14.7,AFR2:14.2,AFR3:14.5,AFR4:14.1"
# or simple numeric lines. User can replace with custom parser.
def default_afr_parser(line: str) -> dict[str, float]:
    """Parse a serial line into channel_id -> value dict.

    Supports formats:
    - "AFR1:14.7,AFR2:14.2,AFR3:14.5,AFR4:14.1"
    - "14.7,14.2,14.5,14.1" (assumes AFR1-4 in order)
    - "14.7" (single value = AFR1)

    Returns:
        Dict mapping channel_id to float value.
    """
    results: dict[str, float] = {}
    line = line.strip()
    if not line:
        return results

    # Try key:value format first
    if ":" in line:
        parts = line.split(",")
        for part in parts:
            if ":" in part:
                key, val_str = part.split(":", 1)
                try:
                    results[key.strip()] = float(val_str.strip())
                except ValueError:
                    pass
        return results

    # Try comma-separated values (AFR1, AFR2, AFR3, AFR4)
    parts = line.split(",")
    for i, val_str in enumerate(parts[:4], start=1):
        try:
            results[f"AFR{i}"] = float(val_str.strip())
        except ValueError:
            pass

    return results


class SerialAfrReader:
    """Reads AFR data from a serial port and publishes to DataBus.

    Publishes CalibratedSample directly (bypasses calibration bridge
    since the value is already in AFR units from the wideband controller).

    Args:
        data_bus: DataBus to publish AFR samples to.
        port: Serial port path. Default: /dev/ttyUSB0
        baudrate: Serial baud rate. Default: 9600
        parser: Function to parse serial lines into channel values.
            Default: handles "AFR1:14.7,AFR2:14.2" format.
    """

    def __init__(
        self,
        data_bus: DataBus,
        port: str = DEFAULT_PORT,
        baudrate: int = DEFAULT_BAUDRATE,
        parser: Optional[Callable[[str], dict[str, float]]] = None,
    ) -> None:
        self._data_bus = data_bus
        self._port = port
        self._baudrate = baudrate
        self._parser = parser or default_afr_parser

        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._serial: Optional["serial.Serial"] = None

    def start(self) -> None:
        """Start reading from serial port."""
        if self._running:
            return
        if not SERIAL_AVAILABLE:
            logger.warning("pyserial not installed. Install with: pip install pyserial")
            return

        self._running = True
        self._thread = threading.Thread(
            target=self._read_loop,
            name="serial-afr-reader",
            daemon=True,
        )
        self._thread.start()

    def stop(self) -> None:
        """Stop reading and close serial port."""
        self._running = False
        if self._thread:
            self._thread.join(timeout=3.0)
            self._thread = None
        if self._serial and self._serial.is_open:
            self._serial.close()
            self._serial = None

    def _read_loop(self) -> None:
        """Main read loop — opens port, reads lines, parses, publishes."""
        while self._running:
            try:
                if self._serial is None or not self._serial.is_open:
                    self._serial = serial.Serial(
                        port=self._port,
                        baudrate=self._baudrate,
                        timeout=DEFAULT_TIMEOUT,
                    )
                    logger.info("Serial port opened: %s @ %d baud", self._port, self._baudrate)

                line = self._serial.readline().decode("utf-8", errors="ignore").strip()
                if not line:
                    continue

                # Parse the line
                values = self._parser(line)
                timestamp_ms = time.time() * 1000.0

                # Publish each parsed channel as CalibratedSample (no calibration needed)
                for channel_id, value in values.items():
                    sample = CalibratedSample(
                        channel_id=channel_id,
                        timestamp_ms=timestamp_ms,
                        raw_value=value,
                        calibrated_value=value,
                        unit="AFR",
                        validity=SampleValidity.VALID,
                    )
                    self._data_bus.publish(channel_id, sample)

            except serial.SerialException as e:
                logger.warning("Serial error on %s: %s. Retrying in 2s...", self._port, e)
                if self._serial:
                    try:
                        self._serial.close()
                    except Exception:
                        pass
                    self._serial = None
                time.sleep(2.0)
            except Exception as e:
                logger.error("Unexpected error in serial reader: %s", e)
                time.sleep(1.0)

    @property
    def is_running(self) -> bool:
        return self._running

    @property
    def port(self) -> str:
        return self._port

    @property
    def baudrate(self) -> int:
        return self._baudrate

    def set_parser(self, parser: Callable[[str], dict[str, float]]) -> None:
        """Update the line parser function at runtime."""
        self._parser = parser
