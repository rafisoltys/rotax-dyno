# Requirements Document

## Introduction

A data acquisition system for Raspberry Pi using Digilent MCC DAQ HATs to monitor a Rotax 912 ULS engine on a dynamometer. The system provides real-time sensor visualization on a touchscreen-friendly desktop application, logs data to CSV files, supports cloud storage with remote monitoring, and includes configurable alarms and post-processing capabilities.

## Glossary

- **DAQ_System**: The complete data acquisition application running on the Raspberry Pi
- **MCC_134**: Digilent MCC 134 thermocouple measurement HAT for temperature channels
- **MCC_118**: Digilent MCC 118 analog voltage measurement HAT for pressure, RPM, and AFR channels
- **MCC_152**: Digilent MCC 152 digital I/O HAT for future expansion (alarm outputs, triggers)
- **Dashboard**: The main touchscreen-friendly desktop application window displaying live sensor data
- **Engine_Overlay**: A visual representation of the Rotax 912 ULS engine where sensor readings are displayed at their physical measurement locations
- **Channel**: A single sensor input with associated calibration, scaling, and display configuration
- **Run**: A discrete recording session with start/stop times and associated metadata
- **Cloud_Service**: The remote storage and access service for uploading run data and enabling remote monitoring
- **Alarm_Manager**: The subsystem responsible for monitoring channel values against configured thresholds
- **Calibration_Profile**: A configuration mapping raw sensor voltage/counts to engineering units
- **Post_Processor**: The subsystem for filtering, smoothing, and deriving calculated values from recorded data

## Requirements

### Requirement 1: Temperature Acquisition

**User Story:** As a dyno operator, I want to measure exhaust gas temperatures, coolant temperature, oil temperature, and intake air temperature, so that I can monitor engine thermal conditions in real time.

#### Acceptance Criteria

1. THE DAQ_System SHALL acquire temperature data from EGT1, EGT2, EGT3, EGT4, CLT, OilTemp, and IAT channels using the MCC_134
2. WHEN a temperature channel is sampled, THE DAQ_System SHALL convert the raw thermocouple reading to degrees Celsius using the MCC_134 built-in cold junction compensation
3. THE DAQ_System SHALL sample all thermocouple channels at a configurable rate between 1 Hz and 10 Hz, with a default rate of 5 Hz
4. IF a thermocouple channel reports an open-circuit fault, THEN THE DAQ_System SHALL display a sensor fault indicator for that channel on the Dashboard and mark the reading as invalid in logged data

### Requirement 2: Pressure Acquisition

**User Story:** As a dyno operator, I want to measure oil pressure and intake/charge pressure, so that I can monitor engine mechanical health and boost levels.

#### Acceptance Criteria

1. THE DAQ_System SHALL acquire analog voltage data from OilP and ChargeP channels using the MCC_118
2. WHEN a pressure channel is sampled, THE DAQ_System SHALL convert the raw voltage to engineering units (bar or kPa) using the configured Calibration_Profile
3. THE DAQ_System SHALL sample pressure channels at a configurable rate between 10 Hz and 100 Hz, with a default rate of 10 Hz when no rate is explicitly configured
4. IF a pressure channel reading falls outside the valid voltage range defined in that channel's Calibration_Profile, THEN THE DAQ_System SHALL mark the reading as invalid in logged data and display a sensor fault indicator for that channel on the Dashboard
5. THE Calibration_Profile for each pressure channel SHALL include a minimum and maximum valid voltage threshold defining the sensor's expected output range

### Requirement 3: RPM Acquisition

**User Story:** As a dyno operator, I want to measure engine rotational speed, so that I can correlate all sensor data with engine operating point.

#### Acceptance Criteria

1. THE DAQ_System SHALL acquire the RPM signal via the MCC_118 analog input
2. WHEN an RPM sample is acquired, THE DAQ_System SHALL convert the voltage to RPM using the configured Calibration_Profile and constrain the output to the range 0 to 9000 RPM
3. THE DAQ_System SHALL sample the RPM channel at a configurable rate between 10 Hz and 100 Hz, with a default rate of 50 Hz
4. IF the RPM signal voltage is below the minimum valid threshold defined in the Calibration_Profile, THEN THE DAQ_System SHALL report RPM as zero
5. IF the RPM signal voltage exceeds the maximum valid threshold defined in the Calibration_Profile, THEN THE DAQ_System SHALL flag the reading as invalid

### Requirement 4: Air-Fuel Ratio Acquisition

