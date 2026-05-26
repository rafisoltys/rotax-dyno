# Implementation Plan: Rotax Dyno DAQ

## Overview

A Python-based data acquisition system for Raspberry Pi using Digilent MCC DAQ HATs to monitor a Rotax 912 ULS engine on a dynamometer. Implementation follows a bottom-up approach: core data models and configuration first, then acquisition and calibration, followed by storage and alarms, and finally the presentation layer (Dashboard + remote monitoring).

## Tasks

- [x] 1. Set up project structure, dependencies, and core data models
  - [x] 1.1 Create project directory structure and package configuration
    - Create `src/rotax_dyno_daq/` package with `__init__.py`
    - Create subpackages: `acquisition/`, `calibration/`, `core/`, `storage/`, `alarms/`, `processing/`, `dashboard/`, `web/`, `config/`
    - Create `pyproject.toml` with dependencies: `daqhats`, `PyQt6`, `pyqtgraph`, `fastapi`, `uvicorn`, `websockets`, `boto3`, `numpy`, `scipy`, `tomli-w`, `hypothesis`, `pytest`
    - Create `tests/` directory with `property/`, `unit/`, `integration/` subdirectories and `conftest.py`
    - _Requirements: 14.1, 14.2_

  - [x] 1.2 Implement core data models and enumerations
    - Create `src/rotax_dyno_daq/core/models.py` with all dataclasses: `RawSample`, `CalibratedSample`, `ChannelConfig`, `CalibrationProfile`, `LinearCalibrationParams`, `LookupTableParams`, `AlarmThreshold`, `AlarmConfig`, `ActiveAlarm`, `RunInfo`, `RunSummary`, `CloudConfig`, `UploadTask`, `PostProcessConfig`, `SystemConfig`
    - Create `src/rotax_dyno_daq/core/enums.py` with all enumerations: `ChannelType`, `CalibrationType`, `AlarmSeverity`, `AlarmState`, `UploadStatus`, `SampleValidity`
    - _Requirements: 1.1, 2.1, 3.1, 4.1, 10.1, 11.1, 14.1_

  - [x] 1.3 Implement the Data Bus (Pub/Sub)
    - Create `src/rotax_dyno_daq/core/data_bus.py` with thread-safe `DataBus` class
    - Implement `publish(topic, sample)`, `subscribe(topic, callback)`, `unsubscribe(subscription_id)` methods
    - Use `threading.Lock` for thread safety and support multiple subscribers per topic
    - _Requirements: 5.1, 8.2_

- [x] 2. Implement Configuration Manager
  - [x] 2.1 Implement TOML configuration persistence
    - Create `src/rotax_dyno_daq/config/manager.py` with `ConfigurationManager` class
    - Implement `load()` to read TOML config file, returning factory defaults if file is missing/corrupted
    - Implement `save()` to persist configuration within 5 seconds of changes
    - Implement `get(key)` and `set(key, value)` with dotted key path support
    - Factory defaults: mid-range sampling rates, no alarm thresholds, no cloud settings, unity calibration
    - _Requirements: 14.1, 14.2, 14.4_

  - [x] 2.2 Implement configuration export/import with validation
    - Implement `export_config(path)` to write current config to a specified TOML file
    - Implement `import_config(path)` to validate and load config from file
    - Validate all value ranges (sample rates within bounds, non-negative deadband, valid calibration params)
    - Reject invalid imports, retain current config, and return validation errors
    - _Requirements: 14.3, 14.5, 14.6_

  - [ ]* 2.3 Write property test for configuration round-trip (Property 20)
    - **Property 20: Configuration serialization round-trip**
    - Generate arbitrary valid `SystemConfig` objects, serialize to TOML, deserialize, and verify equivalence
    - **Validates: Requirements 14.2, 14.3**

  - [ ]* 2.4 Write property test for configuration import validation (Property 30)
    - **Property 30: Configuration import validation**
    - Generate config files with out-of-range values, verify import rejects them and current config is unchanged
    - **Validates: Requirements 14.5**

