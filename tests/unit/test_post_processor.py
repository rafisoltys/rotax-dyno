"""Unit tests for PostProcessor filtering and derived channel calculations."""

import numpy as np
import pytest

from rotax_dyno_daq.processing.post_processor import PostProcessor


@pytest.fixture
def processor():
    return PostProcessor()


class TestLowPassFilter:
    """Tests for low_pass_filter method."""

    def test_rejects_cutoff_above_nyquist(self, processor):
        data = np.ones(100)
        with pytest.raises(ValueError, match="Cutoff frequency must be between"):
            processor.low_pass_filter(data, cutoff_hz=60.0, sample_rate_hz=100.0)

    def test_rejects_cutoff_below_minimum(self, processor):
        data = np.ones(100)
        with pytest.raises(ValueError, match="Cutoff frequency must be between"):
            processor.low_pass_filter(data, cutoff_hz=0.05, sample_rate_hz=100.0)

    def test_accepts_cutoff_just_below_nyquist(self, processor):
        """Cutoff just below Nyquist should be accepted."""
        data = np.ones(100)
        # cutoff_hz = 49.9, sample_rate_hz = 100.0 -> Nyquist = 50.0
        result = processor.low_pass_filter(data, cutoff_hz=49.9, sample_rate_hz=100.0)
        assert len(result) == len(data)

    def test_rejects_cutoff_at_exactly_nyquist(self, processor):
        """Cutoff at exactly Nyquist is rejected by SciPy (must be strictly less)."""
        data = np.ones(100)
        # SciPy requires normalized frequency strictly < 1
        with pytest.raises(ValueError):
            processor.low_pass_filter(data, cutoff_hz=50.0, sample_rate_hz=100.0)

    def test_constant_signal_unchanged(self, processor):
        """A constant signal should pass through the filter unchanged."""
        data = np.full(100, 5.0)
        result = processor.low_pass_filter(data, cutoff_hz=10.0, sample_rate_hz=100.0)
        np.testing.assert_allclose(result, 5.0, atol=1e-6)

    def test_attenuates_high_frequency(self, processor):
        """High-frequency component should be attenuated."""
        sample_rate = 1000.0
        t = np.arange(0, 1.0, 1.0 / sample_rate)
        # Low frequency (5 Hz) + high frequency (200 Hz)
        low_freq = np.sin(2 * np.pi * 5 * t)
        high_freq = np.sin(2 * np.pi * 200 * t)
        data = low_freq + high_freq

        result = processor.low_pass_filter(data, cutoff_hz=50.0, sample_rate_hz=sample_rate)

        # The high-frequency component should be significantly reduced
        # Check that the result is closer to the low-frequency component
        residual_with_filter = np.std(result - low_freq)
        residual_without_filter = np.std(data - low_freq)
        assert residual_with_filter < residual_without_filter * 0.1

    def test_nan_values_preserved_in_output(self, processor):
        """NaN positions in input should be NaN in output."""
        data = np.sin(np.linspace(0, 2 * np.pi, 100))
        data[10] = np.nan
        data[50] = np.nan

        result = processor.low_pass_filter(data, cutoff_hz=5.0, sample_rate_hz=100.0)

        assert np.isnan(result[10])
        assert np.isnan(result[50])

    def test_valid_positions_not_nan(self, processor):
        """Valid positions should produce non-NaN output."""
        data = np.sin(np.linspace(0, 2 * np.pi, 100))
        data[10] = np.nan

        result = processor.low_pass_filter(data, cutoff_hz=5.0, sample_rate_hz=100.0)

        # Non-NaN positions should have valid output
        valid_mask = ~np.isnan(data)
        assert not np.any(np.isnan(result[valid_mask]))

    def test_insufficient_valid_samples_returns_nan(self, processor):
        """If too few valid samples, return all NaN."""
        data = np.full(20, np.nan)
        data[0] = 1.0
        data[5] = 2.0

        result = processor.low_pass_filter(data, cutoff_hz=5.0, sample_rate_hz=100.0)
        assert np.all(np.isnan(result))

    def test_output_same_length_as_input(self, processor):
        data = np.random.randn(200)
        result = processor.low_pass_filter(data, cutoff_hz=10.0, sample_rate_hz=100.0)
        assert len(result) == len(data)


