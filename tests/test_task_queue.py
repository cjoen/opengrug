"""Tests for TaskQueue: priority ordering, session affinity, batching, cancel."""

import threading
import time

import pytest

from core.task import Task, TaskPriority, TaskState
from core.task_queue import TaskQueue, make_hour_window_check


def _task(session="s1", agent="chat_agent", priority=TaskPriority.URGENT, context="hi"):
    return Task(
        session_id=session,
        user_id="u1",
        agent_name=agent,
        context=context,
        priority=priority,
    )


def test_enqueue_rejects_non_queued_state():
    q = TaskQueue(process_fn=lambda batch: None, worker_count=0)
    t = _task()
    t.transition(TaskState.RUNNING)
    with pytest.raises(ValueError):
        q.enqueue(t)


def test_priority_ordering_urgent_drained_first():
    seen: list[Task] = []
    done = threading.Event()
    expected = 3

    def proc(batch):
        seen.extend(batch)
        if len(seen) >= expected:
            done.set()

    q = TaskQueue(process_fn=proc, worker_count=1)
    bg = _task(session="bg-session", priority=TaskPriority.BACKGROUND)
    u1 = _task(session="urgent-1", priority=TaskPriority.URGENT)
    u2 = _task(session="urgent-2", priority=TaskPriority.URGENT)
    q.enqueue(bg)
    q.enqueue(u1)
    q.enqueue(u2)
    q.start()

    assert done.wait(2.0)
    # Both URGENTs processed before the BACKGROUND one
    bg_idx = next(i for i, t in enumerate(seen) if t.priority is TaskPriority.BACKGROUND)
    urgent_idxs = [i for i, t in enumerate(seen) if t.priority is TaskPriority.URGENT]
    assert all(i < bg_idx for i in urgent_idxs)


def test_urgent_same_session_batched():
    batches: list[list[Task]] = []
    done = threading.Event()

    def proc(batch):
        batches.append(batch)
        if sum(len(b) for b in batches) >= 3:
            done.set()

    q = TaskQueue(process_fn=proc, worker_count=1)
    a = _task(session="s1")
    b = _task(session="s1")
    c = _task(session="s1")
    q.enqueue(a)
    q.enqueue(b)
    q.enqueue(c)
    q.start()

    assert done.wait(2.0)
    # All three same-session URGENTs collapse into one batch
    assert len(batches) == 1
    assert [t.id for t in batches[0]] == [a.id, b.id, c.id]


def test_urgent_different_sessions_not_batched():
    batches: list[list[Task]] = []
    done = threading.Event()

    def proc(batch):
        batches.append(batch)
        if sum(len(b) for b in batches) >= 2:
            done.set()

    q = TaskQueue(process_fn=proc, worker_count=1)
    q.enqueue(_task(session="s1"))
    q.enqueue(_task(session="s2"))
    q.start()

    assert done.wait(2.0)
    assert len(batches) == 2
    assert all(len(b) == 1 for b in batches)


def test_session_affinity_serializes_workers():
    """Two workers must not run the same session concurrently."""
    in_flight = {"s1": 0}
    max_concurrent = {"s1": 0}
    lock = threading.Lock()
    started = threading.Event()
    release = threading.Event()
    done_count = {"n": 0}
    done = threading.Event()

    def proc(batch):
        # Mark this session as in-flight on entry; assert no overlap.
        sid = batch[0].session_id
        with lock:
            in_flight[sid] = in_flight.get(sid, 0) + 1
            max_concurrent[sid] = max(max_concurrent[sid], in_flight[sid])
        started.set()
        release.wait(2.0)
        with lock:
            in_flight[sid] -= 1
            done_count["n"] += 1
            if done_count["n"] >= 2:
                done.set()

    q = TaskQueue(process_fn=proc, worker_count=2)
    # Two batches for the same session — workers must serialize them.
    q.enqueue(_task(session="s1"))
    # Force a separate batch by enqueuing after the first is dequeued.
    q.start()
    started.wait(2.0)
    q.enqueue(_task(session="s1"))
    release.set()

    assert done.wait(3.0)
    assert max_concurrent["s1"] == 1


def test_cancel_queued_task_removes_from_heap():
    proc_called = threading.Event()

    def proc(batch):
        proc_called.set()

    q = TaskQueue(process_fn=proc, worker_count=0)  # no workers, queue stays static
    t = _task()
    q.enqueue(t)
    assert q.pending_count() == 1

    changed = q.cancel(t.id)
    assert changed is True
    assert t.state is TaskState.CANCELLED
    assert q.pending_count() == 0


def test_cancel_unknown_id_returns_false():
    q = TaskQueue(process_fn=lambda b: None, worker_count=0)
    assert q.cancel("does-not-exist") is False


