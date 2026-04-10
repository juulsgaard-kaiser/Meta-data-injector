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
import time
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Optional

from apex_injector.codec_manifest import (
    ContainerFormat,
    detect_container,
    find_mapping,
)
from apex_injector.config import get_config
from apex_injector.handlers import (
    ContainerHandler,
    FileAnalysis,
    InjectionResult,
    InjectionStatus,
    MetadataPayload,
    get_handler,
)
from apex_injector.handlers.exiftool_bridge import get_exiftool_bridge
from apex_injector.manifest import Manifest, ManifestEntry
from apex_injector.thread_pool import InjectionPool, PoolProgress, TaskResult
from apex_injector.win32_io import (
    FileInUseError,
    StagedTransaction,
    Win32IO,
    compute_file_hash,
)

logger = logging.getLogger(__name__)


class ErrorClass(str, Enum):
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
        on_progress: Optional[Callable[[PoolProgress], None]] = None,
        on_file_complete: Optional[Callable[[InjectionResult], None]] = None,
        on_complex_wrap: Optional[Callable[[Path, str], bool]] = None,
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
        self._pool: Optional[InjectionPool] = None

        # Import all handlers to trigger auto-registration
        self._ensure_handlers_loaded()

    def _ensure_handlers_loaded(self):
        """Ensure all container handlers are imported and registered."""
        try:
            import apex_injector.handlers.isobmff_handler  # noqa: F401
            import apex_injector.handlers.mxf_handler  # noqa: F401
            import apex_injector.handlers.matroska_handler  # noqa: F401
            import apex_injector.handlers.bwf_handler  # noqa: F401
            import apex_injector.handlers.aiff_handler  # noqa: F401
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
            return handler.analyze(file_path)

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
        file_path = Path(file_path)
        result = InjectionResult(file_path=file_path, status=InjectionStatus.FAILED)
        start = time.perf_counter()

        # Pre-flight checks
        if not file_path.exists():
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

        # Filter payload to supported schemas
        filtered = handler.filter_payload(payload)
        if not filtered.fields:
            # Try ExifTool bridge for unsupported schemas
            bridge = get_exiftool_bridge()
            filtered = bridge.filter_payload(payload)
            if filtered.fields:
                handler = bridge
            else:
                result.message = "No compatible metadata schemas for this container"
                result.details["error_class"] = ErrorClass.UNSUPPORTED_CODEC.value
                return result

        # Stage-Verify-Commit workflow
        try:
            with StagedTransaction(
                file_path,
                backup=self._config.engine.create_backups,
                verify_essence=self._config.engine.verify_bitstream,
                hash_algorithm=self._config.engine.hash_algorithm,
            ) as txn:
                # Set essence region for verification
                try:
                    offset, length = handler.get_essence_region(file_path)
                    txn.set_essence_region(offset, length)
                except Exception as e:
                    logger.debug("Could not determine essence region: %s", e)

                # STAGE: Inject into staging file
                handler_result = handler.inject(file_path, filtered, txn.staging_path)

                # Handle Complex Wrap
                if handler_result.status == InjectionStatus.COMPLEX_WRAP_REQUIRED:
                    if self._on_complex_wrap:
                        approved = self._on_complex_wrap(
                            file_path,
                            handler_result.complex_wrap_info or "Complex Wrap needed",
                        )
                        if approved:
                            # Execute complex wrap
                            from apex_injector.handlers.mxf_handler import MXFHandler
                            if isinstance(handler, MXFHandler):
                                handler_result = handler.execute_complex_wrap(
                                    file_path, filtered, txn.staging_path,
                                )
                            else:
                                handler_result.message = "Complex Wrap not supported for this handler"
                        else:
                            result.status = InjectionStatus.COMPLEX_WRAP_REQUIRED
                            result.message = "User declined Complex Wrap"
                            result.complex_wrap_info = handler_result.complex_wrap_info
                            txn.rollback()
                            return result
                    else:
                        result.status = InjectionStatus.COMPLEX_WRAP_REQUIRED
                        result.complex_wrap_info = handler_result.complex_wrap_info
                        txn.rollback()
                        return result

                if handler_result.status in (InjectionStatus.SUCCESS, InjectionStatus.PARTIAL):
                    # VERIFY + COMMIT handled by StagedTransaction.__exit__
                    result = handler_result
                    result.backup_path = txn.backup_path
                else:
                    txn.rollback()
                    result = handler_result

        except FileInUseError:
            result.message = "File is locked by another process"
            result.details["error_class"] = ErrorClass.FILE_IN_USE.value
        except RuntimeError as e:
            if "Bitstream verification" in str(e):
                result.message = str(e)
                result.details["error_class"] = ErrorClass.VERIFICATION_FAILED.value
            else:
                raise
        except Exception as e:
            result.message = f"Injection error: {e}"
            result.details["error_class"] = ErrorClass.HANDLER_ERROR.value
            logger.error("Injection failed for %s: %s", file_path, e, exc_info=True)

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
                wait = config.file_in_use_backoff_base * (2 ** attempt)
                logger.info(
                    "File in use, retrying in %.1fs (attempt %d/%d): %s",
                    wait, attempt + 1, config.file_in_use_retries, file_path,
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

        # Build task list
        tasks = []
        for i, entry in enumerate(manifest.entries):
            task_id = f"inject_{i}_{entry.file_path.name}"
            tasks.append((
                task_id,
                self.inject_with_retry,
                (entry.file_path, entry.metadata),
                {},
            ))

        # Run through thread pool
        def on_task_done(task_result: TaskResult):
            if task_result.result:
                inj_result = task_result.result
                batch.file_results.append(inj_result)

                if inj_result.status == InjectionStatus.SUCCESS:
                    batch.succeeded += 1
                elif inj_result.status == InjectionStatus.COMPLEX_WRAP_REQUIRED:
                    batch.complex_wrap_pending += 1
                elif inj_result.status == InjectionStatus.PARTIAL:
                    batch.succeeded += 1  # Partial counts as success
                else:
                    batch.failed += 1
                    batch.errors.append({
                        "file": str(inj_result.file_path),
                        "error": inj_result.message,
                        "class": inj_result.details.get("error_class", "unknown"),
                    })

                if self._on_file_complete:
                    self._on_file_complete(inj_result)

        self._pool = InjectionPool(
            on_progress=self._on_progress,
            on_task_complete=on_task_done,
        )

        self._pool.submit_batch(tasks)

        batch.elapsed_ms = (time.perf_counter() - start) * 1000
        batch.skipped = batch.total_files - batch.succeeded - batch.failed - batch.complex_wrap_pending

        logger.info(
            "Batch complete: %d succeeded, %d failed, %d pending, %.1fs",
            batch.succeeded, batch.failed, batch.complex_wrap_pending,
            batch.elapsed_ms / 1000,
        )

        return batch

    def cancel_batch(self):
        """Cancel the current batch operation."""
        if self._pool:
            self._pool.cancel()