class TestMovingAverage:
    """Tests for moving_average method."""

    def test_rejects_window_below_minimum(self, processor):
        data = np.ones(10)
        with pytest.raises(ValueError, match="Window size must be between 3 and 101"):
            processor.moving_average(data, window_size=2)

    def test_rejects_window_above_maximum(self, processor):
        data = np.ones(10)
        with pytest.raises(ValueError, match="Window size must be between 3 and 101"):
            processor.moving_average(data, window_size=102)

    def test_accepts_window_at_boundaries(self, processor):
        data = np.ones(200)
        # Should not raise
        processor.moving_average(data, window_size=3)
        processor.moving_average(data, window_size=101)

    def test_constant_signal_unchanged(self, processor):
        """A constant signal should remain constant after moving average."""
        data = np.full(50, 7.0)
        result = processor.moving_average(data, window_size=5)
        np.testing.assert_allclose(result, 7.0, atol=1e-10)

    def test_centered_window_calculation(self, processor):
        """Verify centered window averaging for a known input."""
        data = np.array([1.0, 2.0, 3.0, 4.0, 5.0])
        result = processor.moving_average(data, window_size=3)

        # Index 0: window [0:2] -> mean(1, 2) = 1.5
        # Index 1: window [0:3] -> mean(1, 2, 3) = 2.0
        # Index 2: window [1:4] -> mean(2, 3, 4) = 3.0
        # Index 3: window [2:5] -> mean(3, 4, 5) = 4.0
        # Index 4: window [3:5] -> mean(4, 5) = 4.5
        expected = np.array([1.5, 2.0, 3.0, 4.0, 4.5])
        np.testing.assert_allclose(result, expected, atol=1e-10)

    def test_nan_excluded_from_average(self, processor):
        """NaN values should be excluded from the averaging window."""
        data = np.array([1.0, np.nan, 3.0, 4.0, 5.0])
        result = processor.moving_average(data, window_size=3)

        # Index 0: window [0:2] -> valid: [1.0] -> mean = 1.0
        # Index 1: window [0:3] -> valid: [1.0, 3.0] -> mean = 2.0
        # Index 2: window [1:4] -> valid: [3.0, 4.0] -> mean = 3.5
        # Index 3: window [2:5] -> valid: [3.0, 4.0, 5.0] -> mean = 4.0
        # Index 4: window [3:5] -> valid: [4.0, 5.0] -> mean = 4.5
        expected = np.array([1.0, 2.0, 3.5, 4.0, 4.5])
        np.testing.assert_allclose(result, expected, atol=1e-10)

    def test_all_nan_window_produces_nan(self, processor):
        """If all values in a window are NaN, output should be NaN."""
        data = np.array([np.nan, np.nan, np.nan, 4.0, 5.0])
        result = processor.moving_average(data, window_size=3)

        # Index 0: window [0:2] -> all NaN -> NaN
        assert np.isnan(result[0])

    def test_output_same_length_as_input(self, processor):
        data = np.random.randn(100)
        result = processor.moving_average(data, window_size=7)
        assert len(result) == len(data)