**User Story:** As a dyno operator, I want to measure air-fuel ratio from four wideband lambda sensors, so that I can monitor combustion quality per cylinder.

#### Acceptance Criteria

1. THE DAQ_System SHALL acquire analog voltage data from AFR1, AFR2, AFR3, and AFR4 channels using the MCC_118
2. WHEN an AFR channel is sampled, THE DAQ_System SHALL convert the raw voltage to the output unit (lambda or AFR) specified in that channel's Calibration_Profile
3. THE DAQ_System SHALL sample AFR channels at a configurable rate between 10 Hz and 50 Hz, with a default rate of 20 Hz
4. IF an AFR channel voltage reading is outside the configured valid output range (as defined in the channel's Calibration_Profile, within the MCC_118 input range of 0.0V to 5.0V), THEN THE DAQ_System SHALL mark the reading as invalid in the data stream and display a sensor fault indicator for that channel on the Dashboard
5. IF a Calibration_Profile is not configured for an AFR channel, THEN THE DAQ_System SHALL display the raw voltage value and indicate that the channel is uncalibrated

### Requirement 5: Live Dashboard Display

**User Story:** As a dyno operator, I want a touchscreen-friendly live dashboard showing all sensor values, so that I can monitor the engine at a glance during dyno runs.

#### Acceptance Criteria

1. THE Dashboard SHALL display all active channel values with a display refresh rate of at least 10 Hz, regardless of individual channel sampling rates
2. THE Dashboard SHALL render the Engine_Overlay showing sensor readings positioned at their physical measurement locations on a Rotax 912 ULS background image
3. THE Dashboard SHALL provide numeric readouts, status indicators color-coded to Alarm_Manager severity levels (normal, warning, critical), and time-series strip charts with a configurable visible time window between 30 seconds and 600 seconds for each channel
4. THE Dashboard SHALL support touch interaction with a minimum touch target size of 12 mm × 12 mm for navigating between views, starting/stopping runs, and adjusting alarm thresholds and sampling rates
5. WHILE a Run is active, THE Dashboard SHALL display a visible recording indicator and elapsed run time updated at 1-second resolution
6. IF a channel value has not been updated within 3 seconds, THEN THE Dashboard SHALL display a stale-data indicator for that channel

### Requirement 6: Data Logging to CSV

**User Story:** As a dyno operator, I want all sensor data logged to CSV files organized by run, so that I can review and analyze data after testing.

#### Acceptance Criteria

1. WHEN a Run is started, THE DAQ_System SHALL create a new CSV file named with an ISO 8601 timestamp (YYYYMMDD_HHMMSS) and run name, and write a header section containing run name, start time, operator notes, channel list, and sampling rates
2. WHILE a Run is active, THE DAQ_System SHALL write all channel samples to the CSV file at each channel's configured sampling rate, with a millisecond-resolution timestamp column relative to run start
3. WHILE a Run is active, THE DAQ_System SHALL flush buffered data to disk at least once per second to limit data loss to no more than 1 second of samples in the event of abnormal termination
4. WHEN a Run is stopped, THE DAQ_System SHALL close the CSV file and append run summary metadata including total duration, sample count per channel, and min/max values per channel
5. THE DAQ_System SHALL store CSV files in a configurable directory on local storage
6. IF available disk space falls below 50 MB during logging, THEN THE DAQ_System SHALL alert the operator and continue logging until the Run is stopped or disk space is exhausted
7. IF a write error occurs during logging, THEN THE DAQ_System SHALL alert the operator on the Dashboard and attempt to continue logging to a secondary configurable fallback directory

### Requirement 7: Cloud Upload

**User Story:** As a dyno operator, I want completed run files automatically uploaded to cloud storage, so that I can access data from any device and maintain backups.

#### Acceptance Criteria

1. WHEN a Run is completed, THE Cloud_Service SHALL initiate upload of the CSV file to the configured remote storage within 30 seconds of run completion
2. IF the network connection is unavailable during upload, THEN THE Cloud_Service SHALL queue the file and retry upload at intervals of 60 seconds for a maximum of 10 attempts
3. IF all retry attempts are exhausted without successful upload, THEN THE Cloud_Service SHALL mark the file as failed and notify the operator via the Dashboard
4. THE Cloud_Service SHALL maintain an upload status indicator showing pending, in-progress, completed, and failed states for each queued file
5. THE Cloud_Service SHALL queue a maximum of 100 pending upload files; IF the queue is full, THEN THE Cloud_Service SHALL notify the operator and discard no files
6. THE DAQ_System SHALL provide configuration for cloud storage credentials and destination path
7. IF an upload does not complete within 300 seconds, THEN THE Cloud_Service SHALL cancel the upload attempt and treat it as a failed attempt

### Requirement 8: Remote Live Monitoring

**User Story:** As a dyno operator or remote engineer, I want to view live sensor data from a web browser on any device on the network, so that I can monitor the engine remotely.

#### Acceptance Criteria

1. THE DAQ_System SHALL serve a web-based live monitoring interface on a configurable network port accessible to any device on the local network without requiring plugin installation
2. WHILE the DAQ_System is acquiring data, THE remote monitoring interface SHALL display current channel values with updates at a minimum of 1 Hz and a maximum data latency of 2 seconds from acquisition to display
3. THE remote monitoring interface SHALL display a functionally equivalent Engine_Overlay view showing the same channel values positioned at their physical measurement locations as the local Dashboard
4. THE DAQ_System SHALL support at least 3 simultaneous remote monitoring connections
5. IF a remote client connects when the maximum simultaneous connection limit is reached, THEN THE DAQ_System SHALL reject the connection and return an indication that the maximum number of monitoring sessions is active
6. IF the DAQ_System is not actively acquiring data, THEN THE remote monitoring interface SHALL display a status indication that acquisition is inactive and show the last known channel values or no-data placeholders
7. IF a remote monitoring connection is lost, THEN THE remote monitoring interface SHALL display a disconnected status indicator and attempt to reconnect automatically within 5 seconds

### Requirement 9: Historical Data Browsing

**User Story:** As a dyno operator, I want to browse and compare past runs from any device, so that I can track engine performance over time.

#### Acceptance Criteria

1. THE Cloud_Service SHALL provide a paginated list of all uploaded runs displaying metadata (date, duration, notes) sorted by date descending, with a maximum of 50 runs per page
2. WHEN a historical run is selected, THE Cloud_Service SHALL display time-series charts for all recorded channels within 5 seconds of selection, with zoom and pan controls for navigating the time axis
3. THE Cloud_Service SHALL allow comparison of 2 to 5 runs on the same time-series chart, aligned by elapsed time from run start
4. THE Cloud_Service SHALL support filtering runs by date range and one or more metadata tags applied simultaneously
5. IF a selected run's data cannot be loaded, THEN THE Cloud_Service SHALL display an error indication identifying the unavailable run and allow the operator to continue browsing remaining runs

### Requirement 10: Alarm and Threshold Configuration

**User Story:** As a dyno operator, I want configurable alarm thresholds for each channel, so that I am immediately alerted to dangerous engine conditions.

#### Acceptance Criteria

1. THE Alarm_Manager SHALL allow configuration of high and low alarm thresholds for each channel at each severity level (warning, critical)
2. WHEN a channel value rises above a configured high threshold or falls below a configured low threshold, THE Alarm_Manager SHALL trigger a visual alarm on the Dashboard within 500 milliseconds of the threshold crossing
3. WHEN a channel value rises above a configured high threshold or falls below a configured low threshold, THE Alarm_Manager SHALL trigger an audible alarm within 500 milliseconds of the threshold crossing
4. THE Alarm_Manager SHALL support two severity levels (warning, critical) with visually distinguishable indicators and audibly distinguishable tones for each level
5. WHILE an alarm condition is active, THE Dashboard SHALL highlight the affected channel in the Engine_Overlay with a severity-specific visual indicator
6. WHEN a channel value returns within its configured thresholds by at least a configurable deadband amount, THE Alarm_Manager SHALL clear the alarm condition and remove the visual and audible indicators
7. WHEN the operator acknowledges an active alarm, THE Alarm_Manager SHALL silence the audible indicator while maintaining the visual indicator until the alarm condition clears

### Requirement 11: Channel Calibration

**User Story:** As a dyno operator, I want to configure calibration profiles for each analog channel, so that raw sensor voltages are correctly converted to engineering units.

#### Acceptance Criteria

1. THE DAQ_System SHALL provide a calibration configuration interface for each Channel that allows specifying the engineering unit label, calibration type (linear or lookup table), and associated parameters
2. THE Calibration_Profile SHALL support linear scaling (slope and offset) from raw voltage to engineering units, where the operator specifies slope and offset as floating-point values
3. THE Calibration_Profile SHALL support lookup table calibration with a minimum of 2 and a maximum of 64 voltage-to-engineering-unit point pairs, using linear interpolation between defined points
4. WHEN a Calibration_Profile is modified, THE DAQ_System SHALL apply the new calibration to subsequent readings within 1 second without restarting acquisition
5. THE DAQ_System SHALL store Calibration_Profile configurations persistently between sessions
6. IF a raw voltage reading falls outside the defined lookup table range, THEN THE DAQ_System SHALL clamp the output to the nearest boundary value and flag the reading as out-of-range
7. IF a Calibration_Profile is saved with invalid parameters (fewer than 2 lookup table points, or duplicate voltage entries), THEN THE DAQ_System SHALL reject the configuration and display an error message indicating the validation failure

### Requirement 12: Post-Processing

**User Story:** As a dyno operator, I want to apply filtering and smoothing to recorded data, so that I can produce clean data for analysis and reporting.

#### Acceptance Criteria

1. THE Post_Processor SHALL provide configurable low-pass filtering for any recorded channel with a user-selectable cutoff frequency between 0.1 Hz and half the channel's sampling rate
2. THE Post_Processor SHALL provide moving average smoothing with a configurable window size between 3 and 101 samples
3. THE Post_Processor SHALL calculate derived channels from recorded data, including EGT spread defined as the difference between the maximum and minimum EGT channel values at each timestamp, and rate of change defined as the difference between consecutive samples divided by the sample interval in seconds
4. WHEN post-processing is applied, THE Post_Processor SHALL save the processed data as a new CSV file in the same directory as the source file, preserving the original raw data file unmodified
5. THE Post_Processor SHALL provide a visual preview displaying raw and processed data as overlaid time-series charts for selected channels before saving
6. IF the operator specifies a cutoff frequency above half the channel's sampling rate or a window size outside the valid range, THEN THE Post_Processor SHALL reject the parameter and display an error message indicating the valid range
7. IF the source data contains missing or invalid samples, THEN THE Post_Processor SHALL exclude those samples from filter calculations and mark the corresponding output samples as invalid in the processed file

### Requirement 13: Run Management

**User Story:** As a dyno operator, I want to organize recordings into named runs with metadata, so that I can track test conditions and find specific data easily.

#### Acceptance Criteria

1. WHEN starting a new Run, THE DAQ_System SHALL prompt for a run name (1 to 100 characters) and optional notes (up to 1000 characters) for engine config and ambient conditions
2. IF the operator submits an empty run name or a run name that duplicates an existing run name, THEN THE DAQ_System SHALL reject the submission and display an error message indicating the naming constraint violated
3. THE DAQ_System SHALL maintain a run log listing all completed runs with the following summary statistics: run name, date/time, duration, min/max/mean values for each recorded channel, and associated notes
4. THE DAQ_System SHALL allow tagging runs with up to 10 custom labels per run, each label up to 50 characters, for categorization
5. THE DAQ_System SHALL support filtering and searching the run log by run name, date range, and tags
6. WHEN a run export is requested, THE DAQ_System SHALL export run data as a CSV file with a header row containing channel names, a timestamp column in ISO 8601 format, and one column per recorded channel in engineering units

### Requirement 14: System Configuration Persistence

**User Story:** As a dyno operator, I want all system settings saved between sessions, so that I do not need to reconfigure the system each time it starts.

#### Acceptance Criteria

1. WHEN a configuration parameter is modified by the operator, THE DAQ_System SHALL persist all configuration (sampling rates, calibration profiles, alarm thresholds, cloud settings) to a local configuration file within 5 seconds of the change
2. WHEN the DAQ_System starts, THE DAQ_System SHALL load the last saved configuration and apply it to all subsystems before data acquisition begins
3. THE DAQ_System SHALL provide an interface to export the current configuration to a file and import a configuration from a file, validating the imported file structure and value ranges before applying it
4. IF the configuration file is corrupted or missing at startup, THEN THE DAQ_System SHALL start with factory default values (mid-range sampling rates, no alarm thresholds active, no cloud settings, default calibration profiles with unity scaling) and display a persistent notification to the operator indicating which configuration could not be loaded
5. IF an imported configuration file contains invalid or out-of-range values, THEN THE DAQ_System SHALL reject the import, retain the current configuration, and notify the operator indicating which values failed validation
6. IF a configuration save operation fails, THEN THE DAQ_System SHALL notify the operator that settings were not persisted and retry on the next configuration change
