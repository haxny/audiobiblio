"""
tasks — In-memory tracker for long-running background operations.
"""
from __future__ import annotations
import threading
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable
import structlog

from .sse import event_bus

log = structlog.get_logger()


class TaskStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


@dataclass
class BackgroundTask:
    id: str
    name: str
    status: TaskStatus = TaskStatus.PENDING
    result: Any = None
    error: str | None = None
    started_at: float | None = None
    finished_at: float | None = None
    progress: dict[str, Any] = field(default_factory=dict)


class TaskTracker:
    """Track background tasks and publish SSE events on completion."""

    def __init__(self, max_history: int = 100):
        self._tasks: dict[str, BackgroundTask] = {}
        self._lock = threading.Lock()
        self._max_history = max_history

    def submit(self, name: str, fn: Callable, *args, **kwargs) -> str:
        task_id = uuid.uuid4().hex[:12]
        task = BackgroundTask(id=task_id, name=name)
        with self._lock:
            self._tasks[task_id] = task
            self._trim()

        def _run():
            task.status = TaskStatus.RUNNING
            task.started_at = time.time()
            try:
                task.result = fn(*args, **kwargs)
                task.status = TaskStatus.COMPLETED
                event_bus.publish_sync(f"{name}_completed", {"task_id": task_id, "result": str(task.result)})
            except Exception as e:
                task.status = TaskStatus.FAILED
                task.error = str(e)
                log.error("background_task_failed", task_id=task_id, name=name, error=str(e))
                event_bus.publish_sync(f"{name}_failed", {"task_id": task_id, "error": str(e)})
            finally:
                task.finished_at = time.time()

        t = threading.Thread(target=_run, daemon=True)
        t.start()
        return task_id

    def get(self, task_id: str) -> BackgroundTask | None:
        return self._tasks.get(task_id)

    def list_tasks(self) -> list[BackgroundTask]:
        return list(self._tasks.values())

    def _trim(self):
        if len(self._tasks) > self._max_history:
            # Remove oldest completed/failed tasks
            completed = sorted(
                [t for t in self._tasks.values() if t.status in (TaskStatus.COMPLETED, TaskStatus.FAILED)],
                key=lambda t: t.finished_at or 0,
            )
            for t in completed[:len(self._tasks) - self._max_history]:
                del self._tasks[t.id]


# Singleton
task_tracker = TaskTracker()