- [x] 3. Implement Calibration Engine
  - [x] 3.1 Implement linear calibration and lookup table calibration
    - Create `src/rotax_dyno_daq/calibration/engine.py` with `CalibrationEngine` class
    - Implement `LinearCalibration.convert(raw)` returning `slope * raw + offset`
    - Implement `LookupTableCalibration.convert(raw)` with piecewise linear interpolation between sorted points
    - Implement out-of-range clamping for lookup tables (clamp to nearest boundary, flag OUT_OF_RANGE)
    - _Requirements: 2.2, 3.2, 4.2, 11.2, 11.3, 11.6_

  - [x] 3.2 Implement calibration profile validation and hot-swap
    - Implement `validate_profile(profile)` rejecting profiles with < 2 lookup points or duplicate voltages
    - Implement `update_profile(channel_id, profile)` for hot-swapping calibration (takes effect on next sample)
    - Implement `apply(channel_id, raw_value)` returning `CalibratedSample` with validity marking for out-of-range voltages
    - _Requirements: 11.4, 11.7, 2.4, 4.4_

  - [x] 3.3 Implement sample rate clamping and defaults
    - Create `src/rotax_dyno_daq/calibration/rate_config.py` with rate validation logic
    - Clamp thermocouple rates to [1, 10] Hz, default 5 Hz
    - Clamp pressure/RPM rates to [10, 100] Hz, defaults 10 Hz / 50 Hz
    - Clamp AFR rates to [10, 50] Hz, default 20 Hz
    - _Requirements: 1.3, 2.3, 3.3, 4.3_

  - [ ]* 3.4 Write property tests for calibration (Properties 1, 4, 5, 6, 7, 8)
    - **Property 1: Sample rate clamping and defaults** — verify rates are clamped to valid ranges and defaults applied
    - **Property 4: Linear calibration correctness** — verify `slope * voltage + offset` for any valid input
    - **Property 5: Lookup table interpolation correctness** — verify linear interpolation between bracketing points
    - **Property 6: Lookup table out-of-range clamping** — verify clamping and OUT_OF_RANGE flag
    - **Property 7: RPM below-minimum yields zero** — verify RPM reports zero for below-threshold voltage
    - **Property 8: RPM output clamping** — verify RPM constrained to [0, 9000]
    - **Validates: Requirements 1.3, 2.2, 2.3, 2.4, 3.2, 3.3, 3.4, 3.5, 4.2, 4.3, 11.2, 11.3, 11.6**

  - [ ]* 3.5 Write property test for calibration profile validation (Property 19)
    - **Property 19: Calibration profile validation**
    - Generate profiles with < 2 points or duplicate voltages, verify rejection; generate valid profiles, verify acceptance
    - **Validates: Requirements 11.7**

- [x] 4. Implement HAT Readers (Acquisition Layer)
  - [x] 4.1 Implement base HatReader and ThermocoupleReader
    - Create `src/rotax_dyno_daq/acquisition/hat_reader.py` with abstract `HatReader` base class
    - Implement `ThermocoupleReader` using `daqhats.mcc134.t_in_read()` with cold junction compensation
    - Detect open-circuit faults (`TC_OPEN` status) and mark samples as INVALID
    - Run acquisition in a dedicated background thread at configured rate
    - Implement `start()`, `stop()`, `set_sample_rate()` methods
    - _Requirements: 1.1, 1.2, 1.3, 1.4_

  - [x] 4.2 Implement AnalogVoltageReader for MCC 118
    - Create `AnalogVoltageReader` class using `daqhats.mcc118.a_in_read()`
    - Support pressure (OilP, ChargeP), RPM, and AFR channels on the same HAT
    - Validate voltage against calibration profile min/max valid range
    - Implement RPM-specific logic: below-minimum yields zero, above-maximum flags invalid
    - Run acquisition in a dedicated background thread at configured rate
    - _Requirements: 2.1, 2.4, 3.1, 3.4, 3.5, 4.1, 4.4_

  - [ ]* 4.3 Write property tests for out-of-range and fault detection (Properties 2, 3)
    - **Property 2: Out-of-range voltage produces invalid sample** — verify INVALID marking for voltages outside calibration range
    - **Property 3: Open-circuit fault produces invalid sample** — verify INVALID marking for thermocouple open-circuit
    - **Validates: Requirements 1.4, 2.4, 3.5, 4.4**

