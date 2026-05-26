"""Cloud uploader with queue management, retry logic, and state machine.

Manages file upload queue with S3-compatible storage via boto3.
Implements retry logic with configurable intervals and maximum attempts.
"""

import logging
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Callable, Optional, Protocol

from rotax_dyno_daq.core.enums import UploadStatus
from rotax_dyno_daq.core.models import CloudConfig, UploadTask

logger = logging.getLogger(__name__)


class S3ClientProtocol(Protocol):
    """Protocol for S3 client to enable dependency injection and testing."""

    def upload_file(self, file_path: str, bucket: str, key: str) -> None:
        """Upload a file to S3-compatible storage."""
        ...


class UploadQueueFullError(Exception):
    """Raised when the upload queue is at maximum capacity."""

    pass


class FileNotInQueueError(Exception):
    """Raised when a file is not found in the upload queue."""

    pass


class CloudUploader:
    """Queues and uploads CSV files to cloud storage with retry logic.

    Implements a state machine for upload tasks:
        PENDING → IN_PROGRESS → COMPLETED (on success)
        PENDING → IN_PROGRESS → PENDING (on failure, retries remain)
        PENDING → FAILED (after max retries exhausted)

    COMPLETED and FAILED are terminal states.
    """

    def __init__(
        self,
        config: CloudConfig,
        s3_client: Optional[S3ClientProtocol] = None,
        on_failure_notify: Optional[Callable[[UploadTask], None]] = None,
    ) -> None:
        """Initialize the cloud uploader.

        Args:
            config: Cloud storage configuration.
            s3_client: Injectable S3 client (uses boto3 by default).
            on_failure_notify: Callback invoked when an upload permanently fails.
        """
        self._config = config
        self._s3_client = s3_client or self._create_default_s3_client()
        self._on_failure_notify = on_failure_notify

        self._queue: dict[Path, UploadTask] = {}
        self._lock = threading.Lock()
        self._worker_thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._queue_event = threading.Event()  # Signals new items in queue

    def _create_default_s3_client(self) -> S3ClientProtocol:
        """Create a default boto3 S3 client wrapper."""
        return _Boto3S3Client(
            endpoint_url=self._config.endpoint_url,
            access_key=self._config.access_key,
            secret_key=self._config.secret_key,
        )

    def start(self) -> None:
        """Start the background upload worker thread."""
        if self._worker_thread is not None and self._worker_thread.is_alive():
            return
        self._stop_event.clear()
        self._worker_thread = threading.Thread(
            target=self._upload_worker,
            name="cloud-upload-worker",
            daemon=True,
        )
        self._worker_thread.start()
        logger.info("Cloud upload worker started.")

    def stop(self) -> None:
        """Stop the background upload worker thread."""
        self._stop_event.set()
        self._queue_event.set()  # Wake up the worker if it's waiting
        if self._worker_thread is not None:
            self._worker_thread.join(timeout=10)
            self._worker_thread = None
        logger.info("Cloud upload worker stopped.")

    def queue_upload(self, file_path: Path, run_id: str = "") -> None:
        """Add a file to the upload queue.

        Args:
            file_path: Path to the file to upload.
            run_id: Associated run identifier.

        Raises:
            UploadQueueFullError: If the queue is at maximum capacity (100 files).
        """
        with self._lock:
            if len(self._queue) >= self._config.max_queue_size:
                raise UploadQueueFullError(
                    f"Upload queue is full ({self._config.max_queue_size} files). "
                    "Cannot add more files."
                )

            # If file is already in queue, don't re-add
            if file_path in self._queue:
                logger.warning(f"File already in upload queue: {file_path}")
                return

            task = UploadTask(
                file_path=file_path,
                run_id=run_id,
                status=UploadStatus.PENDING,
                attempts=0,
                last_attempt=None,
                error_message="",
            )
            self._queue[file_path] = task
            logger.info(f"Queued file for upload: {file_path}")

        # Signal the worker that there's work to do
        self._queue_event.set()

    def get_status(self, file_path: Path) -> UploadStatus:
        """Get the upload status of a queued file.

        Args:
            file_path: Path to the file to check.

        Returns:
            The current UploadStatus for the file.

        Raises:
            FileNotInQueueError: If the file is not in the upload queue.
        """
        with self._lock:
            task = self._queue.get(file_path)
            if task is None:
                raise FileNotInQueueError(
                    f"File not found in upload queue: {file_path}"
                )
            return task.status

    def get_task(self, file_path: Path) -> Optional[UploadTask]:
        """Get the full upload task for a queued file.

        Args:
            file_path: Path to the file to check.

        Returns:
            The UploadTask, or None if not in queue.
        """
        with self._lock:
            return self._queue.get(file_path)

    def cancel(self, file_path: Path) -> None:
        """Cancel a pending or in-progress upload and remove from queue.

        Args:
            file_path: Path to the file to cancel.

        Raises:
            FileNotInQueueError: If the file is not in the upload queue.
        """
        with self._lock:
            task = self._queue.get(file_path)
            if task is None:
                raise FileNotInQueueError(
                    f"File not found in upload queue: {file_path}"
                )

            if task.status in (UploadStatus.COMPLETED, UploadStatus.FAILED):
                logger.warning(
                    f"Cannot cancel upload in terminal state '{task.status.value}': "
                    f"{file_path}"
                )
                return

            del self._queue[file_path]
            logger.info(f"Cancelled upload for: {file_path}")

    @property
    def queue_size(self) -> int:
        """Return the current number of items in the upload queue."""
        with self._lock:
            return len(self._queue)

    @property
    def pending_count(self) -> int:
        """Return the number of pending uploads."""
        with self._lock:
            return sum(
                1 for t in self._queue.values() if t.status == UploadStatus.PENDING
            )

    def _upload_worker(self) -> None:
        """Background worker that processes the upload queue."""
        logger.info("Upload worker thread running.")

        while not self._stop_event.is_set():
            task = self._get_next_pending_task()

            if task is None:
                # Wait for new items or stop signal, check every 5 seconds
                self._queue_event.wait(timeout=5.0)
                self._queue_event.clear()
                continue

            # Check if enough time has passed since last attempt (retry interval)
            if task.last_attempt is not None:
                elapsed = (datetime.now() - task.last_attempt).total_seconds()
                if elapsed < self._config.retry_interval_seconds:
                    # Not ready for retry yet, sleep briefly and continue
                    time.sleep(1.0)
                    continue

            self._attempt_upload(task)

    def _get_next_pending_task(self) -> Optional[UploadTask]:
        """Get the next task that is ready for upload attempt."""
        with self._lock:
            for task in self._queue.values():
                if task.status == UploadStatus.PENDING:
                    # Check retry interval
                    if task.last_attempt is not None:
                        elapsed = (
                            datetime.now() - task.last_attempt
                        ).total_seconds()
                        if elapsed < self._config.retry_interval_seconds:
                            continue
                    return task
        return None

    def _attempt_upload(self, task: UploadTask) -> None:
        """Attempt to upload a single file."""
        # Transition to IN_PROGRESS
        with self._lock:
            task.status = UploadStatus.IN_PROGRESS
            task.attempts += 1
            task.last_attempt = datetime.now()

        logger.info(
            f"Uploading {task.file_path} (attempt {task.attempts}/"
            f"{self._config.max_retries})..."
        )

        # Build the S3 key
        key = self._build_s3_key(task.file_path)

        # Attempt upload with timeout
        success = False
        error_msg = ""

        try:
            success = self._upload_with_timeout(
                file_path=str(task.file_path),
                bucket=self._config.bucket_name,
                key=key,
                timeout_seconds=self._config.upload_timeout_seconds,
            )
        except Exception as e:
            error_msg = str(e)
            logger.warning(
                f"Upload failed for {task.file_path}: {error_msg}"
            )

        with self._lock:
            if success:
                task.status = UploadStatus.COMPLETED
                task.error_message = ""
                logger.info(f"Upload completed: {task.file_path}")
            else:
                if not error_msg:
                    error_msg = "Upload timed out or was cancelled"
                task.error_message = error_msg

                if task.attempts >= self._config.max_retries:
                    # Max retries exhausted
                    task.status = UploadStatus.FAILED
                    logger.error(
                        f"Upload permanently failed after {task.attempts} "
                        f"attempts: {task.file_path}"
                    )
                    if self._on_failure_notify:
                        self._on_failure_notify(task)
                else:
                    # Return to PENDING for retry
                    task.status = UploadStatus.PENDING
                    logger.info(
                        f"Upload will retry ({task.attempts}/"
                        f"{self._config.max_retries}): {task.file_path}"
                    )

    def _upload_with_timeout(
        self,
        file_path: str,
        bucket: str,
        key: str,
        timeout_seconds: int,
    ) -> bool:
        """Upload a file with a timeout. Returns True on success.

        Args:
            file_path: Local file path to upload.
            bucket: S3 bucket name.
            key: S3 object key.
            timeout_seconds: Maximum time allowed for the upload.

        Returns:
            True if upload succeeded, False if timed out.
        """
        result: dict[str, object] = {"success": False, "error": None}

        def do_upload() -> None:
            try:
                self._s3_client.upload_file(file_path, bucket, key)
                result["success"] = True
            except Exception as e:
                result["error"] = e

        upload_thread = threading.Thread(target=do_upload, daemon=True)
        upload_thread.start()
        upload_thread.join(timeout=timeout_seconds)

        if upload_thread.is_alive():
            # Upload timed out
            logger.warning(
                f"Upload timed out after {timeout_seconds}s: {file_path}"
            )
            return False

        if result["error"] is not None:
            raise result["error"]  # type: ignore[misc]

        return bool(result["success"])

    def _build_s3_key(self, file_path: Path) -> str:
        """Build the S3 object key from the file path and config prefix."""
        prefix = self._config.destination_prefix.strip("/")
        filename = file_path.name
        if prefix:
            return f"{prefix}/{filename}"
        return filename


class _Boto3S3Client:
    """Default S3 client implementation using boto3."""

    def __init__(
        self, endpoint_url: str, access_key: str, secret_key: str
    ) -> None:
        import boto3

        self._client = boto3.client(
            "s3",
            endpoint_url=endpoint_url,
            aws_access_key_id=access_key,
            aws_secret_access_key=secret_key,
        )

    def upload_file(self, file_path: str, bucket: str, key: str) -> None:
        """Upload a file to S3."""
        self._client.upload_file(file_path, bucket, key)
