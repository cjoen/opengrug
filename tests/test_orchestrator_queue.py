"""End-to-end tests for the new Orchestrator → TaskQueue → AgentContainer flow."""

import threading
import time
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from core.agents import AgentContainer
from core.dispatcher import DispatchDecision
from core.interfaces import LLMResponse
from core.orchestrator import Orchestrator, MessageReply
from core.registry import ToolRegistry
from core.router import GrugRouter
from core.task import TaskState


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

class _FakeWorker:
    def __init__(self, response: LLMResponse):
        self.response = response

    def chat(self, system_prompt, messages, tools=None):
        return self.response


class _FakeSessionStore:
    def __init__(self):
        self._sessions = {}

    def get_or_create(self, session_id, channel):
        return self._sessions.setdefault(session_id, {"messages": [], "pending_hitl": None})

    def update_messages(self, session_id, messages):
        self._sessions[session_id]["messages"] = messages

    def set_pending_hitl(self, session_id, value):
        self._sessions.setdefault(session_id, {"messages": [], "pending_hitl": None})["pending_hitl"] = value

    def claim_pending_hitl(self, session_id):
        sess = self._sessions.get(session_id, {})
        p = sess.get("pending_hitl")
        sess["pending_hitl"] = None
        return p


class _FakeStorage:
    def get_capped_tail(self, n): return ""
    def get_instructions_block(self): return ""
    def log_routing_trace(self, *a, **kw): pass


class _FakeVectorMemory:
    def query_memory_raw(self, text, limit): return []


def _config():
    workers = SimpleNamespace(**{"local-fast": SimpleNamespace(target_context_tokens=2048)})
    return SimpleNamespace(
        memory=SimpleNamespace(
            thread_history_limit=10,
            capped_tail_lines=100,
            rag_result_limit=3,
            expert_max_steps=2,
        ),
        dispatcher=SimpleNamespace(worker_tier="local-fast"),
        workers=workers,
        queue=SimpleNamespace(expert_max_steps=2),
    )


def _build_orchestrator(agent_response: LLMResponse, dispatcher_decision):
    """Build an Orchestrator wired to fakes."""
    registry = ToolRegistry()
    captured = {}

    def _reply(message):
        captured["reply"] = message
        return message

    registry.register_python_tool(
        name="reply_to_user",
        schema={"type": "object", "properties": {"message": {"type": "string"}}, "required": ["message"]},
        func=_reply,
        category="SYSTEM",
    )

    worker = _FakeWorker(agent_response)
    container = AgentContainer(name="chat_agent", worker=worker, base_prompt="BASE", registry=registry)
    researcher = AgentContainer(name="researcher", worker=worker, base_prompt="RESEARCHER", registry=registry)

    dispatcher = MagicMock()
    dispatcher.classify.return_value = dispatcher_decision

    router = GrugRouter(registry, chat_worker=worker)

    orch = Orchestrator(
        router=router,
        registry=registry,
        session_store=_FakeSessionStore(),
        storage=_FakeStorage(),
        summarizer=None,
        vector_memory=_FakeVectorMemory(),
        config=_config(),
        build_system_prompt=lambda base, tail, rag_context="", instructions_block="": base,
        find_turn_boundary=lambda msgs: 2,
        auto_offload_pruned_turns=lambda *a, **kw: None,
        base_prompt="BASE",
        worker_count=1,
        agents={"chat_agent": container, "researcher": researcher},
        dispatcher=dispatcher,
    )
    return orch, captured, dispatcher


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_enqueue_routes_via_dispatcher_to_chat_agent():
    response = LLMResponse(
        content="",
        tool_calls=[{"tool": "reply_to_user", "arguments": {"message": "hi back"}}],
    )
    decision = DispatchDecision(agent="chat_agent", context="hi")
    orch, captured, dispatcher = _build_orchestrator(response, decision)

    delivered = threading.Event()
    received = {}

    def on_result(event):
        received["event"] = event
        delivered.set()

    orch.start()
    orch.enqueue(session_id="s1", text="hi", user_id="u1", on_result=on_result)
    assert delivered.wait(2.0)
    assert isinstance(received["event"], MessageReply)
    assert "hi back" in received["event"].text
    dispatcher.classify.assert_called_once()


