"""Real-time display smoothing using a per-channel moving average.

Provides a configurable rolling-window average that smooths live sensor
values before they are displayed on the Engine Overlay and Strip Charts.
A window_size of 1 means no smoothing (pass-through).
"""

from __future__ import annotations

from collections import deque


class DisplaySmoother:
    """Applies real-time moving-average smoothing to displayed values.

    Maintains a rolling buffer per channel. When window_size is 1 the
    value passes through unchanged (no allocation overhead).
    """

    def __init__(self, window_size: int = 1) -> None:
        """Initialize the display smoother.

        Args:
            window_size: Number of samples in the moving average window.
                         Must be >= 1. A value of 1 disables smoothing.
        """
        self._window_size = max(1, window_size)
        self._buffers: dict[str, deque[float]] = {}

    def smooth(self, channel_id: str, value: float) -> float:
        """Apply moving average smoothing to a value.

        Args:
            channel_id: The channel identifier (each channel has its own buffer).
            value: The new sample value to smooth.

        Returns:
            The smoothed value (average of the last N samples).
        """
        if self._window_size <= 1:
            return value

        if channel_id not in self._buffers:
            self._buffers[channel_id] = deque(maxlen=self._window_size)

        buf = self._buffers[channel_id]
        buf.append(value)
        return sum(buf) / len(buf)

    @property
    def window_size(self) -> int:
        """Current smoothing window size."""
        return self._window_size

    @window_size.setter
    def window_size(self, value: int) -> None:
        """Set the smoothing window size.

        Clears all internal buffers when the window size changes.

        Args:
            value: New window size (clamped to minimum of 1).
        """
        new_size = max(1, value)
        if new_size != self._window_size:
            self._window_size = new_size
            self._buffers.clear()
