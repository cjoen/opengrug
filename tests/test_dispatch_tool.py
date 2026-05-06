"""Tests for the wired dispatch_task tool."""

import threading

from core.registry import ToolRegistry
from core.task import TaskPriority, TaskState
from core.task_queue import TaskQueue
from core.router import GrugRouter
from tools.dispatch import register_tools as register_dispatch_tools


def _make_router():
    return GrugRouter(registry=ToolRegistry())


def test_stub_when_no_queue():
    reg = ToolRegistry()
    register_dispatch_tools(reg)
    res = reg.execute("dispatch_task", {"agent": "researcher", "context": "x"})
    assert "stub" in res.output.lower()


def test_dispatch_enqueues_task():
    enqueued = []
    q = TaskQueue(process_fn=lambda batch: enqueued.extend(batch), worker_count=0)
    reg = ToolRegistry()
    router = _make_router()
    router._request_state._dispatch_session_id = "thread-42"
    router._request_state._dispatch_user_id = "U123"
    register_dispatch_tools(
        reg,
        task_queue=q,
        agents={"researcher": object(), "chat_agent": object()},
        router=router,
    )

    res = reg.execute(
        "dispatch_task",
        {"agent": "researcher", "context": "look up X", "plan": ["a", "b"]},
    )
    assert res.success is True
    assert "Dispatched to researcher" in res.output
    assert q.pending_count() == 1


def test_dispatch_rejects_unknown_agent():
    q = TaskQueue(process_fn=lambda b: None, worker_count=0)
    reg = ToolRegistry()
    register_dispatch_tools(reg, task_queue=q, agents={"chat_agent": object()}, router=_make_router())
    res = reg.execute("dispatch_task", {"agent": "ghost", "context": "x"})
    assert "unknown agent" in res.output.lower()
    assert q.pending_count() == 0


def test_dispatch_attaches_on_result_callback():
    """on_result attached at dispatch time fires when the queue processes the task."""
    delivered = threading.Event()
    captured = {}

    def proc(batch):
        for t in batch:
            t.transition(TaskState.RUNNING)
            t.transition(TaskState.COMPLETED)
            if t.on_result:
                t.on_result("done")

    q = TaskQueue(process_fn=proc, worker_count=1)
    reg = ToolRegistry()
    router = _make_router()
    router._request_state._dispatch_session_id = "thread-1"
    router._request_state._dispatch_user_id = "U1"

    def on_result(payload):
        captured["payload"] = payload
        delivered.set()

    router._request_state._dispatch_on_result = on_result
    register_dispatch_tools(reg, task_queue=q, agents={"researcher": object()}, router=router)

    q.start()
    reg.execute("dispatch_task", {"agent": "researcher", "context": "x"})
    assert delivered.wait(2.0)
    assert captured["payload"] == "done"