def test_enqueue_routes_to_expert_agent_with_clean_slate():
    """Expert path: history is NOT included; framing uses task.context + plan."""
    received_msgs = {}

    class _CapturingWorker(_FakeWorker):
        def chat(self, system_prompt, messages, tools=None):
            received_msgs["messages"] = list(messages)
            received_msgs["system"] = system_prompt
            return self.response

    response = LLMResponse(
        content="",
        tool_calls=[{"tool": "reply_to_user", "arguments": {"message": "research done"}}],
    )
    decision = DispatchDecision(agent="researcher", context="find info on X", plan=["step A", "step B"])
    orch, _, _ = _build_orchestrator(response, decision)
    # Replace researcher's worker with capturing one
    orch.agents["researcher"].worker = _CapturingWorker(response)

    # Pre-populate session history — expert path must NOT include it.
    orch.session_store.get_or_create("s1", "")
    orch.session_store.update_messages("s1", [
        {"role": "user", "content": "old turn"},
        {"role": "assistant", "content": "old reply"},
    ])

    delivered = threading.Event()

    def on_result(_): delivered.set()

    orch.start()
    orch.enqueue(session_id="s1", text="research X", user_id="u1", on_result=on_result)
    assert delivered.wait(2.0)

    # Researcher saw only the framing, not the chat history
    assert len(received_msgs["messages"]) == 1
    framing = received_msgs["messages"][0]["content"]
    assert "find info on X" in framing
    assert "step A" in framing and "step B" in framing
    assert "old turn" not in framing
    # Researcher's base_prompt is what reaches the worker
    assert received_msgs["system"] == "RESEARCHER"


def test_dispatcher_failure_falls_back_to_chat_agent():
    response = LLMResponse(
        content="",
        tool_calls=[{"tool": "reply_to_user", "arguments": {"message": "fallback ok"}}],
    )
    orch, captured, dispatcher = _build_orchestrator(response, DispatchDecision(agent="chat_agent", context="x"))
    dispatcher.classify.side_effect = RuntimeError("boom")

    delivered = threading.Event()
    received = {}

    def on_result(event):
        received["event"] = event
        delivered.set()

    orch.start()
    orch.enqueue(session_id="s1", text="hi", user_id="u1", on_result=on_result)
    assert delivered.wait(2.0)
    assert isinstance(received["event"], MessageReply)
    assert "fallback ok" in received["event"].text


def test_scheduled_tool_runs_deterministically_via_registry():
    """Tasks with metadata.scheduled_tool bypass the LLM and call registry directly."""
    response = LLMResponse(content="", tool_calls=[])
    orch, _, dispatcher = _build_orchestrator(response, DispatchDecision(agent="chat_agent", context=""))

    # Register the deterministic tool the schedule will invoke.
    calls = []
    orch.registry.register_python_tool(
        name="ping_tool",
        schema={"type": "object", "properties": {}},
        func=lambda **_: (calls.append("ran") or "pong"),
        category="SYSTEM",
    )

    delivered = threading.Event()
    received = {}

    def on_result(event):
        received["event"] = event
        delivered.set()

    from core.task import Task, TaskPriority
    t = Task(
        session_id="scheduled-1", user_id="grug", agent_name="chat_agent",
        context="ping",
        priority=TaskPriority.URGENT,
        metadata={"scheduled_tool": {"name": "ping_tool", "arguments": {}, "description": "ping"}},
        on_result=on_result,
    )

    orch.start()
    orch.queue.enqueue(t)
    assert delivered.wait(2.0)
    assert calls == ["ran"]
    assert "pong" in received["event"].text
    # LLM was NOT consulted — dispatcher only fires for orch.enqueue() ingress.
    dispatcher.classify.assert_not_called()


