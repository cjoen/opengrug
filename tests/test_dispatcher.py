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
