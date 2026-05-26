"""Post-processing filters and derived channel calculations.

Provides low-pass filtering, moving average smoothing, EGT spread calculation,
and rate-of-change derivation for recorded sensor data.
"""

import csv
from pathlib import Path

import numpy as np
from scipy.signal import butter, filtfilt

from rotax_dyno_daq.core.models import PostProcessConfig


class PostProcessor:
    """Applies signal processing to recorded data."""

    def low_pass_filter(
        self, data: np.ndarray, cutoff_hz: float, sample_rate_hz: float
    ) -> np.ndarray:
        """Apply a Butterworth low-pass filter.

        Uses a 4th-order Butterworth filter applied with zero-phase filtering
        (filtfilt) to avoid phase distortion.

        Args:
            data: 1-D array of sample values. NaN values are treated as invalid.
            cutoff_hz: Filter cutoff frequency in Hz. Must be between 0.1 and
                sample_rate_hz / 2 (Nyquist frequency).
            sample_rate_hz: Sampling rate of the data in Hz.

        Returns:
            Filtered data array of the same length as input. Positions
            corresponding to NaN inputs are set to NaN in the output.

        Raises:
            ValueError: If cutoff_hz is outside the valid range [0.1, Nyquist].
        """
        nyquist = sample_rate_hz / 2.0

        if cutoff_hz < 0.1 or cutoff_hz >= nyquist:
            raise ValueError(
                f"Cutoff frequency must be between 0.1 Hz and less than "
                f"{nyquist} Hz (Nyquist frequency for sample rate "
                f"{sample_rate_hz} Hz). Got {cutoff_hz} Hz."
            )

        # Handle NaN values: identify valid samples
        valid_mask = ~np.isnan(data)
        valid_count = np.sum(valid_mask)

        # Need at least enough valid samples for the filter to work
        # filtfilt requires padlen = 3 * max(len(b), len(a)) - 1 samples minimum
        # For a 4th-order filter, that's at least 13 samples
        min_samples = 13
        if valid_count < min_samples:
            # Not enough valid data to filter; return NaN array
            return np.full_like(data, np.nan, dtype=float)

        # Design the Butterworth filter
        normalized_cutoff = cutoff_hz / nyquist
        b, a = butter(4, normalized_cutoff, btype="low")

        # Create output array initialized to NaN
        output = np.full_like(data, np.nan, dtype=float)

        # Extract valid samples, apply filter, then place back
        valid_data = data[valid_mask].astype(float)
        filtered_valid = filtfilt(b, a, valid_data)

        # Place filtered values back at valid positions
        output[valid_mask] = filtered_valid

        return output

    def moving_average(self, data: np.ndarray, window_size: int) -> np.ndarray:
        """Apply a centered moving average smoothing filter.

        Computes the arithmetic mean of samples within a centered window.
        NaN values are excluded from the computation. If there are no valid
        samples in a window, the output at that position is NaN.

        Args:
            data: 1-D array of sample values. NaN values are treated as invalid.
            window_size: Number of samples in the averaging window.
                Must be between 3 and 101 (inclusive).

        Returns:
            Smoothed data array of the same length as input. Positions where
            the window contains no valid samples are set to NaN.

        Raises:
            ValueError: If window_size is outside the valid range [3, 101].
        """
        if window_size < 3 or window_size > 101:
            raise ValueError(
                f"Window size must be between 3 and 101. Got {window_size}."
            )

        n = len(data)
        output = np.full(n, np.nan, dtype=float)
        half_window = window_size // 2

        for i in range(n):
            start = max(0, i - half_window)
            end = min(n, i + half_window + 1)
            window = data[start:end]

            # Exclude NaN values
            valid_values = window[~np.isnan(window)]

            if len(valid_values) > 0:
                output[i] = np.mean(valid_values)
            # else: output[i] remains NaN

        return output

    def calculate_spread(self, channels: dict[str, np.ndarray]) -> np.ndarray:
        """Calculate EGT spread (max - min across channels per timestamp).

        For each timestamp index, computes the difference between the maximum
        and minimum channel values. If any channel has NaN at an index, the
        output at that index is NaN.

        Args:
            channels: Dictionary mapping channel names to numpy arrays.
                All arrays must be the same length.

        Returns:
            Array where each element is max(channel_values) - min(channel_values)
            at that index. Returns empty array if channels dict is empty.

        Raises:
            ValueError: If channel arrays have different lengths.
        """
        if not channels:
            return np.array([], dtype=float)

        arrays = list(channels.values())
        length = len(arrays[0])

        # Validate all arrays have the same length
        for name, arr in channels.items():
            if len(arr) != length:
                raise ValueError(
                    f"All channel arrays must have the same length. "
                    f"Expected {length}, got {len(arr)} for channel '{name}'."
                )

        # Stack arrays: shape (num_channels, num_samples)
        stacked = np.vstack([arr.astype(float) for arr in arrays])

        # If any channel has NaN at an index, output is NaN
        any_nan = np.any(np.isnan(stacked), axis=0)

        # Compute max - min along channel axis
        max_vals = np.nanmax(stacked, axis=0)
        min_vals = np.nanmin(stacked, axis=0)
        spread = max_vals - min_vals

        # Set NaN where any channel had NaN
        spread[any_nan] = np.nan

        return spread

    def calculate_rate_of_change(
        self, data: np.ndarray, sample_interval_s: float
    ) -> np.ndarray:
        """Calculate rate of change (derivative) of a channel.

        Computes (v[i+1] - v[i]) / sample_interval_s for each consecutive
        pair of samples.

        Args:
            data: 1-D array of sample values.
            sample_interval_s: Time interval between consecutive samples
                in seconds.

        Returns:
            Array of length len(data) - 1 containing the rate of change.
            If either v[i] or v[i+1] is NaN, the output at that position
            is NaN.
        """
        if len(data) < 2:
            return np.array([], dtype=float)

        data_float = data.astype(float)
        diff = np.diff(data_float)
        rate = diff / sample_interval_s

        return rate

    def process_and_save(self, source_path: Path, config: PostProcessConfig) -> Path:
        """Apply processing pipeline and save to a new CSV file.

        Reads the source CSV file, applies configured processing steps
        (low-pass filter, moving average, EGT spread, rate of change)
        to specified channels, and saves the processed data as a new CSV
        in the same directory as the source. The original file is never modified.

        Args:
            source_path: Path to the source CSV file to process.
            config: Processing configuration specifying which filters
                and derived channels to apply.

        Returns:
            Path to the newly created processed CSV file.

        Raises:
            FileNotFoundError: If source_path does not exist.
            ValueError: If processing parameters are invalid.
        """
        if not source_path.exists():
            raise FileNotFoundError(f"Source file not found: {source_path}")

        # Parse the source CSV
        header_lines, column_headers, data_rows = self._parse_csv(source_path)

        # Organize data by channel
        channel_data = self._organize_by_channel(data_rows)

        # Determine sample rate from timestamps (needed for filtering)
        sample_rates = self._estimate_sample_rates(channel_data)

        # Apply processing to configured channels
        processed_data = {}
        for channel_id in config.channels_to_process:
            if channel_id not in channel_data:
                continue

            timestamps, values, units, validities = channel_data[channel_id]
            processed_values = values.copy()

            # Apply low-pass filter if configured
            if config.low_pass_cutoff_hz is not None:
                sample_rate = sample_rates.get(channel_id, 100.0)
                processed_values = self.low_pass_filter(
                    processed_values, config.low_pass_cutoff_hz, sample_rate
                )

            # Apply moving average if configured
            if config.moving_average_window is not None:
                processed_values = self.moving_average(
                    processed_values, config.moving_average_window
                )

            processed_data[channel_id] = (timestamps, processed_values, units, validities)

        # Calculate derived channels
        derived_channels: dict[str, tuple[np.ndarray, np.ndarray, str]] = {}

        # EGT spread
        if config.calculate_egt_spread:
            egt_channels = {
                ch_id: channel_data[ch_id][1]
                for ch_id in channel_data
                if ch_id.upper().startswith("EGT")
            }
            if egt_channels:
                spread = self.calculate_spread(egt_channels)
                # Use timestamps from the first EGT channel
                first_egt_id = next(iter(egt_channels))
                egt_timestamps = channel_data[first_egt_id][0]
                derived_channels["EGT_spread"] = (egt_timestamps, spread, "°C")

        # Rate of change for specified channels
        for channel_id in config.calculate_rate_of_change:
            if channel_id not in channel_data:
                continue
            timestamps, values, units, validities = channel_data[channel_id]
            sample_rate = sample_rates.get(channel_id, 100.0)
            sample_interval = 1.0 / sample_rate
            rate = self.calculate_rate_of_change(values, sample_interval)
            if len(rate) > 0:
                # Rate of change has one fewer sample; use timestamps[1:]
                rate_timestamps = timestamps[1:]
                unit_label = units[0] if len(units) > 0 else ""
                derived_channels[f"{channel_id}_rate"] = (
                    rate_timestamps,
                    rate,
                    f"{unit_label}/s",
                )

        # Build output file path
        output_path = source_path.parent / f"{source_path.stem}_processed.csv"

        # Write processed CSV
        self._write_processed_csv(
            output_path,
            header_lines,
            column_headers,
            data_rows,
            processed_data,
            derived_channels,
        )

        return output_path

    def _parse_csv(
        self, path: Path
    ) -> tuple[list[str], list[str], list[list[str]]]:
        """Parse a CSV file into header lines, column headers, and data rows.

        Args:
            path: Path to the CSV file.

        Returns:
            Tuple of (header_comment_lines, column_header_row, data_rows).
        """
        header_lines: list[str] = []
        column_headers: list[str] = []
        data_rows: list[list[str]] = []

        with open(path, "r", newline="", encoding="utf-8") as f:
            reader = csv.reader(f)
            found_columns = False

            for row in reader:
                if not row:
                    continue

                # Comment/metadata rows start with '#'
                if row[0].startswith("#"):
                    header_lines.append(",".join(row))
                    continue

                # First non-comment row is the column header
                if not found_columns:
                    column_headers = row
                    found_columns = True
                    continue

                # Remaining rows are data
                data_rows.append(row)

        return header_lines, column_headers, data_rows

    def _organize_by_channel(
        self, data_rows: list[list[str]]
    ) -> dict[str, tuple[np.ndarray, np.ndarray, list[str], list[str]]]:
        """Organize data rows by channel ID.

        Args:
            data_rows: List of CSV data rows [timestamp_ms, channel_id, value, unit, validity].

        Returns:
            Dictionary mapping channel_id to (timestamps, values, units, validities).
            Values marked as invalid are represented as NaN.
        """
        channel_timestamps: dict[str, list[float]] = {}
        channel_values: dict[str, list[float]] = {}
        channel_units: dict[str, list[str]] = {}
        channel_validities: dict[str, list[str]] = {}

        for row in data_rows:
            if len(row) < 5:
                continue

            timestamp_ms = float(row[0])
            channel_id = row[1]
            value_str = row[2]
            unit = row[3]
            validity = row[4]

            if channel_id not in channel_timestamps:
                channel_timestamps[channel_id] = []
                channel_values[channel_id] = []
                channel_units[channel_id] = []
                channel_validities[channel_id] = []

            channel_timestamps[channel_id].append(timestamp_ms)

            # Mark invalid samples as NaN for processing
            if validity != "valid":
                channel_values[channel_id].append(np.nan)
            else:
                channel_values[channel_id].append(float(value_str))

            channel_units[channel_id].append(unit)
            channel_validities[channel_id].append(validity)

        result: dict[str, tuple[np.ndarray, np.ndarray, list[str], list[str]]] = {}
        for channel_id in channel_timestamps:
            result[channel_id] = (
                np.array(channel_timestamps[channel_id]),
                np.array(channel_values[channel_id]),
                channel_units[channel_id],
                channel_validities[channel_id],
            )

        return result

    def _estimate_sample_rates(
        self, channel_data: dict[str, tuple[np.ndarray, np.ndarray, list[str], list[str]]]
    ) -> dict[str, float]:
        """Estimate sample rates from timestamp data.

        Args:
            channel_data: Organized channel data with timestamps.

        Returns:
            Dictionary mapping channel_id to estimated sample rate in Hz.
        """
        rates: dict[str, float] = {}
        for channel_id, (timestamps, _, _, _) in channel_data.items():
            if len(timestamps) >= 2:
                # Calculate mean interval in milliseconds, convert to Hz
                intervals = np.diff(timestamps)
                mean_interval_ms = np.mean(intervals)
                if mean_interval_ms > 0:
                    rates[channel_id] = 1000.0 / mean_interval_ms
                else:
                    rates[channel_id] = 100.0  # fallback
            else:
                rates[channel_id] = 100.0  # fallback for single-sample channels

        return rates

    def _write_processed_csv(
        self,
        output_path: Path,
        header_lines: list[str],
        column_headers: list[str],
        original_data_rows: list[list[str]],
        processed_data: dict[str, tuple[np.ndarray, np.ndarray, list[str], list[str]]],
        derived_channels: dict[str, tuple[np.ndarray, np.ndarray, str]],
    ) -> None:
        """Write the processed data to a new CSV file.

        Preserves the original header metadata, replaces processed channel
        values, and appends derived channel data.

        Args:
            output_path: Path for the output CSV file.
            header_lines: Original header comment lines.
            column_headers: Column header row.
            original_data_rows: Original data rows.
            processed_data: Processed channel data (channel_id -> (timestamps, values, units, validities)).
            derived_channels: Derived channel data (channel_id -> (timestamps, values, unit)).
        """
        # Build index maps for processed channels to efficiently replace values
        # Track which row index corresponds to which sample index per channel
        channel_row_indices: dict[str, list[int]] = {}
        for row_idx, row in enumerate(original_data_rows):
            if len(row) < 5:
                continue
            channel_id = row[1]
            if channel_id not in channel_row_indices:
                channel_row_indices[channel_id] = []
            channel_row_indices[channel_id].append(row_idx)

        with open(output_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)

            # Write header comment lines
            for line in header_lines:
                # Parse the line back into fields for proper CSV writing
                parsed = next(csv.reader([line]))
                writer.writerow(parsed)

            # Write column headers
            writer.writerow(column_headers)

            # Write data rows, replacing processed channel values
            for row_idx, row in enumerate(original_data_rows):
                if len(row) < 5:
                    writer.writerow(row)
                    continue

                channel_id = row[1]

                if channel_id in processed_data:
                    # Find the sample index for this channel at this row
                    sample_idx = channel_row_indices[channel_id].index(row_idx)
                    _, processed_values, _, validities = processed_data[channel_id]

                    value = processed_values[sample_idx]
                    # Write the processed value
                    if np.isnan(value):
                        # Keep original validity if it was already invalid,
                        # otherwise mark as invalid
                        validity = validities[sample_idx] if validities[sample_idx] != "valid" else "invalid"
                        writer.writerow([row[0], row[1], row[2], row[3], validity])
                    else:
                        writer.writerow([row[0], row[1], f"{value:.6g}", row[3], row[4]])
                else:
                    # Channel not processed, write original row
                    writer.writerow(row)

            # Append derived channels
            for derived_id, (timestamps, values, unit) in derived_channels.items():
                for i in range(len(timestamps)):
                    value = values[i]
                    validity = "invalid" if np.isnan(value) else "valid"
                    value_str = f"{value:.6g}" if not np.isnan(value) else "NaN"
                    writer.writerow([
                        f"{timestamps[i]:.3f}",
                        derived_id,
                        value_str,
                        unit,
                        validity,
                    ])
