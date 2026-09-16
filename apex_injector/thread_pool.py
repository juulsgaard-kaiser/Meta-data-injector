"""
Apex Meta-Injector — Windows-optimized Thread Pool.

Wraps concurrent.futures.ThreadPoolExecutor with:
- Conservative automatic worker count
- Progress reporting callbacks
- Graceful shutdown with in-flight operation completion
- Error collection and classification
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor, as_completed
from dataclasses import dataclass, replace
from enum import StrEnum
from typing import Any

from apex_injector.config import get_config

logger = logging.getLogger(__name__)


class TaskStatus(StrEnum):
    """Status of a pool task."""

    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass
class TaskResult:
    """Result of a single pool task."""

    task_id: str
    status: TaskStatus
    result: Any = None
    error: str | None = None
    duration_ms: float = 0.0


@dataclass
class PoolProgress:
    """Real-time progress of the thread pool."""

    total: int = 0
    queued: int = 0
    running: int = 0
    completed: int = 0
    failed: int = 0
    cancelled: int = 0
    elapsed_ms: float = 0.0
    eta_ms: float = 0.0

    @property
    def percent(self) -> float:
        if self.total == 0:
            return 0.0
        return (self.completed + self.failed + self.cancelled) / self.total * 100

    def to_dict(self) -> dict:
        return {
            "total": self.total,
            "queued": self.queued,
            "running": self.running,
            "completed": self.completed,
            "failed": self.failed,
            "cancelled": self.cancelled,
            "percent": round(self.percent, 1),
            "elapsed_ms": round(self.elapsed_ms, 1),
            "eta_ms": round(self.eta_ms, 1),
        }


class InjectionPool:
    """
    Windows-optimized thread pool for batch metadata injection.

    Runs I/O-bound tasks with configurable
    worker counts and real-time progress reporting.
    """

    def __init__(
        self,
        max_workers: int | None = None,
        on_progress: Callable[[PoolProgress], None] | None = None,
        on_task_complete: Callable[[TaskResult], None] | None = None,
    ):
        config = get_config()
        self._max_workers = max_workers or config.engine.get_worker_count()
        self._on_progress = on_progress
        self._on_task_complete = on_task_complete
        self._executor: ThreadPoolExecutor | None = None
        self._futures: dict[Future, str] = {}
        self._progress = PoolProgress()
        self._results: list[TaskResult] = []
        self._start_time: float = 0.0
        self._cancelled = False
        self._lock = threading.RLock()

        logger.info("Thread pool configured with %d workers", self._max_workers)

    @property
    def progress(self) -> PoolProgress:
        return self._progress

    @property
    def results(self) -> list[TaskResult]:
        return list(self._results)

    @property
    def is_running(self) -> bool:
        return self._executor is not None and not self._cancelled

    def submit_batch(
        self,
        tasks: list[tuple[str, Callable, tuple, dict]],
    ) -> list[TaskResult]:
        """
        Submit a batch of tasks and wait for all to complete.

        Each task is a tuple of (task_id, callable, args, kwargs).
        Returns list of TaskResults.
        """
        with self._lock:
            if self._executor is not None:
                raise RuntimeError("This pool is already running")
            self._start_time = time.perf_counter()
            self._progress = PoolProgress(total=len(tasks), queued=len(tasks))
            self._results = []
            self._executor = ThreadPoolExecutor(max_workers=self._max_workers, thread_name_prefix="apex-inject")
        try:
            for task_id, fn, args, kwargs in tasks:
                with self._lock:
                    future = self._executor.submit(self._wrapped_call, task_id, fn, args, kwargs)
                    self._futures[future] = task_id
                    if self._cancelled:
                        future.cancel()
            for future in as_completed(list(self._futures)):
                task_id = self._futures[future]
                if future.cancelled():
                    task_result = TaskResult(task_id, TaskStatus.CANCELLED)
                    with self._lock:
                        self._progress.queued -= 1
                else:
                    try:
                        task_result = future.result()
                    except Exception as e:
                        task_result = TaskResult(task_id, TaskStatus.FAILED, error=str(e))
                with self._lock:
                    self._results.append(task_result)
                    if task_result.status == TaskStatus.CANCELLED:
                        self._progress.cancelled += 1
                    elif task_result.status == TaskStatus.FAILED:
                        self._progress.failed += 1
                    else:
                        self._progress.completed += 1
                if self._on_task_complete:
                    self._on_task_complete(task_result)
                self._emit_progress()
        finally:
            self._executor.shutdown(wait=True)
            with self._lock:
                self._executor = None
                self._futures.clear()
        return self.results

    def cancel(self):
        with self._lock:
            self._cancelled = True
            for future in self._futures:
                future.cancel()

    def _wrapped_call(self, task_id, fn, args, kwargs):
        start = time.perf_counter()
        with self._lock:
            self._progress.queued -= 1
            if self._cancelled:
                return TaskResult(task_id, TaskStatus.CANCELLED)
            self._progress.running += 1
        self._emit_progress()
        try:
            result = fn(*args, **kwargs)
            status = getattr(result, "status", None)
            failed = status is not None and getattr(status, "value", status) not in ("success", "complex_wrap_required")
            return TaskResult(
                task_id,
                TaskStatus.FAILED if failed else TaskStatus.COMPLETED,
                result=result,
                duration_ms=(time.perf_counter() - start) * 1000,
            )
        except Exception as e:
            return TaskResult(
                task_id, TaskStatus.FAILED, error=str(e), duration_ms=(time.perf_counter() - start) * 1000
            )
        finally:
            with self._lock:
                self._progress.running -= 1

    def _emit_progress(self):
        """Emit progress update."""
        with self._lock:
            self._progress.elapsed_ms = (time.perf_counter() - self._start_time) * 1000
            done = self._progress.completed + self._progress.failed + self._progress.cancelled
            self._progress.eta_ms = self._progress.elapsed_ms / done * (self._progress.total - done) if done else 0
            snapshot = replace(self._progress)
        if self._on_progress:
            try:
                self._on_progress(snapshot)
            except Exception:
                logger.exception("Progress callback failed")