- [x] 5. Checkpoint - Core acquisition pipeline
  - Ensure all tests pass, ask the user if questions arise.

- [x] 6. Implement Alarm Manager
  - [x] 6.1 Implement alarm threshold evaluation and state machine
    - Create `src/rotax_dyno_daq/alarms/manager.py` with `AlarmManager` class
    - Implement `configure_threshold(channel_id, config)` for setting high/low warning/critical thresholds
    - Implement `evaluate(channel_id, value)` with threshold crossing detection (within 500ms requirement met by bus subscription)
    - Implement deadband-based clearing: alarm clears only when value returns within threshold by at least deadband amount
    - Implement `acknowledge(alarm_id)` transitioning state to ACKNOWLEDGED (silences audible, keeps visual)
    - Implement `get_active_alarms()` returning all currently active alarm conditions
    - Subscribe to DataBus for channel updates
    - _Requirements: 10.1, 10.2, 10.3, 10.4, 10.5, 10.6, 10.7_

  - [ ]* 6.2 Write property tests for alarm logic (Properties 16, 17, 18)
    - **Property 16: Alarm threshold crossing detection** — verify ACTIVE state with correct severity on threshold crossing
    - **Property 17: Alarm deadband clearing** — verify alarm clears only when value returns by deadband amount
    - **Property 18: Alarm acknowledgment state** — verify ACKNOWLEDGED state transitions and clearing conditions
    - **Validates: Requirements 10.2, 10.3, 10.6, 10.7**

- [x] 7. Implement CSV Logger and Run Manager
  - [x] 7.1 Implement CSV Logger with flush and fault tolerance
    - Create `src/rotax_dyno_daq/storage/csv_logger.py` with `CsvLogger` class
    - Implement `start_run(run_info)` creating CSV file named `YYYYMMDD_HHMMSS_{run_name}.csv` with header section
    - Implement `write_sample(sample)` buffering samples for writing
    - Implement `flush()` called at least once per second to limit data loss to ≤ 1 second
    - Implement `stop_run()` closing file and appending summary metadata (duration, sample counts, min/max per channel)
    - Handle disk space monitoring (alert at < 50 MB), write errors (switch to fallback directory)
    - _Requirements: 6.1, 6.2, 6.3, 6.4, 6.5, 6.6, 6.7_

  - [x] 7.2 Implement Run Manager with metadata and validation
    - Create `src/rotax_dyno_daq/storage/run_manager.py` with `RunManager` class
    - Implement `start_run(name, notes)` with validation: name 1-100 chars, no duplicates, notes up to 1000 chars
    - Implement `stop_run()` finalizing run and triggering cloud upload
    - Implement `get_run_log(filters)` with filtering by name, date range, and tags, sorted by date descending
    - Implement `tag_run(run_id, tags)` with validation: up to 10 tags, each up to 50 chars
    - Implement `export_run(run_id, output_path)` exporting as CSV with ISO 8601 timestamps
    - _Requirements: 13.1, 13.2, 13.3, 13.4, 13.5, 13.6_

  - [ ]* 7.3 Write property tests for CSV and run management (Properties 10, 11, 12, 27, 28, 29)
    - **Property 10: CSV filename and header generation** — verify filename pattern and header contents
    - **Property 11: CSV sample serialization round-trip** — verify write/parse produces equivalent sample
    - **Property 12: Run summary correctness** — verify duration, sample counts, min/max calculations
    - **Property 27: Run metadata validation** — verify rejection of invalid names, duplicate names, excess tags
    - **Property 28: Run log filtering correctness** — verify filtered results match all criteria simultaneously
    - **Property 29: Run pagination correctness** — verify correct page count and all runs appear exactly once
    - **Validates: Requirements 6.1, 6.2, 6.4, 13.1, 13.2, 13.4, 13.5, 9.1**

