"""Tests for operator tools."""

from core.dlq import DeadLetterQueue
from core.registry import ToolRegistry
from core.task import Task, TaskPriority, TaskState
from core.task_queue import TaskQueue
from tools.operator import register_tools, queue_status, retry_dlq, clear_dlq, drain_queue, cancel_task


class _FakeWorker:
    def __init__(self, model, msg):
        self.model_name = model
        self._msg = msg

    def health_check(self):
        return self._msg


def _queue():
    return TaskQueue(process_fn=lambda b: None, worker_count=0)


def _make_task(**kw):
    defaults = dict(session_id="s1", user_id="u1", agent_name="chat_agent", context="hi",
                    priority=TaskPriority.URGENT)
    defaults.update(kw)
    return Task(**defaults)


def test_queue_status_reports_state(tmp_path):
    pool = {"fast": _FakeWorker("llama", "Ollama: reachable, llama loaded")}
    dlq = DeadLetterQueue(str(tmp_path / "f.md"))
    q = _queue()
    out = queue_status(pool, q, dlq)
    assert "Queue depth: 0" in out
    assert "DLQ size: 0" in out
    assert "fast" in out


def test_retry_dlq_re_enqueues_and_clears(tmp_path):
    dlq = DeadLetterQueue(str(tmp_path / "f.md"))
    q = _queue()
    t = _make_task()
    dlq.add(t, error="boom", reason="failed")
    assert dlq.size() == 1

    out = retry_dlq(q, dlq)
    assert "Re-enqueued 1" in out
    assert q.pending_count() == 1
    assert dlq.size() == 0


def test_retry_dlq_empty(tmp_path):
    dlq = DeadLetterQueue(str(tmp_path / "f.md"))
    q = _queue()
    out = retry_dlq(q, dlq)
    assert "empty" in out.lower()


def test_clear_dlq_purges(tmp_path):
    dlq = DeadLetterQueue(str(tmp_path / "f.md"))
    dlq.add(_make_task(), error="a")
    dlq.add(_make_task(), error="b")
    out = clear_dlq(dlq)
    assert "2 entries" in out
    assert dlq.size() == 0


def test_drain_queue_cancels_only_background():
    q = _queue()
    bg1 = _make_task(session_id="bg1", priority=TaskPriority.BACKGROUND)
    bg2 = _make_task(session_id="bg2", priority=TaskPriority.BACKGROUND)
    urg = _make_task(session_id="urg", priority=TaskPriority.URGENT)
    q.enqueue(bg1)
    q.enqueue(bg2)
    q.enqueue(urg)
    out = drain_queue(q)
    assert "2" in out
    assert urg.state is TaskState.QUEUED
    assert bg1.state is TaskState.CANCELLED
    assert bg2.state is TaskState.CANCELLED


def test_cancel_task_by_id():
    q = _queue()
    t = _make_task()
    q.enqueue(t)
    out = cancel_task(q, t.id)
    assert "Cancelled" in out
    assert t.state is TaskState.CANCELLED


def test_cancel_task_unknown_id():
    q = _queue()
    out = cancel_task(q, "no-such-id")
    assert "not found" in out


def test_register_tools_gates_destructive(tmp_path):
    reg = ToolRegistry()
    pool = {"fast": _FakeWorker("llama", "ok")}
    dlq = DeadLetterQueue(str(tmp_path / "f.md"))
    q = _queue()
    register_tools(reg, q, dlq, pool)

    # Non-destructive: executes immediately
    res = reg.execute("queue_status", {})
    assert res.success and not res.requires_approval

    # Destructive: returns approval-required without executing
    res = reg.execute("clear_dlq", {})
    assert res.requires_approval is True

    # With skip_hitl, executes
    res = reg.execute("clear_dlq", {}, skip_hitl=True)
    assert res.success and not res.requires_approval