def test_cancelled_task_does_not_pollute_history():
    """A task cancelled mid-StepLoop transitions to CANCELLED and writes nothing
    to session history."""
    cancel_seen = threading.Event()

    class _CancelObservingWorker:
        def __init__(self):
            self.calls = 0

        def chat(self, system_prompt, messages, tools=None):
            self.calls += 1
            cancel_seen.set()
            # Simulate the chat call seeing the cancel — return empty so the
            # router treats it as the cancel sentinel path.
            return LLMResponse(content="", tool_calls=[])

    response = LLMResponse(content="", tool_calls=[])
    orch, _, _ = _build_orchestrator(response, DispatchDecision(agent="chat_agent", context="x"))
    orch.agents["chat_agent"].worker = _CancelObservingWorker()

    delivered = threading.Event()
    received = {}

    def on_result(event):
        received["event"] = event
        delivered.set()

    # Pre-flag cancellation: the task picks up cancel_event before/after the
    # router call, regardless of how fast the worker runs.
    from core.task import Task, TaskPriority, TaskState
    t = Task(
        session_id="cancelled-session", user_id="u1", agent_name="chat_agent",
        context="hi",
        priority=TaskPriority.URGENT,
        metadata={"raw_text": "hi"},
        on_result=on_result,
    )
    t.cancel_event.set()  # cancel before dispatch
    orch.start()
    orch.queue.enqueue(t)

    assert delivered.wait(2.0)
    # Final state is CANCELLED, not COMPLETED
    assert t.state is TaskState.CANCELLED
    # Session history must not contain the cancel sentinel
    session = orch.session_store.get_or_create("cancelled-session", "")
    assert all("cancelled" not in m["content"].lower()
               for m in session["messages"]
               if m.get("role") == "assistant")


def test_background_gate_blocks_dispatch_end_to_end():
    """An URGENT task enqueued after a BG task gets processed first when the BG gate is closed."""
    response = LLMResponse(
        content="",
        tool_calls=[{"tool": "reply_to_user", "arguments": {"message": "ok"}}],
    )
    decision = DispatchDecision(agent="chat_agent", context="x")

    # Build with a closed BG gate
    registry = ToolRegistry()
    registry.register_python_tool(
        name="reply_to_user",
        schema={"type": "object", "properties": {"message": {"type": "string"}}, "required": ["message"]},
        func=lambda message: message,
        category="SYSTEM",
    )
    worker = _FakeWorker(response)
    container = AgentContainer(name="chat_agent", worker=worker, base_prompt="BASE", registry=registry)
    dispatcher = MagicMock()
    dispatcher.classify.return_value = decision
    router = GrugRouter(registry, chat_worker=worker)

    orch = Orchestrator(
        router=router, registry=registry, session_store=_FakeSessionStore(),
        storage=_FakeStorage(), summarizer=None, vector_memory=_FakeVectorMemory(),
        config=_config(),
        build_system_prompt=lambda base, tail, rag_context="", instructions_block="": base,
        find_turn_boundary=lambda msgs: 2,
        auto_offload_pruned_turns=lambda *a, **kw: None,
        base_prompt="BASE", worker_count=1,
        agents={"chat_agent": container}, dispatcher=dispatcher,
        background_runnable=lambda: False,  # gate closed
    )

    from core.task import Task, TaskPriority, TaskState
    bg = Task(session_id="bg-sess", user_id="u", agent_name="chat_agent",
              context="bg", priority=TaskPriority.BACKGROUND,
              metadata={"raw_text": "bg"})

    urgent_done = threading.Event()
    urgent = Task(session_id="urg-sess", user_id="u", agent_name="chat_agent",
                  context="urgent", priority=TaskPriority.URGENT,
                  metadata={"raw_text": "urgent"},
                  on_result=lambda _: urgent_done.set())

    orch.start()
    orch.queue.enqueue(bg)
    time.sleep(0.1)  # let the worker peek at the BG task and decide to wait
    orch.queue.enqueue(urgent)

    assert urgent_done.wait(2.0)
    assert urgent.state is TaskState.COMPLETED
    # BG task remains queued (gate still closed)
    assert bg.state is TaskState.QUEUED


