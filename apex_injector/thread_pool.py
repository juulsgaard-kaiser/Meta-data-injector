"""
Apex Meta-Injector — Windows-optimized Thread Pool.

Wraps concurrent.futures.ThreadPoolExecutor with:
- Auto-tuned worker count for NVMe storage
- Progress reporting callbacks
- Graceful shutdown with in-flight operation completion
- Error collection and classification
"""

from __future__ import annotations

import logging
import os
import time
from concurrent.futures import Future, ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Optional

from apex_injector.config import get_config

logger = logging.getLogger(__name__)


class TaskStatus(str, Enum):
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
    error: Optional[str] = None
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
        return (self.completed + self.failed) / self.total * 100

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

    Optimized for I/O-bound tasks on NVMe storage with configurable
    worker counts and real-time progress reporting.
    """

    def __init__(
        self,
        max_workers: Optional[int] = None,
        on_progress: Optional[Callable[[PoolProgress], None]] = None,
        on_task_complete: Optional[Callable[[TaskResult], None]] = None,
    ):
        config = get_config()
        self._max_workers = max_workers or config.engine.get_worker_count()
        self._on_progress = on_progress
        self._on_task_complete = on_task_complete
        self._executor: Optional[ThreadPoolExecutor] = None
        self._futures: dict[Future, str] = {}
        self._progress = PoolProgress()
        self._results: list[TaskResult] = []
        self._start_time: float = 0.0
        self._cancelled = False

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
        self._start_time = time.perf_counter()
        self._progress = PoolProgress(total=len(tasks))
        self._results = []
        self._cancelled = False

        self._executor = ThreadPoolExecutor(
            max_workers=self._max_workers,
            thread_name_prefix="apex-inject",
        )

        try:
            for task_id, fn, args, kwargs in tasks:
                if self._cancelled:
                    break

                future = self._executor.submit(self._wrapped_call, task_id, fn, args, kwargs)
                self._futures[future] = task_id
                self._progress.queued += 1
                self._emit_progress()

            # Wait for completion
            for future in as_completed(self._futures):
                if self._cancelled:
                    break

                task_id = self._futures[future]
                try:
                    task_result = future.result()
                    self._results.append(task_result)

                    if task_result.status == TaskStatus.COMPLETED:
                        self._progress.completed += 1
                    else:
                        self._progress.failed += 1

                except Exception as e:
                    result = TaskResult(
                        task_id=task_id,
                        status=TaskStatus.FAILED,
                        error=str(e),
                    )
                    self._results.append(result)
                    self._progress.failed += 1

                self._progress.queued = max(0, self._progress.queued - 1)
                self._emit_progress()

                if self._on_task_complete:
                    self._on_task_complete(self._results[-1])

        finally:
            self._executor.shutdown(wait=True)
            self._executor = None
            self._futures.clear()

        return self._results

    def cancel(self):
        """Cancel all pending tasks. In-flight tasks will complete."""
        self._cancelled = True
        if self._executor:
            for future in list(self._futures.keys()):
                if not future.done():
                    future.cancel()
                    self._progress.cancelled += 1

    def _wrapped_call(
        self,
        task_id: str,
        fn: Callable,
        args: tuple,
        kwargs: dict,
    ) -> TaskResult:
        """Wrap a callable with timing and error handling."""
        start = time.perf_counter()
        self._progress.running += 1
        self._emit_progress()

        try:
            result = fn(*args, **kwargs)
            duration = (time.perf_counter() - start) * 1000

            return TaskResult(
                task_id=task_id,
                status=TaskStatus.COMPLETED,
                result=result,
                duration_ms=duration,
            )
        except Exception as e:
            duration = (time.perf_counter() - start) * 1000
            logger.error("Task %s failed: %s", task_id, e)

            return TaskResult(
                task_id=task_id,
                status=TaskStatus.FAILED,
                error=str(e),
                duration_ms=duration,
            )
        finally:
            self._progress.running = max(0, self._progress.running - 1)

    def _emit_progress(self):
        """Emit progress update."""
        self._progress.elapsed_ms = (time.perf_counter() - self._start_time) * 1000

        # ETA calculation
        done = self._progress.completed + self._progress.failed
        if done > 0 and self._progress.elapsed_ms > 0:
            avg_per_task = self._progress.elapsed_ms / done
            remaining = self._progress.total - done
            self._progress.eta_ms = avg_per_task * remaining
        else:
            self._progress.eta_ms = 0

        if self._on_progress:
            try:
                self._on_progress(self._progress)
            except Exception as e:
                logger.debug("Progress callback error: %s", e)