def test_watchdog_cancels_after_max_run_time():
    """A task that exceeds max_run_time has its cancel_event set automatically."""
    saw_cancel = threading.Event()
    finished = threading.Event()

    def proc(batch):
        for t in batch:
            t.transition(TaskState.RUNNING)
        # Wait for the watchdog to fire (max_run_time=0.1s).
        if batch[0].cancel_event.wait(2.0):
            saw_cancel.set()
        finished.set()

    q = TaskQueue(process_fn=proc, worker_count=1)
    t = _task()
    t.max_run_time = 0.1
    q.enqueue(t)
    q.start()

    assert finished.wait(3.0)
    assert saw_cancel.is_set()


def test_cancel_running_task_signals_event():
    """If task is already executing, cancel() sets the cancel_event."""
    started = threading.Event()
    release = threading.Event()
    saw_cancel = {"v": False}
    finished = threading.Event()

    def proc(batch):
        # Mark task as RUNNING so cancel takes the running-path
        for t in batch:
            t.transition(TaskState.RUNNING)
        started.set()
        # Wait for cancel signal
        for t in batch:
            if t.cancel_event.wait(2.0):
                saw_cancel["v"] = True
        release.wait(2.0)
        finished.set()

    q = TaskQueue(process_fn=proc, worker_count=1)
    t = _task()
    q.enqueue(t)
    q.start()

    assert started.wait(2.0)
    assert q.cancel(t.id) is True
    release.set()
    assert finished.wait(2.0)
    assert saw_cancel["v"] is True


def test_hour_window_check_simple_range():
    from unittest.mock import patch
    import datetime as _dt
    with patch("core.task_queue.datetime") as mock_dt:
        mock_dt.now.return_value = _dt.datetime(2026, 1, 1, 9, 0, 0)
        assert make_hour_window_check(8, 17)() is True
        mock_dt.now.return_value = _dt.datetime(2026, 1, 1, 18, 0, 0)
        assert make_hour_window_check(8, 17)() is False


def test_hour_window_check_wraps_midnight():
    from unittest.mock import patch
    import datetime as _dt
    with patch("core.task_queue.datetime") as mock_dt:
        mock_dt.now.return_value = _dt.datetime(2026, 1, 1, 23, 0, 0)
        assert make_hour_window_check(22, 6)() is True
        mock_dt.now.return_value = _dt.datetime(2026, 1, 1, 3, 0, 0)
        assert make_hour_window_check(22, 6)() is True
        mock_dt.now.return_value = _dt.datetime(2026, 1, 1, 12, 0, 0)
        assert make_hour_window_check(22, 6)() is False


def test_urgent_batch_preserves_fifo_within_session():
    """Same-session URGENTs must be delivered to the worker in submission order."""
    captured: list[Task] = []
    done = threading.Event()
    expected = 5

    def proc(batch):
        captured.extend(batch)
        if len(captured) >= expected:
            done.set()

    q = TaskQueue(process_fn=proc, worker_count=1)
    tasks = []
    for i in range(expected):
        t = _task(session="fifo")
        # Force monotonic created_at without relying on real-time spacing.
        t.created_at = 1000.0 + i
        tasks.append(t)
        q.enqueue(t)
    q.start()

    assert done.wait(2.0)
    assert [t.id for t in captured] == [t.id for t in tasks]


def test_session_lock_reaped_after_drain():
    """After all tasks for a session complete, its lock entry is removed."""
    done = threading.Event()
    counter = {"n": 0}

    def proc(batch):
        counter["n"] += 1
        if counter["n"] >= 2:
            done.set()

    q = TaskQueue(process_fn=proc, worker_count=1)
    q.enqueue(_task(session="reaped"))
    q.start()
    # Wait for first batch to drain so the lock could be reaped before the next.
    time.sleep(0.05)
    q.enqueue(_task(session="reaped"))

    assert done.wait(2.0)
    # Give the worker loop a moment to run the post-batch reap.
    for _ in range(50):
        if "reaped" not in q._session_locks:
            break
        time.sleep(0.02)
    assert "reaped" not in q._session_locks


def test_background_held_when_gate_closed_urgent_runs_immediately():
    """Background tasks wait when the gate is closed; URGENT tasks bypass."""
    processed: list[Task] = []
    done = threading.Event()
    gate = {"open": False}

    def proc(batch):
        processed.extend(batch)
        if any(t.priority is TaskPriority.URGENT for t in processed):
            done.set()

    q = TaskQueue(
        process_fn=proc,
        worker_count=1,
        background_runnable=lambda: gate["open"],
        background_poll_seconds=0.05,
    )
    bg = _task(session="bg", priority=TaskPriority.BACKGROUND)
    q.enqueue(bg)
    q.start()
    # Give the worker a moment to look at the BG task and decide to wait.
    time.sleep(0.1)
    assert not processed  # gate closed → BG not yet processed

    urgent = _task(session="u", priority=TaskPriority.URGENT)
    q.enqueue(urgent)
    assert done.wait(2.0)
    # URGENT got through despite the closed BG gate
    assert any(t.id == urgent.id for t in processed)
