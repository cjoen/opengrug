"""End-to-end tests for the new Orchestrator → TaskQueue → AgentContainer flow."""

import threading
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
    task = orch.enqueue(session_id="s1", text="hi", user_id="u1", on_result=on_result)
    assert delivered.wait(2.0)
    assert isinstance(received["event"], MessageReply)
    assert "hi back" in received["event"].text
    assert task.state is TaskState.COMPLETED
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