class TestCalculateSpread:
    """Tests for calculate_spread method."""

    def test_empty_channels_returns_empty(self, processor):
        result = processor.calculate_spread({})
        assert len(result) == 0

    def test_single_channel_spread_is_zero(self, processor):
        channels = {"egt1": np.array([100.0, 200.0, 300.0])}
        result = processor.calculate_spread(channels)
        np.testing.assert_allclose(result, [0.0, 0.0, 0.0])

    def test_two_channels_spread(self, processor):
        channels = {
            "egt1": np.array([100.0, 200.0, 300.0]),
            "egt2": np.array([150.0, 180.0, 350.0]),
        }
        result = processor.calculate_spread(channels)
        # max - min at each index:
        # [150-100, 200-180, 350-300] = [50, 20, 50]
        expected = np.array([50.0, 20.0, 50.0])
        np.testing.assert_allclose(result, expected)

    def test_four_channels_spread(self, processor):
        channels = {
            "egt1": np.array([600.0, 620.0]),
            "egt2": np.array([580.0, 640.0]),
            "egt3": np.array([610.0, 600.0]),
            "egt4": np.array([590.0, 630.0]),
        }
        result = processor.calculate_spread(channels)
        # Index 0: max=610, min=580 -> 30
        # Index 1: max=640, min=600 -> 40
        expected = np.array([30.0, 40.0])
        np.testing.assert_allclose(result, expected)

    def test_nan_in_any_channel_produces_nan(self, processor):
        channels = {
            "egt1": np.array([100.0, np.nan, 300.0]),
            "egt2": np.array([150.0, 200.0, 350.0]),
        }
        result = processor.calculate_spread(channels)
        assert not np.isnan(result[0])
        assert np.isnan(result[1])
        assert not np.isnan(result[2])

    def test_mismatched_lengths_raises(self, processor):
        channels = {
            "egt1": np.array([100.0, 200.0]),
            "egt2": np.array([150.0, 200.0, 350.0]),
        }
        with pytest.raises(ValueError, match="same length"):
            processor.calculate_spread(channels)


class TestCalculateRateOfChange:
    """Tests for calculate_rate_of_change method."""

    def test_constant_signal_zero_rate(self, processor):
        data = np.full(10, 5.0)
        result = processor.calculate_rate_of_change(data, sample_interval_s=0.1)
        np.testing.assert_allclose(result, 0.0, atol=1e-10)

    def test_linear_signal_constant_rate(self, processor):
        """A linearly increasing signal should have constant rate of change."""
        data = np.array([0.0, 1.0, 2.0, 3.0, 4.0])
        result = processor.calculate_rate_of_change(data, sample_interval_s=0.5)
        # (1-0)/0.5 = 2.0 for all
        expected = np.array([2.0, 2.0, 2.0, 2.0])
        np.testing.assert_allclose(result, expected)

    def test_output_length_is_n_minus_1(self, processor):
        data = np.random.randn(50)
        result = processor.calculate_rate_of_change(data, sample_interval_s=0.01)
        assert len(result) == len(data) - 1

    def test_nan_propagation(self, processor):
        """If either v[i] or v[i+1] is NaN, output should be NaN."""
        data = np.array([1.0, 2.0, np.nan, 4.0, 5.0])
        result = processor.calculate_rate_of_change(data, sample_interval_s=1.0)

        # Index 0: (2-1)/1 = 1.0
        # Index 1: (nan-2)/1 = nan
        # Index 2: (4-nan)/1 = nan
        # Index 3: (5-4)/1 = 1.0
        assert result[0] == 1.0
        assert np.isnan(result[1])
        assert np.isnan(result[2])
        assert result[3] == 1.0

    def test_single_sample_returns_empty(self, processor):
        data = np.array([5.0])
        result = processor.calculate_rate_of_change(data, sample_interval_s=0.1)
        assert len(result) == 0

    def test_empty_data_returns_empty(self, processor):
        data = np.array([])
        result = processor.calculate_rate_of_change(data, sample_interval_s=0.1)
        assert len(result) == 0

    def test_negative_rate_of_change(self, processor):
        data = np.array([10.0, 8.0, 5.0])
        result = processor.calculate_rate_of_change(data, sample_interval_s=1.0)
        expected = np.array([-2.0, -3.0])
        np.testing.assert_allclose(result, expected)


