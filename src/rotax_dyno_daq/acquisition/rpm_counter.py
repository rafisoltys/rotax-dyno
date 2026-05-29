"""RPM counter using GPIO edge detection on Raspberry Pi.

Measures engine RPM by counting time between pulses on a GPIO pin.
One pulse per crankshaft revolution. Uses RPi.GPIO for
hardware interrupt-based edge detection.

Recommended wiring:
- Signal → GPIO 4 (physical pin 7)
- GND → Pin 9
- If signal is 5V/12V, use voltage divider or optocoupler

Algorithm:
- On each rising edge, record timestamp
- RPM = 60 / (time_between_edges_in_seconds)
- Apply moving average to smooth out jitter
- Publish CalibratedSample to DataBus at configured rate
"""

import time
import threading
from typing import Optional
from collections import deque

from rotax_dyno_daq.core.data_bus import DataBus
from rotax_dyno_daq.core.enums import SampleValidity
from rotax_dyno_daq.core.models import CalibratedSample

# Try to import GPIO library
GPIO_AVAILABLE = False
try:
    import RPi.GPIO as GPIO
    GPIO_AVAILABLE = True
except ImportError:
    pass

DEFAULT_GPIO_PIN = 4  # GPIO 4 = physical pin 7
MAX_RPM = 9000
MIN_RPM = 100  # Below this, consider engine stopped
TIMEOUT_SECONDS = 2.0  # If no pulse for 2s, RPM = 0


class RpmCounter:
    """Measures RPM from pulse signal on GPIO pin.

    Uses edge detection interrupt to measure time between pulses.
    Publishes CalibratedSample with RPM value to DataBus (bypasses
    calibration bridge since the value is already in RPM).

    Args:
        data_bus: DataBus to publish RPM samples to.
        gpio_pin: GPIO pin number (BCM numbering). Default: 4
        channel_id: Channel ID for published samples. Default: "RPM"
        publish_rate_hz: How often to publish RPM value. Default: 10 Hz
        smoothing_samples: Number of periods to average. Default: 4
    """

    def __init__(
        self,
        data_bus: DataBus,
        gpio_pin: int = DEFAULT_GPIO_PIN,
        channel_id: str = "RPM",
        publish_rate_hz: float = 10.0,
        smoothing_samples: int = 4,
    ) -> None:
        self._data_bus = data_bus
        self._gpio_pin = gpio_pin
        self._channel_id = channel_id
        self._publish_rate_hz = publish_rate_hz
        self._smoothing_samples = smoothing_samples

        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._last_edge_time: float = 0.0
        self._periods: deque[float] = deque(maxlen=smoothing_samples)
        self._current_rpm: float = 0.0
        self._lock = threading.Lock()

    def start(self) -> None:
        """Start RPM measurement."""
        if self._running:
            return
        self._running = True

        if GPIO_AVAILABLE:
            GPIO.setmode(GPIO.BCM)
            GPIO.setup(self._gpio_pin, GPIO.IN, pull_up_down=GPIO.PUD_DOWN)
            GPIO.add_event_detect(
                self._gpio_pin, GPIO.RISING,
                callback=self._on_edge,
                bouncetime=2,  # 2ms debounce (max ~30000 RPM)
            )

        # Publisher thread
        self._thread = threading.Thread(
            target=self._publish_loop,
            name="rpm-counter",
            daemon=True,
        )
        self._thread.start()

    def stop(self) -> None:
        """Stop RPM measurement and clean up GPIO."""
        self._running = False
        if self._thread:
            self._thread.join(timeout=3.0)
            self._thread = None
        if GPIO_AVAILABLE:
            try:
                GPIO.remove_event_detect(self._gpio_pin)
                GPIO.cleanup(self._gpio_pin)
            except Exception:
                pass

    def _on_edge(self, channel: int) -> None:
        """GPIO interrupt callback — called on each rising edge."""
        now = time.perf_counter()
        with self._lock:
            if self._last_edge_time > 0:
                period = now - self._last_edge_time
                if period > 0.001:  # Ignore periods < 1ms (noise/bounce)
                    self._periods.append(period)
            self._last_edge_time = now

    def _publish_loop(self) -> None:
        """Periodically compute RPM from collected periods and publish."""
        interval = 1.0 / self._publish_rate_hz
        while self._running:
            time.sleep(interval)

            with self._lock:
                now = time.perf_counter()
                # Check for timeout (engine stopped)
                if self._last_edge_time > 0 and (now - self._last_edge_time) > TIMEOUT_SECONDS:
                    self._current_rpm = 0.0
                    self._periods.clear()
                elif self._periods:
                    avg_period = sum(self._periods) / len(self._periods)
                    rpm = 60.0 / avg_period
                    # Clamp to valid range
                    if rpm < MIN_RPM:
                        rpm = 0.0
                    elif rpm > MAX_RPM:
                        rpm = MAX_RPM
                    self._current_rpm = rpm
                # else: keep last known RPM (or 0 if never received)

            # Publish CalibratedSample directly (no calibration bridge needed)
            sample = CalibratedSample(
                channel_id=self._channel_id,
                timestamp_ms=time.time() * 1000.0,
                raw_value=self._current_rpm,
                calibrated_value=self._current_rpm,
                unit="RPM",
                validity=SampleValidity.VALID,
            )
            self._data_bus.publish(self._channel_id, sample)

    @property
    def is_running(self) -> bool:
        return self._running

    @property
    def current_rpm(self) -> float:
        return self._current_rpm

    @property
    def gpio_pin(self) -> int:
        return self._gpio_pin
