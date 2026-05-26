"""Unit tests for the CloudUploader class."""

import threading
import time
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from rotax_dyno_daq.core.enums import UploadStatus
from rotax_dyno_daq.core.models import CloudConfig, UploadTask
from rotax_dyno_daq.storage.cloud_uploader import (
    CloudUploader,
    FileNotInQueueError,
    UploadQueueFullError,
)


@pytest.fixture
def cloud_config() -> CloudConfig:
    """Create a test cloud configuration."""
    return CloudConfig(
        endpoint_url="http://localhost:9000",
        bucket_name="test-bucket",
        access_key="test-key",
        secret_key="test-secret",
        destination_prefix="dyno-data",
        upload_timeout_seconds=5,  # Short timeout for tests
        max_retries=3,  # Fewer retries for tests
        retry_interval_seconds=1,  # Short interval for tests
        max_queue_size=100,
    )


@pytest.fixture
def mock_s3_client() -> MagicMock:
    """Create a mock S3 client."""
    client = MagicMock()
    client.upload_file = MagicMock(return_value=None)
    return client


@pytest.fixture
def uploader(cloud_config: CloudConfig, mock_s3_client: MagicMock) -> CloudUploader:
    """Create a CloudUploader with mock S3 client."""
    return CloudUploader(config=cloud_config, s3_client=mock_s3_client)


class TestQueueUpload:
    """Tests for queue_upload method."""

    def test_queue_upload_adds_file(self, uploader: CloudUploader) -> None:
        """Test that queue_upload adds a file to the queue."""
        file_path = Path("/data/run_001.csv")
        uploader.queue_upload(file_path, run_id="run-001")

        assert uploader.queue_size == 1
        assert uploader.get_status(file_path) == UploadStatus.PENDING

    def test_queue_upload_multiple_files(self, uploader: CloudUploader) -> None:
        """Test queuing multiple files."""
        for i in range(5):
            uploader.queue_upload(Path(f"/data/run_{i:03d}.csv"), run_id=f"run-{i}")

        assert uploader.queue_size == 5

    def test_queue_upload_duplicate_file_ignored(self, uploader: CloudUploader) -> None:
        """Test that adding the same file twice doesn't create duplicates."""
        file_path = Path("/data/run_001.csv")
        uploader.queue_upload(file_path, run_id="run-001")
        uploader.queue_upload(file_path, run_id="run-001")

        assert uploader.queue_size == 1

    def test_queue_upload_full_raises_error(
        self, cloud_config: CloudConfig, mock_s3_client: MagicMock
    ) -> None:
        """Test that exceeding max queue size raises UploadQueueFullError."""
        config = CloudConfig(
            endpoint_url=cloud_config.endpoint_url,
            bucket_name=cloud_config.bucket_name,
            access_key=cloud_config.access_key,
            secret_key=cloud_config.secret_key,
            max_queue_size=5,  # Small queue for testing
        )
        uploader = CloudUploader(config=config, s3_client=mock_s3_client)

        # Fill the queue
        for i in range(5):
            uploader.queue_upload(Path(f"/data/run_{i:03d}.csv"), run_id=f"run-{i}")

        # Next one should raise
        with pytest.raises(UploadQueueFullError):
            uploader.queue_upload(Path("/data/run_005.csv"), run_id="run-5")

        # Queue size should still be 5 (no files discarded)
        assert uploader.queue_size == 5

    def test_queue_upload_at_capacity_100(
        self, mock_s3_client: MagicMock
    ) -> None:
        """Test queue capacity enforcement at exactly 100 files."""
        config = CloudConfig(
            endpoint_url="http://localhost:9000",
            bucket_name="test-bucket",
            access_key="key",
            secret_key="secret",
            max_queue_size=100,
        )
        uploader = CloudUploader(config=config, s3_client=mock_s3_client)

        # Fill to capacity
        for i in range(100):
            uploader.queue_upload(Path(f"/data/run_{i:03d}.csv"), run_id=f"run-{i}")

        assert uploader.queue_size == 100

        # 101st should fail
        with pytest.raises(UploadQueueFullError):
            uploader.queue_upload(Path("/data/run_100.csv"), run_id="run-100")

        # No files discarded
        assert uploader.queue_size == 100


class TestGetStatus:
    """Tests for get_status method."""

    def test_get_status_pending(self, uploader: CloudUploader) -> None:
        """Test status is PENDING after queuing."""
        file_path = Path("/data/run_001.csv")
        uploader.queue_upload(file_path)
        assert uploader.get_status(file_path) == UploadStatus.PENDING

    def test_get_status_file_not_in_queue(self, uploader: CloudUploader) -> None:
        """Test that getting status of unknown file raises error."""
        with pytest.raises(FileNotInQueueError):
            uploader.get_status(Path("/data/nonexistent.csv"))