class TestProcessAndSave:
    """Tests for process_and_save pipeline method."""

    def _create_test_csv(self, tmp_path, filename="test_run.csv", channels=None, num_samples=20):
        """Helper to create a test CSV file matching the CsvLogger format.

        Args:
            tmp_path: pytest tmp_path fixture.
            filename: Name of the CSV file.
            channels: Dict of channel_id -> (unit, sample_rate_hz, values).
                If None, creates default EGT channels.
            num_samples: Number of samples per channel if using defaults.

        Returns:
            Path to the created CSV file.
        """
        from pathlib import Path
        import csv

        if channels is None:
            # Default: 4 EGT channels at 5 Hz
            channels = {
                "EGT1": ("°C", 5.0, [600.0 + i * 2 for i in range(num_samples)]),
                "EGT2": ("°C", 5.0, [580.0 + i * 1.5 for i in range(num_samples)]),
                "EGT3": ("°C", 5.0, [610.0 + i * 1.8 for i in range(num_samples)]),
                "EGT4": ("°C", 5.0, [590.0 + i * 2.2 for i in range(num_samples)]),
            }

        csv_path = tmp_path / filename
        with open(csv_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            # Write header metadata
            writer.writerow(["# Run Name", "Test Run"])
            writer.writerow(["# Start Time", "2024-01-15T10:30:00"])
            writer.writerow(["# Operator", "Test Operator"])
            writer.writerow(["# Notes", "Test notes"])
            # Column headers
            writer.writerow(["timestamp_ms", "channel_id", "calibrated_value", "unit", "validity"])
            # Write interleaved data rows (as CsvLogger does)
            for sample_idx in range(num_samples):
                for channel_id, (unit, rate, values) in channels.items():
                    timestamp_ms = sample_idx * (1000.0 / rate)
                    writer.writerow([
                        f"{timestamp_ms:.3f}",
                        channel_id,
                        f"{values[sample_idx]:.6g}",
                        unit,
                        "valid",
                    ])

        return csv_path

    def test_creates_processed_file_with_correct_name(self, processor, tmp_path):
        """Output file should be named {source_stem}_processed.csv."""
        from rotax_dyno_daq.core.models import PostProcessConfig

        csv_path = self._create_test_csv(tmp_path, filename="my_run.csv")
        config = PostProcessConfig(
            source_path=csv_path,
            channels_to_process=["EGT1"],
            moving_average_window=3,
        )

        result_path = processor.process_and_save(csv_path, config)

        assert result_path.name == "my_run_processed.csv"
        assert result_path.parent == csv_path.parent
        assert result_path.exists()

    def test_preserves_original_file_unmodified(self, processor, tmp_path):
        """Original source file must remain byte-for-byte identical."""
        from rotax_dyno_daq.core.models import PostProcessConfig

        csv_path = self._create_test_csv(tmp_path)

        # Read original content before processing
        original_content = csv_path.read_bytes()

        config = PostProcessConfig(
            source_path=csv_path,
            channels_to_process=["EGT1", "EGT2"],
            moving_average_window=5,
        )

        processor.process_and_save(csv_path, config)

        # Verify original file is unchanged
        assert csv_path.read_bytes() == original_content

    def test_applies_moving_average_to_specified_channels(self, processor, tmp_path):
        """Moving average should be applied only to channels_to_process."""
        from rotax_dyno_daq.core.models import PostProcessConfig
        import csv

        channels = {
            "EGT1": ("°C", 10.0, [100.0, 200.0, 300.0, 400.0, 500.0]),
            "EGT2": ("°C", 10.0, [50.0, 60.0, 70.0, 80.0, 90.0]),
        }
        csv_path = self._create_test_csv(tmp_path, channels=channels, num_samples=5)

        config = PostProcessConfig(
            source_path=csv_path,
            channels_to_process=["EGT1"],  # Only process EGT1
            moving_average_window=3,
        )

        result_path = processor.process_and_save(csv_path, config)

        # Parse the output file
        with open(result_path, "r", newline="", encoding="utf-8") as f:
            reader = csv.reader(f)
            rows = [row for row in reader if row and not row[0].startswith("#")]

        # Skip column header row
        data_rows = rows[1:]

        # EGT2 should be unchanged
        egt2_rows = [r for r in data_rows if r[1] == "EGT2"]
        for i, row in enumerate(egt2_rows):
            expected = channels["EGT2"][2][i]
            assert float(row[2]) == pytest.approx(expected, rel=1e-5)

        # EGT1 should be smoothed (different from original for middle values)
        egt1_rows = [r for r in data_rows if r[1] == "EGT1"]
        # Middle value (index 2): mean(200, 300, 400) = 300 (same as original here)
        # Index 1: mean(100, 200, 300) = 200 (same as original)
        # But edge values differ: index 0 = mean(100, 200) = 150 (not 100)
        assert float(egt1_rows[0][2]) == pytest.approx(150.0, rel=1e-5)

    def test_applies_low_pass_filter(self, processor, tmp_path):
        """Low-pass filter should be applied when cutoff is configured."""
        from rotax_dyno_daq.core.models import PostProcessConfig
        import csv

        # Create a signal with high-frequency noise
        num_samples = 100
        sample_rate = 100.0
        t = [i / sample_rate for i in range(num_samples)]
        # Low freq (2 Hz) + high freq (40 Hz)
        values = [5.0 * np.sin(2 * np.pi * 2 * ti) + np.sin(2 * np.pi * 40 * ti) for ti in t]

        channels = {"pressure": ("bar", sample_rate, values)}
        csv_path = self._create_test_csv(tmp_path, channels=channels, num_samples=num_samples)

        config = PostProcessConfig(
            source_path=csv_path,
            channels_to_process=["pressure"],
            low_pass_cutoff_hz=10.0,
        )

        result_path = processor.process_and_save(csv_path, config)

        # Parse output
        with open(result_path, "r", newline="", encoding="utf-8") as f:
            reader = csv.reader(f)
            rows = [row for row in reader if row and not row[0].startswith("#")]

        data_rows = rows[1:]
        pressure_rows = [r for r in data_rows if r[1] == "pressure"]
        processed_values = np.array([float(r[2]) for r in pressure_rows])

        # The high-frequency component should be attenuated
        # Compare with the pure low-frequency signal
        low_freq_signal = np.array([5.0 * np.sin(2 * np.pi * 2 * ti) for ti in t])
        residual = np.std(processed_values - low_freq_signal)
        assert residual < 0.5  # Should be much less than 1.0 (amplitude of high freq)

    def test_calculates_egt_spread(self, processor, tmp_path):
        """EGT spread should be computed as max - min across EGT channels."""
        from rotax_dyno_daq.core.models import PostProcessConfig
        import csv

        channels = {
            "EGT1": ("°C", 5.0, [600.0, 620.0, 640.0]),
            "EGT2": ("°C", 5.0, [580.0, 610.0, 650.0]),
            "EGT3": ("°C", 5.0, [610.0, 600.0, 630.0]),
        }
        csv_path = self._create_test_csv(tmp_path, channels=channels, num_samples=3)

        config = PostProcessConfig(
            source_path=csv_path,
            channels_to_process=[],
            calculate_egt_spread=True,
        )

        result_path = processor.process_and_save(csv_path, config)

        # Parse output
        with open(result_path, "r", newline="", encoding="utf-8") as f:
            reader = csv.reader(f)
            rows = [row for row in reader if row and not row[0].startswith("#")]

        data_rows = rows[1:]
        spread_rows = [r for r in data_rows if r[1] == "EGT_spread"]

        assert len(spread_rows) == 3
        # Index 0: max(600,580,610) - min(600,580,610) = 610 - 580 = 30
        assert float(spread_rows[0][2]) == pytest.approx(30.0)
        # Index 1: max(620,610,600) - min(620,610,600) = 620 - 600 = 20
        assert float(spread_rows[1][2]) == pytest.approx(20.0)
        # Index 2: max(640,650,630) - min(640,650,630) = 650 - 630 = 20
        assert float(spread_rows[2][2]) == pytest.approx(20.0)

    def test_calculates_rate_of_change(self, processor, tmp_path):
        """Rate of change should be computed for specified channels."""
        from rotax_dyno_daq.core.models import PostProcessConfig
        import csv

        # Linear signal: 0, 10, 20, 30, 40 at 10 Hz (interval = 0.1s)
        channels = {
            "RPM": ("rpm", 10.0, [0.0, 10.0, 20.0, 30.0, 40.0]),
        }
        csv_path = self._create_test_csv(tmp_path, channels=channels, num_samples=5)

        config = PostProcessConfig(
            source_path=csv_path,
            channels_to_process=[],
            calculate_rate_of_change=["RPM"],
        )

        result_path = processor.process_and_save(csv_path, config)

        # Parse output
        with open(result_path, "r", newline="", encoding="utf-8") as f:
            reader = csv.reader(f)
            rows = [row for row in reader if row and not row[0].startswith("#")]

        data_rows = rows[1:]
        rate_rows = [r for r in data_rows if r[1] == "RPM_rate"]

        # Rate = (10-0)/0.1 = 100 for all intervals
        assert len(rate_rows) == 4
        for row in rate_rows:
            assert float(row[2]) == pytest.approx(100.0, rel=1e-3)
            assert row[3] == "rpm/s"

    def test_source_file_not_found_raises(self, processor, tmp_path):
        """Should raise FileNotFoundError for non-existent source."""
        from rotax_dyno_daq.core.models import PostProcessConfig
        from pathlib import Path

        config = PostProcessConfig(
            source_path=tmp_path / "nonexistent.csv",
            channels_to_process=["EGT1"],
        )

        with pytest.raises(FileNotFoundError):
            processor.process_and_save(tmp_path / "nonexistent.csv", config)

    def test_preserves_header_metadata(self, processor, tmp_path):
        """Processed file should preserve original header metadata."""
        from rotax_dyno_daq.core.models import PostProcessConfig
        import csv

        csv_path = self._create_test_csv(tmp_path)

        config = PostProcessConfig(
            source_path=csv_path,
            channels_to_process=["EGT1"],
            moving_average_window=3,
        )

        result_path = processor.process_and_save(csv_path, config)

        # Read header lines from processed file
        with open(result_path, "r", newline="", encoding="utf-8") as f:
            reader = csv.reader(f)
            header_lines = []
            for row in reader:
                if row and row[0].startswith("#"):
                    header_lines.append(row)
                else:
                    break

        # Should contain the original metadata
        assert any("Test Run" in str(row) for row in header_lines)
        assert any("Test Operator" in str(row) for row in header_lines)

    def test_output_in_same_directory_as_source(self, processor, tmp_path):
        """Processed file should be in the same directory as the source."""
        from rotax_dyno_daq.core.models import PostProcessConfig

        subdir = tmp_path / "data" / "runs"
        subdir.mkdir(parents=True)
        csv_path = self._create_test_csv(subdir, filename="run_001.csv")

        config = PostProcessConfig(
            source_path=csv_path,
            channels_to_process=["EGT1"],
            moving_average_window=3,
        )

        result_path = processor.process_and_save(csv_path, config)

        assert result_path.parent == subdir
        assert result_path.name == "run_001_processed.csv"

    def test_no_processing_copies_data_unchanged(self, processor, tmp_path):
        """With no filters configured, output data should match input."""
        from rotax_dyno_daq.core.models import PostProcessConfig
        import csv

        channels = {
            "EGT1": ("°C", 5.0, [600.0, 610.0, 620.0]),
        }
        csv_path = self._create_test_csv(tmp_path, channels=channels, num_samples=3)

        config = PostProcessConfig(
            source_path=csv_path,
            channels_to_process=["EGT1"],
            # No filters configured
        )

        result_path = processor.process_and_save(csv_path, config)

        # Parse output
        with open(result_path, "r", newline="", encoding="utf-8") as f:
            reader = csv.reader(f)
            rows = [row for row in reader if row and not row[0].startswith("#")]

        data_rows = rows[1:]
        egt1_rows = [r for r in data_rows if r[1] == "EGT1"]

        # Values should be unchanged
        assert float(egt1_rows[0][2]) == pytest.approx(600.0)
        assert float(egt1_rows[1][2]) == pytest.approx(610.0)
        assert float(egt1_rows[2][2]) == pytest.approx(620.0)
