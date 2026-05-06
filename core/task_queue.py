"""Priority task queue with session affinity (Phase 5.3).

Replaces GrugMessageQueue. Tasks are ordered by (priority, created_at) on a
min-heap. Within a single session_id only one worker runs at a time
(session-affinity lock). When dequeuing an URGENT task we drain all other
URGENT tasks already pending for the same session into one batch so the
worker processes them consecutively without re-acquiring the session lock.

Scope of this file (what's implemented now):
- heap-based priority ordering
- session-affinity locking
- URGENT same-session batching at dequeue time
- cooperative cancellation (remove from heap or signal cancel_event)

Deferred to follow-on work in Phase 5.3:
- per-task max_run_time watchdog
- BACKGROUND off-hours window deferral
- worker-tier semaphore allocation (currently the process_fn is responsible
  for acquiring the agent's worker semaphore)
"""

from __future__ import annotations

import heapq
import threading
from datetime import datetime
from typing import Callable, Optional

from core.task import Task, TaskPriority, TaskState


def make_hour_window_check(start_hour: int, end_hour: int) -> Callable[[], bool]:
    """Return a callable that's True when the local hour is in the window.

    Window is half-open [start_hour, end_hour). When start == end the window
    is considered always-open. When start > end (e.g. 22 → 6) the window
    wraps midnight.
    """
    if start_hour == end_hour:
        return lambda: True
    if start_hour < end_hour:
        return lambda: start_hour <= datetime.now().hour < end_hour
    # Wrap over midnight
    return lambda: datetime.now().hour >= start_hour or datetime.now().hour < end_hour


class TaskQueue:
    """Thread-safe priority queue for Task objects."""

    def __init__(self, process_fn: Callable[[list[Task]], None], worker_count: int = 1,
                 background_runnable: Optional[Callable[[], bool]] = None,
                 background_poll_seconds: float = 60.0):
        self._heap: list[Task] = []
        self._lock = threading.Lock()
        self._not_empty = threading.Condition(self._lock)
        self._process_fn = process_fn
        self._worker_count = worker_count
        self._workers: list[threading.Thread] = []
        # session_id -> Lock; ensures only one worker runs a given session at a time
        self._session_locks: dict[str, threading.Lock] = {}
        # task_id -> Task, for cancellation lookup (only QUEUED/RUNNING tasks)
        self._index: dict[str, Task] = {}
        # Optional gate for BACKGROUND tasks: returns True when they may run.
        # When False, BG tasks are held in the heap and we re-check after the
        # poll interval. URGENT tasks bypass this gate entirely.
        self._background_runnable = background_runnable
        self._background_poll_seconds = background_poll_seconds

    @property
    def worker_count(self) -> int:
        return self._worker_count

    def start(self) -> None:
        for i in range(self._worker_count):
            t = threading.Thread(target=self._worker_loop, name=f"task-worker-{i}", daemon=True)
            t.start()
            self._workers.append(t)

    def enqueue(self, task: Task) -> None:
        if task.state is not TaskState.QUEUED:
            raise ValueError(f"task {task.id} not in QUEUED state, got {task.state}")
        with self._not_empty:
            heapq.heappush(self._heap, task)
            self._index[task.id] = task
            self._not_empty.notify()

    def cancel(self, task_id: str) -> bool:
        """Cancel a task by id. Returns True if state actually changed.

        QUEUED -> CANCELLED (and removed from heap).
        RUNNING -> sets cancel_event; the worker is responsible for transitioning
                   to CANCELLED when its StepLoop exits.
        """
        with self._lock:
            task = self._index.get(task_id)
            if task is None:
                return False
            if task.state is TaskState.QUEUED:
                task.transition(TaskState.CANCELLED)
                self._heap = [t for t in self._heap if t.id != task_id]
                heapq.heapify(self._heap)
                self._index.pop(task_id, None)
                return True
            if task.state is TaskState.RUNNING:
                task.request_cancel()
                return True
            return False

    def pending_count(self) -> int:
        with self._lock:
            return len(self._heap)

    # ------------------------------------------------------------------
    # Worker loop
    # ------------------------------------------------------------------

    def _worker_loop(self) -> None:
        while True:
            batch = self._take_next_batch()
            if not batch:
                continue
            session_id = batch[0].session_id
            session_lock = self._get_session_lock(session_id)
            with session_lock:
                self._run_batch(batch)

    def _get_session_lock(self, session_id: str) -> threading.Lock:
        with self._lock:
            lock = self._session_locks.get(session_id)
            if lock is None:
                lock = threading.Lock()
                self._session_locks[session_id] = lock
            return lock

    def _take_next_batch(self) -> list[Task]:
        """Pop the highest-priority task. If URGENT, also drain any other
        QUEUED URGENT tasks for the same session into the batch.

        If the heap top is BACKGROUND and the background gate is closed, the
        task is held and we wait up to ``background_poll_seconds`` before
        re-checking — without blocking URGENT tasks that arrive in the interim.
        """
        with self._not_empty:
            while True:
                while not self._heap:
                    self._not_empty.wait()
                head_peek = self._heap[0]
                if head_peek.priority is TaskPriority.BACKGROUND and self._background_runnable is not None:
                    try:
                        if not self._background_runnable():
                            # Wait for new arrivals or the poll window to elapse.
                            self._not_empty.wait(timeout=self._background_poll_seconds)
                            continue
                    except Exception as e:
                        print(f"[task-queue] background_runnable check raised, allowing run: {e}")
                break

            head = heapq.heappop(self._heap)

            # Skip cancelled stragglers (defensive — cancel() removes them but
            # races are possible if cancel runs between push and pop).
            if head.state is TaskState.CANCELLED:
                self._index.pop(head.id, None)
                return []

            batch = [head]

            if head.priority is TaskPriority.URGENT:
                remaining: list[Task] = []
                for t in self._heap:
                    if (
                        t.priority is TaskPriority.URGENT
                        and t.session_id == head.session_id
                        and t.state is TaskState.QUEUED
                    ):
                        batch.append(t)
                    else:
                        remaining.append(t)
                self._heap = remaining
                heapq.heapify(self._heap)

            # Tasks remain in _index while running so cancel() can still find
            # them and set their cancel_event. They're removed in _run_batch.
            return batch

    def _start_watchdog(self, task: Task) -> threading.Timer:
        """Schedule cancel_event to fire after task.max_run_time seconds."""
        timer = threading.Timer(task.max_run_time, task.request_cancel)
        timer.daemon = True
        timer.start()
        return timer

    def _run_batch(self, batch: list[Task]) -> None:
        watchdogs = [self._start_watchdog(t) for t in batch]
        try:
            self._process_fn(batch)
        except Exception as e:
            print(f"[task-queue] error processing batch: {e}")
            for t in batch:
                if t.state in (TaskState.QUEUED, TaskState.RUNNING):
                    try:
                        if t.state is TaskState.QUEUED:
                            t.transition(TaskState.RUNNING)
                        t.transition(TaskState.FAILED)
                    except Exception:
                        pass
        finally:
            for w in watchdogs:
                w.cancel()
            with self._lock:
                for t in batch:
                    self._index.pop(t.id, None)