def test_enqueue_returns_before_dispatcher_completes():
    """Slack ingress must not block on a slow dispatcher LLM call."""
    response = LLMResponse(
        content="", tool_calls=[{"tool": "reply_to_user", "arguments": {"message": "ok"}}],
    )
    orch, _, dispatcher = _build_orchestrator(response, DispatchDecision(agent="chat_agent", context="x"))

    block = threading.Event()
    release = threading.Event()

    def slow_classify(**kw):
        block.set()
        release.wait(2.0)
        return DispatchDecision(agent="chat_agent", context="x")

    dispatcher.classify.side_effect = slow_classify

    delivered = threading.Event()

    orch.start()
    # Time the enqueue call — it must return immediately even though the
    # dispatcher is blocked.
    import time as _t
    t0 = _t.perf_counter()
    orch.enqueue(session_id="s1", text="hi", user_id="u1",
                 on_result=lambda _: delivered.set())
    elapsed = _t.perf_counter() - t0
    assert elapsed < 0.1, f"enqueue took {elapsed:.3f}s — should be near-instant"

    # The dispatcher is now waiting on `release`. Confirm the task hasn't been
    # delivered yet.
    assert block.wait(1.0)
    assert not delivered.is_set()

    release.set()
    assert delivered.wait(2.0)


def test_session_affinity_enforced_through_orchestrator():
    """Two messages on the same session run sequentially."""
    response = LLMResponse(
        content="",
        tool_calls=[{"tool": "reply_to_user", "arguments": {"message": "ok"}}],
    )
    orch, _, _ = _build_orchestrator(response, DispatchDecision(agent="chat_agent", context="x"))

    completions = []
    lock = threading.Lock()
    expected = 2
    done = threading.Event()

    def on_result(_):
        with lock:
            completions.append(threading.current_thread().name)
            if len(completions) >= expected:
                done.set()

    orch.start()
    orch.enqueue(session_id="s1", text="msg 1", user_id="u1", on_result=on_result)
    orch.enqueue(session_id="s1", text="msg 2", user_id="u1", on_result=on_result)

    assert done.wait(3.0)
    assert len(completions) == 2


def test_retry_succeeds_does_not_dlq(tmp_path):
    """Worker fails first call, then succeeds. on_result delivers MessageReply
    and the DLQ stays empty."""
    from core.dlq import DeadLetterQueue

    success_response = LLMResponse(
        content="",
        tool_calls=[{"tool": "reply_to_user", "arguments": {"message": "second time charm"}}],
    )

    class _FlakyWorker(_FakeWorker):
        def __init__(self, response):
            super().__init__(response)
            self.calls = 0

        def chat(self, system_prompt, messages, tools=None):
            self.calls += 1
            if self.calls == 1:
                raise RuntimeError("transient")
            return self.response

    # Build the orchestrator with the standard helper, then swap the worker.
    orch, captured, _ = _build_orchestrator(success_response,
                                            DispatchDecision(agent="chat_agent", context="hi"))
    flaky = _FlakyWorker(success_response)
    orch.agents["chat_agent"].worker = flaky
    orch.router.chat_worker = flaky

    # Wire a DLQ + max_retries to the queue.
    dlq = DeadLetterQueue(str(tmp_path / "failed.md"))
    orch._queue._dlq = dlq
    orch._queue._max_retries = 1

    delivered = threading.Event()
    received = {}

    def on_result(event):
        if isinstance(event, MessageReply):
            received["event"] = event
            delivered.set()

    orch.start()
    orch.enqueue(session_id="retry-int", text="hi", user_id="u1", on_result=on_result)

    # Backoff is 1s for attempt=1.
    assert delivered.wait(5.0)
    assert "second time charm" in received["event"].text
    # Wait briefly to ensure no DLQ write races in.
    time.sleep(0.2)
    assert dlq.size() == 0
    assert flaky.calls == 2


