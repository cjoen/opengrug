"""Tests for the Dispatcher intent classifier."""

import os
import textwrap

import pytest

from core.dispatcher import Dispatcher, DispatchDecision
from core.interfaces import LLMResponse


class _FakeWorker:
    def __init__(self, response: str):
        self.response = response
        self.last_prompt = None

    def chat(self, system_prompt, messages, tools=None):
        self.last_prompt = system_prompt
        return LLMResponse(content=self.response, tool_calls=[])


@pytest.fixture
def dispatcher_prompt(tmp_path):
    p = tmp_path / "dispatcher.md"
    p.write_text("AGENTS: {{AVAILABLE_AGENTS}}")
    return str(p)


def test_classify_returns_chat_agent_default(dispatcher_prompt):
    raw = '{"agent": "chat_agent", "context": "hello"}'
    d = Dispatcher(_FakeWorker(raw), prompt_path=dispatcher_prompt)
    decision = d.classify("hi", history=[], available_agents=["chat_agent", "researcher"])
    assert decision.agent == "chat_agent"
    assert decision.context == "hello"
    assert decision.plan is None


def test_classify_routes_to_expert_with_plan(dispatcher_prompt):
    raw = '{"agent": "researcher", "context": "find X", "plan": ["step 1", "step 2"]}'
    d = Dispatcher(_FakeWorker(raw), prompt_path=dispatcher_prompt)
    decision = d.classify("research X", history=[], available_agents=["chat_agent", "researcher"])
    assert decision.agent == "researcher"
    assert decision.plan == ["step 1", "step 2"]


def test_classify_falls_back_on_unparseable_output(dispatcher_prompt):
    d = Dispatcher(_FakeWorker("not json at all"), prompt_path=dispatcher_prompt)
    decision = d.classify("hi", history=[], available_agents=["chat_agent"])
    assert decision.agent == "chat_agent"
    assert decision.context == "hi"
    assert decision.plan is None


def test_classify_falls_back_on_unknown_agent(dispatcher_prompt):
    raw = '{"agent": "ghost", "context": "x"}'
    d = Dispatcher(_FakeWorker(raw), prompt_path=dispatcher_prompt)
    decision = d.classify("hi", history=[], available_agents=["chat_agent"])
    assert decision.agent == "chat_agent"


def test_classify_falls_back_on_worker_exception(dispatcher_prompt):
    class Boom:
        def chat(self, *a, **kw):
            raise RuntimeError("boom")
    d = Dispatcher(Boom(), prompt_path=dispatcher_prompt)
    decision = d.classify("hi", history=[], available_agents=["chat_agent"])
    assert decision.agent == "chat_agent"


def test_classify_handles_fenced_json(dispatcher_prompt):
    raw = textwrap.dedent("""
        ```json
        {"agent": "chat_agent", "context": "wrapped"}
        ```
    """).strip()
    d = Dispatcher(_FakeWorker(raw), prompt_path=dispatcher_prompt)
    decision = d.classify("hi", history=[], available_agents=["chat_agent"])
    assert decision.agent == "chat_agent"
    assert decision.context == "wrapped"


def test_classify_drops_invalid_plan_field(dispatcher_prompt):
    raw = '{"agent": "chat_agent", "context": "x", "plan": "not a list"}'
    d = Dispatcher(_FakeWorker(raw), prompt_path=dispatcher_prompt)
    decision = d.classify("hi", history=[], available_agents=["chat_agent"])
    assert decision.plan is None


def test_prompt_interpolates_available_agents(dispatcher_prompt):
    worker = _FakeWorker('{"agent": "chat_agent", "context": "x"}')
    d = Dispatcher(worker, prompt_path=dispatcher_prompt)
    d.classify("hi", history=[], available_agents=["chat_agent", "researcher"])
    assert "chat_agent, researcher" in worker.last_prompt


# ---------------------------------------------------------------------------
# Task 2: registry-aware dispatcher tests
# ---------------------------------------------------------------------------

from core.registry import ToolRegistry


def _make_registry_with_rules():
    r = ToolRegistry()
    r.register_python_tool(
        name="add_note",
        schema={"description": "save a note", "type": "object", "properties": {}},
        func=lambda: "ok",
        dispatch_to="chat_agent",
    )
    r.register_python_tool(
        name="get_health",
        schema={"description": "check health", "type": "object", "properties": {}},
        func=lambda: "ok",
        dispatcher_direct=True,
    )
    return r


def test_build_prompt_appends_routing_block(dispatcher_prompt):
    r = _make_registry_with_rules()
    worker = _FakeWorker('{"agent": "chat_agent", "context": "x"}')
    d = Dispatcher(worker, registry=r, prompt_path=dispatcher_prompt)
    prompt = d._build_prompt(["chat_agent"])
    assert "chat_agent" in prompt
    assert "add_note" in prompt
    assert "get_health" in prompt
    assert "Direct tools" in prompt


def test_build_prompt_without_registry_returns_static_file(dispatcher_prompt):
    worker = _FakeWorker('{"agent": "chat_agent", "context": "x"}')
    d = Dispatcher(worker, prompt_path=dispatcher_prompt)
    prompt = d._build_prompt(["chat_agent"])
    # Static file contents only — no routing block appended
    assert "Direct tools" not in prompt


def test_tool_with_no_dispatch_to_omitted_from_routing_block(dispatcher_prompt):
    r = ToolRegistry()
    r.register_python_tool(
        name="orphan_tool",
        schema={"description": "no rules", "type": "object", "properties": {}},
        func=lambda: "ok",
    )
    worker = _FakeWorker('{"agent": "chat_agent", "context": "x"}')
    d = Dispatcher(worker, registry=r, prompt_path=dispatcher_prompt)
    prompt = d._build_prompt(["chat_agent"])
    # orphan_tool has no dispatch_to so it must not appear under any agent
    # (it may appear in direct tools section only if dispatcher_direct=True)
    assert "orphan_tool" not in prompt


def test_classify_returns_direct_tool_on_tool_call(dispatcher_prompt):
    r = _make_registry_with_rules()

    class _DirectWorker:
        def chat(self, system_prompt, messages, tools=None):
            return LLMResponse(
                content="",
                tool_calls=[{"tool": "get_health", "arguments": {}}],
            )

    d = Dispatcher(_DirectWorker(), registry=r, prompt_path=dispatcher_prompt)
    decision = d.classify("how are you", history=[], available_agents=["chat_agent"])
    assert decision.direct_tool == "get_health"
    assert decision.direct_args == {}
    assert decision.agent == "chat_agent"  # defaults to fallback_agent


def test_classify_direct_tool_falls_back_on_worker_exception(dispatcher_prompt):
    r = _make_registry_with_rules()

    class Boom:
        def chat(self, *a, **kw):
            raise RuntimeError("boom")

    d = Dispatcher(Boom(), registry=r, prompt_path=dispatcher_prompt)
    decision = d.classify("hi", history=[], available_agents=["chat_agent"])
    assert decision.agent == "chat_agent"
    assert decision.direct_tool is None
