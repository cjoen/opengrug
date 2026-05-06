"""Task model for the priority queue (Phase 5.3).

A Task encapsulates everything a worker needs to execute one unit of work:
target agent, distilled context, optional plan, callback, and cancellation
signaling. State transitions are explicit and validated.
"""

from __future__ import annotations

import threading
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum, IntEnum
from typing import Any, Callable, Optional


class TaskPriority(IntEnum):
    """Priority levels. Lower value = higher priority (heapq min-heap)."""
    URGENT = 0
    BACKGROUND = 10


class TaskState(Enum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


# Allowed transitions. CANCELLED is reachable from QUEUED or RUNNING.
_VALID_TRANSITIONS: dict[TaskState, set[TaskState]] = {
    TaskState.QUEUED: {TaskState.RUNNING, TaskState.CANCELLED},
    TaskState.RUNNING: {TaskState.COMPLETED, TaskState.FAILED, TaskState.CANCELLED},
    TaskState.COMPLETED: set(),
    TaskState.FAILED: set(),
    TaskState.CANCELLED: set(),
}


class InvalidTransitionError(RuntimeError):
    pass


@dataclass(order=False)
class Task:
    """A unit of work routed to a specific agent."""
    session_id: str
    user_id: str
    agent_name: str
    context: str
    priority: TaskPriority = TaskPriority.URGENT
    state: TaskState = TaskState.QUEUED
    plan: Optional[list[str]] = None
    metadata: dict = field(default_factory=dict)
    on_result: Optional[Callable[[Any], None]] = None
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    created_at: float = field(default_factory=time.time)
    cancel_event: threading.Event = field(default_factory=threading.Event)
    max_run_time: float = 300.0
    root_task_id: str = ""
    attempt: int = 1

    def __post_init__(self) -> None:
        if not self.root_task_id:
            self.root_task_id = self.id

    def transition(self, new_state: TaskState) -> None:
        """Move to new_state, raising if the transition is invalid."""
        if new_state not in _VALID_TRANSITIONS[self.state]:
            raise InvalidTransitionError(
                f"task {self.id}: cannot transition {self.state.value} -> {new_state.value}"
            )
        self.state = new_state

    def request_cancel(self) -> None:
        """Signal cooperative cancellation. Caller is responsible for the state transition."""
        self.cancel_event.set()

    # Heap ordering: priority first, then created_at (FIFO within priority).
    # Defined explicitly so two tasks with equal keys don't trigger comparison
    # on the dataclass itself (which would fail on threading.Event).
    def __lt__(self, other: "Task") -> bool:
        return (self.priority, self.created_at) < (other.priority, other.created_at)