- [x] 8. Implement Cloud Uploader
  - [x] 8.1 Implement upload queue with retry logic and state machine
    - Create `src/rotax_dyno_daq/storage/cloud_uploader.py` with `CloudUploader` class
    - Implement `queue_upload(file_path)` adding files to upload queue (max 100 files)
    - Implement upload worker: initiate upload within 30 seconds of run completion, retry at 60s intervals, max 10 attempts
    - Implement 300-second upload timeout (cancel and count as failed attempt)
    - Implement state transitions: PENDING → IN_PROGRESS → COMPLETED or FAILED
    - Implement `get_status(file_path)` and `cancel(file_path)` methods
    - Use boto3 for S3-compatible storage upload
    - _Requirements: 7.1, 7.2, 7.3, 7.4, 7.5, 7.6, 7.7_

  - [ ]* 8.2 Write property tests for cloud uploader (Properties 13, 14)
    - **Property 13: Upload state machine validity** — verify valid state transitions and attempt count limits
    - **Property 14: Upload queue capacity enforcement** — verify queue accepts below 100, rejects at 100 without discarding
    - **Validates: Requirements 7.2, 7.3, 7.4, 7.5**

- [x] 9. Checkpoint - Storage and alarms complete
  - Ensure all tests pass, ask the user if questions arise.

- [x] 10. Implement Post-Processor
  - [x] 10.1 Implement filtering and derived channel calculations
    - Create `src/rotax_dyno_daq/processing/post_processor.py` with `PostProcessor` class
    - Implement `low_pass_filter(data, cutoff_hz, sample_rate_hz)` using SciPy Butterworth filter
    - Implement `moving_average(data, window_size)` with window size validation [3, 101]
    - Implement `calculate_spread(channels)` computing max - min across EGT channels per timestamp
    - Implement `calculate_rate_of_change(data, sample_interval_s)` computing (v2-v1)/dt
    - Implement parameter validation: reject cutoff > Nyquist, window outside [3, 101]
    - Handle invalid samples: exclude from filter calculations, mark corresponding outputs as invalid
    - _Requirements: 12.1, 12.2, 12.3, 12.6, 12.7_

  - [x] 10.2 Implement process-and-save pipeline
    - Implement `process_and_save(source_path, config)` applying processing pipeline and saving to new CSV
    - Preserve original raw data file unmodified
    - Save processed data as new CSV in same directory as source
    - _Requirements: 12.4_

  - [ ]* 10.3 Write property tests for post-processing (Properties 21, 22, 23, 24, 25, 26)
    - **Property 21: Low-pass filter frequency attenuation** — verify frequency components above cutoff are attenuated
    - **Property 22: Moving average correctness** — verify output equals arithmetic mean of window
    - **Property 23: Derived channel correctness** — verify EGT spread and rate of change formulas
    - **Property 24: Post-processing preserves original file** — verify source file unchanged after processing
    - **Property 25: Post-processing parameter validation** — verify rejection of invalid cutoff/window values
    - **Property 26: Invalid sample exclusion in filtering** — verify invalid samples excluded from computation
    - **Validates: Requirements 12.1, 12.2, 12.3, 12.4, 12.6, 12.7**

