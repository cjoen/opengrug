"""Tests for DeadLetterQueue: write/read/remove/clear cycle and queue integration."""

import os
import threading

from core.dlq import DeadLetterQueue
from core.task import Task, TaskPriority, TaskState
from core.task_queue import TaskQueue


def _task(**overrides):
    defaults = dict(
        session_id="s1",
        user_id="u1",
        agent_name="researcher",
        context="look up X",
        priority=TaskPriority.BACKGROUND,
    )
    defaults.update(overrides)
    return Task(**defaults)


def test_add_and_list_failed_round_trip(tmp_path):
    dlq = DeadLetterQueue(str(tmp_path / "failed.md"))
    t = _task()
    dlq.add(t, error="ollama timeout", traceback_str="Traceback...\n  line 1\n  line 2")

    entries = dlq.list_failed()
    assert len(entries) == 1
    e = entries[0]
    assert e["task_id"] == t.id
    assert e["agent"] == "researcher"
    assert e["priority"] == "BACKGROUND"
    assert e["session"] == "s1"
    assert e["user"] == "u1"
    assert e["reason"] == "failed"
    assert "ollama timeout" in e["error"]
    assert "line 1" in e["traceback"]


def test_multiple_entries_preserved(tmp_path):
    dlq = DeadLetterQueue(str(tmp_path / "failed.md"))
    t1 = _task()
    t2 = _task()
    dlq.add(t1, error="boom", traceback_str="")
    dlq.add(t2, error="kapow", traceback_str="")
    entries = dlq.list_failed()
    assert {e["task_id"] for e in entries} == {t1.id, t2.id}


def test_remove_specific_entry(tmp_path):
    dlq = DeadLetterQueue(str(tmp_path / "failed.md"))
    t1 = _task()
    t2 = _task()
    dlq.add(t1, error="a")
    dlq.add(t2, error="b")
    assert dlq.remove(t1.id) is True
    remaining = dlq.list_failed()
    assert len(remaining) == 1
    assert remaining[0]["task_id"] == t2.id


def test_remove_unknown_returns_false(tmp_path):
    dlq = DeadLetterQueue(str(tmp_path / "failed.md"))
    dlq.add(_task(), error="x")
    assert dlq.remove("not-a-real-id") is False
    assert len(dlq.list_failed()) == 1


def test_clear_purges_all(tmp_path):
    dlq = DeadLetterQueue(str(tmp_path / "failed.md"))
    dlq.add(_task(), error="a")
    dlq.add(_task(), error="b")
    n = dlq.clear()
    assert n == 2
    assert dlq.list_failed() == []


def test_size_matches_count(tmp_path):
    dlq = DeadLetterQueue(str(tmp_path / "failed.md"))
    assert dlq.size() == 0
    dlq.add(_task(), error="a")
    dlq.add(_task(), error="b")
    assert dlq.size() == 2


def test_list_failed_on_missing_file_returns_empty(tmp_path):
    dlq = DeadLetterQueue(str(tmp_path / "missing.md"))
    assert dlq.list_failed() == []


def test_queue_routes_failed_task_to_dlq(tmp_path):
    """When process_fn raises, the failed task is routed to the DLQ."""
    dlq = DeadLetterQueue(str(tmp_path / "failed.md"))
    finished = threading.Event()

    def proc(batch):
        finished.set()
        raise RuntimeError("simulated failure")

    q = TaskQueue(process_fn=proc, worker_count=1, dlq=dlq, max_retries=0)
    t = _task(priority=TaskPriority.URGENT, session_id="dlq-test")
    q.enqueue(t)
    q.start()

    assert finished.wait(2.0)
    # Give the queue a moment to write to DLQ after the batch completes.
    for _ in range(50):
        if dlq.size() >= 1:
            break
        threading.Event().wait(0.05)

    entries = dlq.list_failed()
    assert len(entries) == 1
    assert entries[0]["task_id"] == t.id
    assert entries[0]["reason"] == "failed"


def test_queue_retries_before_dlq(tmp_path):
    """With max_retries=2, a failing task is retried twice before DLQ."""
    dlq = DeadLetterQueue(str(tmp_path / "failed.md"))
    attempts = {"n": 0}
    barrier = threading.Event()

    def proc(batch):
        attempts["n"] += 1
        if attempts["n"] >= 3:
            barrier.set()
        raise RuntimeError("always fails")

    q = TaskQueue(process_fn=proc, worker_count=1, dlq=dlq, max_retries=2)
    q.enqueue(_task(priority=TaskPriority.URGENT, session_id="retry-test"))
    q.start()

    # Backoff: 1s + 2s between retries, plus scheduling overhead.
    assert barrier.wait(8.0)
    # Wait for DLQ write
    for _ in range(50):
        if dlq.size() >= 1:
            break
        threading.Event().wait(0.05)
    assert attempts["n"] == 3  # initial + 2 retries
    assert dlq.size() == 1


def test_queue_no_dlq_no_route(tmp_path):
    """Without a DLQ, failures don't error out."""
    def proc(batch):
        raise RuntimeError("nope")

    q = TaskQueue(process_fn=proc, worker_count=1, dlq=None, max_retries=0)
    finished = threading.Event()

    def watch(batch):
        finished.set()
        raise RuntimeError("nope")

    q._process_fn = watch
    q.enqueue(_task(session_id="no-dlq"))
    q.start()
    assert finished.wait(2.0)
