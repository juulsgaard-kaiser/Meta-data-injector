"""
Apex Meta-Injector — Core Injection Engine.

Orchestrates the batch metadata injection pipeline:
1. File discovery and container detection
2. Handler routing based on codec manifest
3. Stage-Verify-Commit atomic transaction workflow
4. Error classification and retry logic
5. Progress reporting via thread pool
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any

from apex_injector.codec_manifest import (
    detect_container,
    find_mapping,
)
from apex_injector.config import get_config
from apex_injector.handlers import (
    FileAnalysis,
    InjectionResult,
    InjectionStatus,
    MetadataPayload,
    get_handler,
)
from apex_injector.handlers.exiftool_bridge import get_exiftool_bridge
from apex_injector.manifest import Manifest
from apex_injector.thread_pool import InjectionPool, PoolProgress, TaskResult
from apex_injector.win32_io import (
    FileInUseError,
    StagedTransaction,
    VerificationError,
    Win32IO,
)

logger = logging.getLogger(__name__)


class ErrorClass(StrEnum):
    """Classification of injection errors."""

    FILE_IN_USE = "file_in_use"
    HEADER_CORRUPT = "header_corrupt"
    COMPLEX_WRAP = "complex_wrap"
    PERMISSION_DENIED = "permission_denied"
    UNSUPPORTED_CODEC = "unsupported_codec"
    FILE_NOT_FOUND = "file_not_found"
    HANDLER_ERROR = "handler_error"
    VERIFICATION_FAILED = "verification_failed"


@dataclass
class BatchResult:
    """Result of a complete batch injection operation."""

    total_files: int = 0
    succeeded: int = 0
    failed: int = 0
    skipped: int = 0
    complex_wrap_pending: int = 0
    elapsed_ms: float = 0.0
    file_results: list[InjectionResult] = field(default_factory=list)
    errors: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "total_files": self.total_files,
            "succeeded": self.succeeded,
            "failed": self.failed,
            "skipped": self.skipped,
            "complex_wrap_pending": self.complex_wrap_pending,
            "elapsed_ms": round(self.elapsed_ms, 1),
            "errors": self.errors,
        }


class InjectionEngine:
    """
    Core engine for batch metadata injection.

    Coordinates file analysis, handler selection, and the
    Stage-Verify-Commit transaction workflow. Supports both
    single-file and batch operations.
    """

    def __init__(
        self,
        on_progress: Callable[[PoolProgress], None] | None = None,
        on_file_complete: Callable[[InjectionResult], None] | None = None,
        on_complex_wrap: Callable[[Path, str], bool] | None = None,
    ):
        """
        Initialize the injection engine.

        Args:
            on_progress: Callback for batch progress updates
            on_file_complete: Callback when a single file completes
            on_complex_wrap: Callback to request permission for Complex Wrap.
                             Should return True to proceed, False to skip.
        """
        self._on_progress = on_progress
        self._on_file_complete = on_file_complete
        self._on_complex_wrap = on_complex_wrap
        self._config = get_config()
        self._pool: InjectionPool | None = None
        self._cancel_requested = threading.Event()

        # Import all handlers to trigger auto-registration
        self._ensure_handlers_loaded()

    def _ensure_handlers_loaded(self):
        """Ensure all container handlers are imported and registered."""
        try:
            import apex_injector.handlers.aiff_handler  # noqa: F401
            import apex_injector.handlers.bwf_handler  # noqa: F401
            import apex_injector.handlers.isobmff_handler  # noqa: F401
            import apex_injector.handlers.matroska_handler  # noqa: F401
            import apex_injector.handlers.mxf_handler  # noqa: F401
        except ImportError as e:
            logger.warning("Some handlers could not be loaded: %s", e)

    def analyze_file(self, file_path: Path) -> FileAnalysis:
        """
        Analyze a single file for container type, codec, and metadata.
        """
        file_path = Path(file_path)
        analysis = FileAnalysis(file_path=file_path)

        if not file_path.exists():
            analysis.error = "File not found"
            return analysis

        analysis.file_size = file_path.stat().st_size

        # Detect container
        container = detect_container(file_path)
        if not container:
            analysis.error = f"Unsupported file type: {file_path.suffix}"
            return analysis

        analysis.container = container

        # Find handler
        handler = get_handler(container)
        if handler:
            analysis = handler.analyze(file_path)
            analysis.mapping = find_mapping(container, analysis.codec_fourcc)
            return analysis

        # Fallback to ExifTool bridge
        bridge = get_exiftool_bridge()
        return bridge.analyze(file_path)

    def scan_directory(
        self,
        directory: Path,
        recursive: bool = True,
        glob_pattern: str = "*",
    ) -> list[FileAnalysis]:
        """
        Scan a directory and analyze all supported media files.
        """
        directory = Path(directory)
        if not directory.is_dir():
            raise ValueError(f"Not a directory: {directory}")
        results = []
        from apex_injector.codec_manifest import EXTENSION_MAP

        pattern = f"**/{glob_pattern}" if recursive else glob_pattern
        for file_path in sorted(directory.glob(pattern)):
            if file_path.is_file() and file_path.suffix.lower() in EXTENSION_MAP:
                analysis = self.analyze_file(file_path)
                results.append(analysis)

        return results

    def inject_single(
        self,
        file_path: Path,
        payload: MetadataPayload,
    ) -> InjectionResult:
        """
        Inject metadata into a single file using Stage-Verify-Commit.
        """
        file_path = Path(file_path).resolve()
        result = InjectionResult(file_path=file_path, status=InjectionStatus.FAILED)
        start = time.perf_counter()

        # Pre-flight checks
        if not file_path.is_file():
            result.message = "File not found"
            result.details["error_class"] = ErrorClass.FILE_NOT_FOUND.value
            return result

        if Win32IO.is_file_locked(file_path):
            result.message = "File is in use by another process"
            result.details["error_class"] = ErrorClass.FILE_IN_USE.value
            return result

        if not Win32IO.check_write_access(file_path):
            result.message = "Insufficient write permissions"
            result.details["error_class"] = ErrorClass.PERMISSION_DENIED.value
            return result

        # Detect container and find handler
        container = detect_container(file_path)
        if not container:
            result.message = f"Unsupported file type: {file_path.suffix}"
            result.details["error_class"] = ErrorClass.UNSUPPORTED_CODEC.value
            return result

        handler = get_handler(container)
        if not handler:
            # Fall back to ExifTool
            handler = get_exiftool_bridge()

        # Route the whole payload; unsupported fields never disappear silently.
        container_handler = handler
        if payload.fields and any(not handler.supports_schema(f.schema) for f in payload.fields):
            bridge = get_exiftool_bridge()
            if (
                container in bridge.supported_containers
                and self._config.tools.exiftool
                and all(bridge.supports_schema(f.schema) for f in payload.fields)
            ):
                handler = bridge
            else:
                result.status = InjectionStatus.UNSUPPORTED_FIELD
                result.fields_failed = len(payload.fields)
                result.message = "Unsupported schema or missing ExifTool; no changes made"
                return result
        if not payload.fields:
            result.message = "No metadata supplied"
            return result

        from apex_injector.risk import assess_risks

        risks = assess_risks(payload)
        if risks["has_read_only"] or (risks["requires_acknowledgement"] and not payload.acknowledge_risk):
            result.status = InjectionStatus.UNSUPPORTED_FIELD
            result.fields_failed = len(payload.fields)
            result.details["risks"] = risks
            result.message = (
                "Read-only tags cannot be changed"
                if risks["has_read_only"]
                else "Higher-risk metadata edit requires acknowledgement; preview the affected tags first"
            )
            return result

        try:
            with StagedTransaction(
                file_path,
                backup=self._config.engine.create_backups,
                verify_essence=self._config.engine.verify_bitstream,
                hash_algorithm=self._config.engine.hash_algorithm,
                fingerprint=handler.essence_fingerprint,
                validate=container_handler.validate,
                backup_suffix=self._config.engine.backup_suffix,
                staging_suffix=self._config.engine.staging_suffix,
            ) as txn:
                result = handler.inject(file_path, payload, txn.staging_path)
                if result.status == InjectionStatus.COMPLEX_WRAP_REQUIRED:
                    if self._on_complex_wrap and self._on_complex_wrap(file_path, result.complex_wrap_info or ""):
                        result = handler.execute_complex_wrap(file_path, payload, txn.staging_path)
                    else:
                        txn.rollback()
                if result.status != InjectionStatus.SUCCESS or result.fields_written != len(payload.fields):
                    txn.rollback()
                    if result.status == InjectionStatus.SUCCESS:
                        result.status = InjectionStatus.FAILED
                        result.message = "Writer did not confirm every requested field"
                    # No partial commits: a failed request leaves its source intact.
                    result.fields_written = 0
                    result.fields_failed = len(payload.fields)
            if result.status == InjectionStatus.SUCCESS:
                result.backup_path = txn.backup_path
        except Exception as e:
            result.status = InjectionStatus.FAILED
            result.fields_written = 0
            result.fields_failed = len(payload.fields)
            result.message = str(e)
            error_class = (
                ErrorClass.FILE_IN_USE
                if isinstance(e, FileInUseError)
                else ErrorClass.VERIFICATION_FAILED
                if isinstance(e, VerificationError)
                else ErrorClass.HANDLER_ERROR
            )
            result.details["error_class"] = error_class.value
            logger.warning("Injection failed for %s: %s", file_path, e)
        result.duration_ms = (time.perf_counter() - start) * 1000
        return result

    def inject_with_retry(
        self,
        file_path: Path,
        payload: MetadataPayload,
    ) -> InjectionResult:
        """
        Inject with retry logic for FileInUse errors.
        """
        config = self._config.engine
        last_result = None

        for attempt in range(config.file_in_use_retries + 1):
            result = self.inject_single(file_path, payload)

            if result.details.get("error_class") != ErrorClass.FILE_IN_USE.value:
                return result

            last_result = result

            if attempt < config.file_in_use_retries:
                wait = config.file_in_use_backoff_base * (2**attempt)
                logger.info(
                    "File in use, retrying in %.1fs (attempt %d/%d): %s",
                    wait,
                    attempt + 1,
                    config.file_in_use_retries,
                    file_path,
                )
                time.sleep(wait)

        return last_result

    def run_batch(self, manifest: Manifest) -> BatchResult:
        """
        Run a batch injection from a manifest.

        Uses the thread pool for concurrent processing.
        """
        start = time.perf_counter()
        batch = BatchResult(total_files=manifest.file_count)

        if not manifest.entries:
            return batch

        # Duplicate paths otherwise race and make field precedence nondeterministic.
        canonical = [entry.file_path.resolve() for entry in manifest.entries]
        if len(set(canonical)) != len(canonical):
            batch.failed = batch.total_files
            batch.errors.append(
                {"error": "Duplicate file paths in manifest; no files processed", "class": "duplicate_file"}
            )
            return batch
        tasks = []
        for i, entry in enumerate(manifest.entries):
            task_id = f"inject_{i}_{entry.file_path.name}"
            tasks.append(
                (
                    task_id,
                    self.inject_with_retry,
                    (entry.file_path, entry.metadata),
                    {},
                )
            )

        # Run through thread pool
        paths = {task[0]: entry.file_path for task, entry in zip(tasks, manifest.entries)}

        def on_task_done(task_result: TaskResult):
            if task_result.status.value == "cancelled":
                batch.skipped += 1
                return
            if task_result.result is None:
                task_result.result = InjectionResult(
                    file_path=paths[task_result.task_id],
                    status=InjectionStatus.FAILED,
                    message=task_result.error or "Worker failed",
                    details={"error_class": "handler_error"},
                )
            if task_result.result:
                inj_result = task_result.result
                batch.file_results.append(inj_result)

                if inj_result.status == InjectionStatus.SUCCESS:
                    batch.succeeded += 1
                elif inj_result.status == InjectionStatus.COMPLEX_WRAP_REQUIRED:
                    batch.complex_wrap_pending += 1
                else:
                    batch.failed += 1
                    batch.errors.append(
                        {
                            "file": str(inj_result.file_path),
                            "error": inj_result.message,
                            "class": inj_result.details.get("error_class", "unknown"),
                        }
                    )

                if self._on_file_complete:
                    self._on_file_complete(inj_result)

        self._pool = InjectionPool(
            on_progress=self._on_progress,
            on_task_complete=on_task_done,
        )

        if self._cancel_requested.is_set():
            self._pool.cancel()
        self._pool.submit_batch(tasks)

        batch.elapsed_ms = (time.perf_counter() - start) * 1000
        batch.skipped = batch.total_files - batch.succeeded - batch.failed - batch.complex_wrap_pending

        logger.info(
            "Batch complete: %d succeeded, %d failed, %d pending, %.1fs",
            batch.succeeded,
            batch.failed,
            batch.complex_wrap_pending,
            batch.elapsed_ms / 1000,
        )

        return batch

    def cancel_batch(self):
        """Cancel the current batch operation."""
        self._cancel_requested.set()
        if self._pool:
            self._pool.cancel()