class TestCancel:
    """Tests for cancel method."""

    def test_cancel_pending_upload(self, uploader: CloudUploader) -> None:
        """Test cancelling a pending upload removes it from queue."""
        file_path = Path("/data/run_001.csv")
        uploader.queue_upload(file_path)
        assert uploader.queue_size == 1

        uploader.cancel(file_path)
        assert uploader.queue_size == 0

    def test_cancel_nonexistent_file_raises(self, uploader: CloudUploader) -> None:
        """Test cancelling a file not in queue raises error."""
        with pytest.raises(FileNotInQueueError):
            uploader.cancel(Path("/data/nonexistent.csv"))

    def test_cancel_does_not_affect_other_files(
        self, uploader: CloudUploader
    ) -> None:
        """Test that cancelling one file doesn't affect others."""
        file1 = Path("/data/run_001.csv")
        file2 = Path("/data/run_002.csv")
        uploader.queue_upload(file1)
        uploader.queue_upload(file2)

        uploader.cancel(file1)

        assert uploader.queue_size == 1
        assert uploader.get_status(file2) == UploadStatus.PENDING


class TestUploadWorker:
    """Tests for the background upload worker."""

    def test_successful_upload(
        self, uploader: CloudUploader, mock_s3_client: MagicMock
    ) -> None:
        """Test that a queued file gets uploaded successfully."""
        file_path = Path("/data/run_001.csv")
        uploader.queue_upload(file_path, run_id="run-001")
        uploader.start()

        # Wait for upload to complete
        deadline = time.time() + 10
        while time.time() < deadline:
            status = uploader.get_status(file_path)
            if status == UploadStatus.COMPLETED:
                break
            time.sleep(0.1)

        uploader.stop()

        assert uploader.get_status(file_path) == UploadStatus.COMPLETED
        mock_s3_client.upload_file.assert_called_once_with(
            str(file_path), "test-bucket", "dyno-data/run_001.csv"
        )

    def test_upload_retry_on_failure(
        self,
        cloud_config: CloudConfig,
        mock_s3_client: MagicMock,
    ) -> None:
        """Test that failed uploads are retried."""
        # Fail first two attempts, succeed on third
        mock_s3_client.upload_file.side_effect = [
            Exception("Network error"),
            Exception("Network error"),
            None,  # Success
        ]

        config = CloudConfig(
            endpoint_url=cloud_config.endpoint_url,
            bucket_name=cloud_config.bucket_name,
            access_key=cloud_config.access_key,
            secret_key=cloud_config.secret_key,
            destination_prefix=cloud_config.destination_prefix,
            upload_timeout_seconds=5,
            max_retries=5,
            retry_interval_seconds=0,  # No delay for tests
            max_queue_size=100,
        )
        uploader = CloudUploader(config=config, s3_client=mock_s3_client)

        file_path = Path("/data/run_001.csv")
        uploader.queue_upload(file_path, run_id="run-001")
        uploader.start()

        # Wait for upload to complete
        deadline = time.time() + 15
        while time.time() < deadline:
            status = uploader.get_status(file_path)
            if status == UploadStatus.COMPLETED:
                break
            time.sleep(0.1)

        uploader.stop()

        assert uploader.get_status(file_path) == UploadStatus.COMPLETED
        assert mock_s3_client.upload_file.call_count == 3

    def test_upload_fails_after_max_retries(
        self,
        cloud_config: CloudConfig,
        mock_s3_client: MagicMock,
    ) -> None:
        """Test that upload is marked FAILED after max retries."""
        mock_s3_client.upload_file.side_effect = Exception("Persistent error")

        failure_callback = MagicMock()

        config = CloudConfig(
            endpoint_url=cloud_config.endpoint_url,
            bucket_name=cloud_config.bucket_name,
            access_key=cloud_config.access_key,
            secret_key=cloud_config.secret_key,
            destination_prefix=cloud_config.destination_prefix,
            upload_timeout_seconds=5,
            max_retries=3,
            retry_interval_seconds=0,  # No delay for tests
            max_queue_size=100,
        )
        uploader = CloudUploader(
            config=config,
            s3_client=mock_s3_client,
            on_failure_notify=failure_callback,
        )

        file_path = Path("/data/run_001.csv")
        uploader.queue_upload(file_path, run_id="run-001")
        uploader.start()

        # Wait for upload to fail
        deadline = time.time() + 15
        while time.time() < deadline:
            status = uploader.get_status(file_path)
            if status == UploadStatus.FAILED:
                break
            time.sleep(0.1)

        uploader.stop()

        assert uploader.get_status(file_path) == UploadStatus.FAILED
        assert mock_s3_client.upload_file.call_count == 3
        failure_callback.assert_called_once()

    def test_upload_timeout(
        self,
        mock_s3_client: MagicMock,
    ) -> None:
        """Test that uploads exceeding timeout are cancelled."""

        def slow_upload(*args: object, **kwargs: object) -> None:
            time.sleep(10)  # Longer than timeout

        mock_s3_client.upload_file.side_effect = slow_upload

        config = CloudConfig(
            endpoint_url="http://localhost:9000",
            bucket_name="test-bucket",
            access_key="key",
            secret_key="secret",
            destination_prefix="data",
            upload_timeout_seconds=1,  # Very short timeout
            max_retries=2,
            retry_interval_seconds=0,
            max_queue_size=100,
        )
        uploader = CloudUploader(config=config, s3_client=mock_s3_client)

        file_path = Path("/data/run_001.csv")
        uploader.queue_upload(file_path, run_id="run-001")
        uploader.start()

        # Wait for upload to fail after retries
        deadline = time.time() + 15
        while time.time() < deadline:
            status = uploader.get_status(file_path)
            if status == UploadStatus.FAILED:
                break
            time.sleep(0.2)

        uploader.stop()

        assert uploader.get_status(file_path) == UploadStatus.FAILED
        task = uploader.get_task(file_path)
        assert task is not None
        assert task.attempts == 2

    def test_start_stop_worker(self, uploader: CloudUploader) -> None:
        """Test starting and stopping the worker thread."""
        uploader.start()
        assert uploader._worker_thread is not None
        assert uploader._worker_thread.is_alive()

        uploader.stop()
        assert uploader._worker_thread is None