- [x] 11. Implement Dashboard (PyQt6)
  - [x] 11.1 Implement main Dashboard window and navigation
    - Create `src/rotax_dyno_daq/dashboard/main_window.py` with `DashboardWindow(QMainWindow)` class
    - Implement tabbed navigation between Engine Overlay, Strip Charts, Alarm Config, Run Management, and Post-Processing views
    - Ensure minimum touch target size of 12mm × 12mm for all interactive elements
    - Implement recording indicator and elapsed run time display (updated at 1-second resolution)
    - _Requirements: 5.4, 5.5_

  - [x] 11.2 Implement Engine Overlay widget
    - Create `src/rotax_dyno_daq/dashboard/engine_overlay.py` with `EngineOverlayWidget(QWidget)` class
    - Render Rotax 912 ULS background image with sensor readings positioned at physical measurement locations
    - Color-code readings based on alarm severity (normal=green, warning=amber, critical=red)
    - Display stale-data indicator when channel not updated within 3 seconds
    - Refresh display at minimum 10 Hz
    - _Requirements: 5.1, 5.2, 5.3, 5.6, 10.5_

  - [x] 11.3 Implement Strip Chart widgets with PyQtGraph
    - Create `src/rotax_dyno_daq/dashboard/strip_chart.py` with `StripChartWidget(pg.PlotWidget)` class
    - Implement real-time scrolling time-series charts for each channel
    - Support configurable visible time window between 30 and 600 seconds
    - Subscribe to DataBus for live data updates at ≥ 10 Hz refresh rate
    - _Requirements: 5.1, 5.3_

  - [x] 11.4 Implement Alarm indicator and audible alerts
    - Create `src/rotax_dyno_daq/dashboard/alarm_widget.py` with `AlarmIndicatorWidget(QWidget)` class
    - Display visual alarm indicators with severity-specific colors and patterns
    - Implement audible alarm with distinguishable tones for warning vs critical
    - Implement acknowledge button to silence audible while maintaining visual
    - Trigger within 500ms of threshold crossing (via DataBus subscription)
    - _Requirements: 10.2, 10.3, 10.4, 10.5, 10.7_

  - [x] 11.5 Implement Run Management and Calibration UI panels
    - Create run start/stop dialog with name input (1-100 chars) and notes field (up to 1000 chars)
    - Create calibration configuration panel: unit label, calibration type selection, slope/offset or lookup table entry
    - Create alarm threshold configuration panel with high/low warning/critical inputs and deadband
    - Display run log with filtering by name, date range, and tags
    - _Requirements: 13.1, 13.2, 13.5, 11.1, 10.1_

  - [x] 11.6 Implement Post-Processing UI with preview
    - Create post-processing panel with filter parameter inputs (cutoff frequency, window size)
    - Implement visual preview displaying raw and processed data as overlaid time-series charts
    - Validate parameters before processing (cutoff ≤ Nyquist, window in [3, 101])
    - Trigger `PostProcessor.process_and_save()` on user confirmation
    - _Requirements: 12.1, 12.2, 12.5, 12.6_

  - [ ]* 11.7 Write property test for stale data detection (Property 9)
    - **Property 9: Stale data detection**
    - Verify that any channel with no update for > 3 seconds is marked as stale
    - **Validates: Requirements 5.6**

- [x] 12. Checkpoint - Dashboard complete
  - Ensure all tests pass, ask the user if questions arise.

