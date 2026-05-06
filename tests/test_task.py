"""Tests for the Task model: state transitions, ordering, cancellation."""

import pytest

from core.task import (
    Task,
    TaskPriority,
    TaskState,
    InvalidTransitionError,
)


def _make(**overrides) -> Task:
    defaults = dict(
        session_id="s1",
        user_id="u1",
        agent_name="chat_agent",
        context="hello",
    )
    defaults.update(overrides)
    return Task(**defaults)


def test_defaults():
    t = _make()
    assert t.state is TaskState.QUEUED
    assert t.priority is TaskPriority.URGENT
    assert t.plan is None
    assert t.id  # uuid populated
    assert t.cancel_event.is_set() is False


def test_root_task_id_defaults_to_id():
    t = _make()
    assert t.root_task_id == t.id
    assert t.attempt == 1


def test_root_task_id_preserved_when_provided():
    t = _make(root_task_id="root-abc", attempt=3)
    assert t.root_task_id == "root-abc"
    assert t.attempt == 3


def test_valid_transition_queued_to_running_to_completed():
    t = _make()
    t.transition(TaskState.RUNNING)
    assert t.state is TaskState.RUNNING
    t.transition(TaskState.COMPLETED)
    assert t.state is TaskState.COMPLETED


def test_invalid_transition_raises():
    t = _make()
    with pytest.raises(InvalidTransitionError):
        t.transition(TaskState.COMPLETED)  # QUEUED -> COMPLETED not allowed


def test_terminal_state_cannot_transition():
    t = _make()
    t.transition(TaskState.RUNNING)
    t.transition(TaskState.FAILED)
    with pytest.raises(InvalidTransitionError):
        t.transition(TaskState.RUNNING)


def test_cancel_from_queued():
    t = _make()
    t.transition(TaskState.CANCELLED)
    assert t.state is TaskState.CANCELLED


def test_cancel_from_running():
    t = _make()
    t.transition(TaskState.RUNNING)
    t.transition(TaskState.CANCELLED)
    assert t.state is TaskState.CANCELLED


def test_request_cancel_sets_event_without_state_change():
    t = _make()
    t.request_cancel()
    assert t.cancel_event.is_set()
    assert t.state is TaskState.QUEUED


def test_priority_ordering_urgent_before_background():
    urgent = _make(priority=TaskPriority.URGENT)
    background = _make(priority=TaskPriority.BACKGROUND)
    assert urgent < background


def test_fifo_within_same_priority():
    import time
    first = _make()
    time.sleep(0.001)
    second = _make()
    assert first < second


def test_heap_ordering():
    import heapq
    a = _make(priority=TaskPriority.BACKGROUND)
    b = _make(priority=TaskPriority.URGENT)
    c = _make(priority=TaskPriority.URGENT)
    h: list = []
    for t in (a, b, c):
        heapq.heappush(h, t)
    popped = [heapq.heappop(h) for _ in range(3)]
    # Both URGENTs come out before BACKGROUND
    assert popped[0].priority is TaskPriority.URGENT
    assert popped[1].priority is TaskPriority.URGENT
    assert popped[2] is a