class TestStateMachine:
    """Tests for upload state transitions."""

    def test_initial_state_is_pending(self, uploader: CloudUploader) -> None:
        """Test that newly queued files start in PENDING state."""
        file_path = Path("/data/run_001.csv")
        uploader.queue_upload(file_path)
        assert uploader.get_status(file_path) == UploadStatus.PENDING

    def test_completed_is_terminal(
        self, uploader: CloudUploader, mock_s3_client: MagicMock
    ) -> None:
        """Test that COMPLETED state is terminal (file stays in queue)."""
        file_path = Path("/data/run_001.csv")
        uploader.queue_upload(file_path)
        uploader.start()

        # Wait for completion
        deadline = time.time() + 10
        while time.time() < deadline:
            if uploader.get_status(file_path) == UploadStatus.COMPLETED:
                break
            time.sleep(0.1)

        uploader.stop()

        # File should remain in queue with COMPLETED status
        assert uploader.get_status(file_path) == UploadStatus.COMPLETED

    def test_failed_is_terminal(
        self, mock_s3_client: MagicMock
    ) -> None:
        """Test that FAILED state is terminal."""
        mock_s3_client.upload_file.side_effect = Exception("Error")

        config = CloudConfig(
            endpoint_url="http://localhost:9000",
            bucket_name="test-bucket",
            access_key="key",
            secret_key="secret",
            max_retries=1,
            retry_interval_seconds=0,
            max_queue_size=100,
        )
        uploader = CloudUploader(config=config, s3_client=mock_s3_client)

        file_path = Path("/data/run_001.csv")
        uploader.queue_upload(file_path)
        uploader.start()

        # Wait for failure
        deadline = time.time() + 10
        while time.time() < deadline:
            if uploader.get_status(file_path) == UploadStatus.FAILED:
                break
            time.sleep(0.1)

        uploader.stop()

        assert uploader.get_status(file_path) == UploadStatus.FAILED

    def test_cancel_completed_upload_no_effect(
        self, uploader: CloudUploader, mock_s3_client: MagicMock
    ) -> None:
        """Test that cancelling a completed upload has no effect."""
        file_path = Path("/data/run_001.csv")
        uploader.queue_upload(file_path)
        uploader.start()

        # Wait for completion
        deadline = time.time() + 10
        while time.time() < deadline:
            if uploader.get_status(file_path) == UploadStatus.COMPLETED:
                break
            time.sleep(0.1)

        uploader.stop()

        # Cancel should not remove completed uploads
        uploader.cancel(file_path)
        assert uploader.get_status(file_path) == UploadStatus.COMPLETED


class TestS3KeyBuilding:
    """Tests for S3 key construction."""

    def test_key_with_prefix(self, uploader: CloudUploader) -> None:
        """Test S3 key includes destination prefix."""
        key = uploader._build_s3_key(Path("/data/run_001.csv"))
        assert key == "dyno-data/run_001.csv"

    def test_key_without_prefix(
        self, mock_s3_client: MagicMock
    ) -> None:
        """Test S3 key without prefix is just the filename."""
        config = CloudConfig(
            endpoint_url="http://localhost:9000",
            bucket_name="test-bucket",
            access_key="key",
            secret_key="secret",
            destination_prefix="",
        )
        uploader = CloudUploader(config=config, s3_client=mock_s3_client)
        key = uploader._build_s3_key(Path("/data/run_001.csv"))
        assert key == "run_001.csv"

    def test_key_with_trailing_slash_prefix(
        self, mock_s3_client: MagicMock
    ) -> None:
        """Test S3 key strips trailing slashes from prefix."""
        config = CloudConfig(
            endpoint_url="http://localhost:9000",
            bucket_name="test-bucket",
            access_key="key",
            secret_key="secret",
            destination_prefix="data/uploads/",
        )
        uploader = CloudUploader(config=config, s3_client=mock_s3_client)
        key = uploader._build_s3_key(Path("/local/run_001.csv"))
        assert key == "data/uploads/run_001.csv"