- [x] 13. Implement Remote Monitoring (FastAPI + WebSocket)
  - [x] 13.1 Implement FastAPI WebSocket server for live data streaming
    - Create `src/rotax_dyno_daq/web/server.py` with FastAPI application
    - Implement `/ws/live` WebSocket endpoint streaming channel data at ≥ 1 Hz with ≤ 2 seconds latency
    - Implement connection limiting: max 3 simultaneous connections, reject with capacity message when full
    - Handle client disconnection gracefully (remove from active connections, free slot)
    - Show acquisition-inactive status and last known values when not acquiring
    - _Requirements: 8.1, 8.2, 8.4, 8.5, 8.6, 8.7_

  - [x] 13.2 Implement REST API for historical data browsing
    - Implement `GET /api/runs` with pagination (50 per page), date range filtering, and tag filtering
    - Implement `GET /api/runs/{run_id}/data` returning time-series data for charting
    - Support comparison of 2-5 runs aligned by elapsed time
    - Return error indication for unavailable run data
    - _Requirements: 9.1, 9.2, 9.3, 9.4, 9.5_

  - [x] 13.3 Implement web-based remote monitoring frontend
    - Create static HTML/JS frontend served by FastAPI
    - Implement Engine Overlay view functionally equivalent to local Dashboard
    - Implement auto-reconnect within 5 seconds on connection loss with disconnected status indicator
    - Ensure no plugin installation required (pure HTML5/WebSocket)
    - _Requirements: 8.1, 8.3, 8.7_

  - [ ]* 13.4 Write property test for connection limiting (Property 15)
    - **Property 15: Remote connection limiting**
    - Verify connections accepted below max (3), rejected at max, and freed slots allow new connections
    - **Validates: Requirements 8.4, 8.5**

- [x] 14. Integration wiring and application entry point
  - [x] 14.1 Wire all components together in application entry point
    - Create `src/rotax_dyno_daq/app.py` as the main application entry point
    - Initialize ConfigurationManager, load config (or factory defaults with notification)
    - Initialize DataBus, CalibrationEngine, AlarmManager, RunManager, CsvLogger, CloudUploader
    - Initialize HAT readers with configured channels and rates
    - Start PyQt6 application with Dashboard window
    - Start FastAPI server in background thread on configured port
    - Wire DataBus subscriptions: Dashboard, CsvLogger, AlarmManager, WebSocket broadcaster
    - Implement graceful shutdown: stop acquisition, flush CSV, close connections
    - _Requirements: 14.2, 14.4, 5.1, 8.1_

  - [ ]* 14.2 Write integration tests for end-to-end data flow
    - Test data flow from mock HAT reader through calibration, DataBus, to CSV logger
    - Test alarm triggering from DataBus sample through to alarm state change
    - Test configuration hot-reload timing (< 1 second)
    - _Requirements: 1.1, 2.1, 10.2, 11.4_

- [x] 15. Final checkpoint - Ensure all tests pass
  - Ensure all tests pass, ask the user if questions arise.

## Notes

- Tasks marked with `*` are optional and can be skipped for faster MVP
- Each task references specific requirements for traceability
- Checkpoints ensure incremental validation
- Property tests validate universal correctness properties from the design document
- Unit tests validate specific examples and edge cases
- The `daqhats` library requires physical MCC HATs for integration testing; unit/property tests should mock hardware interfaces
- PyQt6 Dashboard tests may require a display or `QT_QPA_PLATFORM=offscreen` for CI environments

## Task Dependency Graph

```json
{
  "waves": [
    { "id": 0, "tasks": ["1.1"] },
    { "id": 1, "tasks": ["1.2", "1.3"] },
    { "id": 2, "tasks": ["2.1", "3.1", "3.3"] },
    { "id": 3, "tasks": ["2.2", "3.2", "2.3", "2.4", "3.4", "3.5"] },
    { "id": 4, "tasks": ["4.1", "4.2", "6.1"] },
    { "id": 5, "tasks": ["4.3", "6.2"] },
    { "id": 6, "tasks": ["7.1", "7.2", "8.1"] },
    { "id": 7, "tasks": ["7.3", "8.2"] },
    { "id": 8, "tasks": ["10.1"] },
    { "id": 9, "tasks": ["10.2", "10.3"] },
    { "id": 10, "tasks": ["11.1", "11.2", "11.3", "11.4"] },
    { "id": 11, "tasks": ["11.5", "11.6", "11.7"] },
    { "id": 12, "tasks": ["13.1", "13.2"] },
    { "id": 13, "tasks": ["13.3", "13.4"] },
    { "id": 14, "tasks": ["14.1"] },
    { "id": 15, "tasks": ["14.2"] }
  ]
}
```