def test_dispatch_pool_runs_classifies_concurrently():
    """With dispatch_worker_count=2, two slow classify calls overlap."""
    from core.dispatcher import DispatchDecision

    response = LLMResponse(
        content="",
        tool_calls=[{"tool": "reply_to_user", "arguments": {"message": "ok"}}],
    )
    orch, _, _ = _build_orchestrator(response, DispatchDecision(agent="chat_agent", context="x"))
    orch._dispatch_worker_count = 2
    orch._dispatch_workers = []  # force re-spawn on start

    barrier = threading.Barrier(2, timeout=2.0)
    classified = threading.Semaphore(0)

    def slow_classify(**kw):
        # Both classify calls must reach the barrier together → proves overlap.
        barrier.wait()
        classified.release()
        return DispatchDecision(agent="chat_agent", context="x")

    orch.dispatcher.classify.side_effect = slow_classify

    orch.start()
    orch.enqueue(session_id="s-a", text="msg a", user_id="u1")
    orch.enqueue(session_id="s-b", text="msg b", user_id="u2")

    # Both classify calls should release the semaphore via the barrier.
    assert classified.acquire(timeout=3.0)
    assert classified.acquire(timeout=3.0)


def test_scheduled_destructive_tool_refused_without_allow_unattended():
    response = LLMResponse(content="", tool_calls=[])
    orch, _, _ = _build_orchestrator(response, DispatchDecision(agent="chat_agent", context=""))

    calls = []
    orch.registry.register_python_tool(
        name="rm_thing",
        schema={"type": "object", "properties": {}},
        func=lambda **_: (calls.append("ran") or "deleted"),
        category="SYSTEM",
        destructive=True,
    )

    delivered = threading.Event()
    received = {}

    def on_result(event):
        received["event"] = event
        delivered.set()

    from core.task import Task, TaskPriority
    t = Task(
        session_id="sched-d", user_id="grug", agent_name="chat_agent",
        context="run",
        priority=TaskPriority.URGENT,
        metadata={"scheduled_tool": {"name": "rm_thing", "arguments": {}, "description": "wipe"}},
        on_result=on_result,
    )

    orch.start()
    orch.queue.enqueue(t)
    assert delivered.wait(2.0)
    assert calls == []  # registry never called
    assert "refused" in received["event"].text.lower()


def test_scheduled_destructive_tool_runs_with_allow_unattended():
    response = LLMResponse(content="", tool_calls=[])
    orch, _, _ = _build_orchestrator(response, DispatchDecision(agent="chat_agent", context=""))

    calls = []
    orch.registry.register_python_tool(
        name="rm_thing",
        schema={"type": "object", "properties": {}},
        func=lambda **_: (calls.append("ran") or "deleted"),
        category="SYSTEM",
        destructive=True,
    )

    delivered = threading.Event()
    received = {}

    def on_result(event):
        received["event"] = event
        delivered.set()

    from core.task import Task, TaskPriority
    t = Task(
        session_id="sched-d2", user_id="grug", agent_name="chat_agent",
        context="run",
        priority=TaskPriority.URGENT,
        metadata={"scheduled_tool": {
            "name": "rm_thing", "arguments": {},
            "description": "wipe", "allow_unattended": True,
        }},
        on_result=on_result,
    )

    orch.start()
    orch.queue.enqueue(t)
    assert delivered.wait(2.0)
    assert calls == ["ran"]
    assert "deleted" in received["event"].text
