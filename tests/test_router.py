"""Tests for GrugRouter: routing, multi-action, unknown tools."""

from core.utils import load_agent_prompt
from core.interfaces import LLMResponse


def test_graceful_offline_degradation(fresh_env):
    _, _, router = fresh_env
    base_prompt = load_agent_prompt("prompts/base.md", "prompts/agents/chat_agent.md")

    router.invoke_chat = lambda sys_prompt, msgs, tools=None: LLMResponse(
        content="",
        tool_calls=[{"tool": "ask_for_clarification", "arguments": {"reason_for_confusion": "Grug brain foggy. Ollama not responding."}}]
    )
    res = router.route_message("Explain quantum mechanics.", system_prompt=base_prompt)
    assert res.success is True
    assert "Grug" in res.output


def test_prefixed_message_routes_through_llm(fresh_env):
    _, _, router = fresh_env
    invocations = []

    def mock_invoke_chat(sys_prompt, msgs, tools=None):
        invocations.append(True)
        return LLMResponse(
            content="",
            tool_calls=[{"tool": "add_note", "arguments": {"content": "fire is hot"}}]
        )

    router.invoke_chat = mock_invoke_chat
    res = router.route_message("/note fire is hot")
    assert res.success is True
    assert len(invocations) == 1


def test_task_message_routes_through_llm(fresh_env):
    _, registry, router = fresh_env
    registry.register_python_tool(
        name="add_task",
        schema={
            "type": "object",
            "properties": {"title": {"type": "string"}},
            "required": ["title"],
        },
        func=lambda title: f"task added: {title}",
        category="TASKS",
    )

    def mock_invoke_chat(sys_prompt, msgs, tools=None):
        return LLMResponse(
            content="",
            tool_calls=[{"tool": "add_task", "arguments": {"title": "fix the login"}}]
        )

    router.invoke_chat = mock_invoke_chat
    res = router.route_message("/task fix the login")
    assert res.success is True
    assert "fix the login" in res.output


def test_multi_action_response(fresh_env):
    _, _, router = fresh_env

    def mock_invoke_chat(sys_prompt, msgs, tools=None):
        return LLMResponse(
            content="Thinking context here",
            tool_calls=[
                {"tool": "reply_to_user", "arguments": {"message": "done1"}},
                {"tool": "reply_to_user", "arguments": {"message": "done2"}}
            ]
        )

    router.invoke_chat = mock_invoke_chat
    res = router.route_message("hello")
    assert res.success is True
    assert "done1" in res.output
    assert "done2" in res.output


def test_unknown_tool_returns_error(fresh_env):
    _, _, router = fresh_env

    def mock_invoke_chat(sys_prompt, msgs, tools=None):
        return LLMResponse(
            content="",
            tool_calls=[{"tool": "nonexistent_tool", "arguments": {}}]
        )

    router.invoke_chat = mock_invoke_chat
    res = router.route_message("do something weird")
    assert "not found" in res.output.lower()


def test_normal_routing_calls_llm(fresh_env):
    _, _, router = fresh_env
    routing_called = []

    def mock_invoke_chat(sys_prompt, msgs, tools=None):
        routing_called.append(True)
        return LLMResponse(
            content="",
            tool_calls=[{"tool": "reply_to_user", "arguments": {"message": "Grug here!"}}]
        )

    router.invoke_chat = mock_invoke_chat
    res = router.route_message("hello grug")
    assert len(routing_called) == 1
    assert res.success is True


def test_router_uses_agent_container_registry(fresh_env):
    """When an AgentContainer is passed, the router uses its scoped registry."""
    _, _registry, router = fresh_env
    from core.registry import ToolRegistry
    from core.agents import AgentContainer

    scoped = ToolRegistry()
    captured = {}

    def fake_reply(message):
        captured["message"] = message
        return message

    scoped.register_python_tool(
        name="reply_to_user",
        schema={"type": "object", "properties": {"message": {"type": "string"}}, "required": ["message"]},
        func=fake_reply,
        category="SYSTEM",
    )
    container = AgentContainer(
        name="test_agent",
        worker=None,
        base_prompt="",
        registry=scoped,
    )

    captured_tools = {}

    def mock_invoke_chat(sys_prompt, msgs, tools=None):
        captured_tools["tools"] = tools
        return LLMResponse(
            content="",
            tool_calls=[{"tool": "reply_to_user", "arguments": {"message": "scoped reply"}}],
        )

    router.invoke_chat = mock_invoke_chat
    res = router.route_message("hello", agent_container=container)
    assert res.success is True
    assert captured["message"] == "scoped reply"
    # Only the scoped registry's schema should reach the LLM
    tool_names = [s["function"]["name"] for s in captured_tools["tools"]]
    assert tool_names == ["reply_to_user"]


def test_router_respects_cancel_event(fresh_env):
    """A set cancel_event aborts the step loop before any LLM call."""
    import threading as _t
    _, _, router = fresh_env

    called = []
    router.invoke_chat = lambda *a, **kw: (called.append(True) or LLMResponse(content="", tool_calls=[]))
    ev = _t.Event()
    ev.set()
    res = router.route_message("hello", cancel_event=ev)
    assert res.success is False
    assert res.output == "Task cancelled"
    assert called == []


def test_routing_handles_prefixed_messages(fresh_env):
    _, _, router = fresh_env

    def mock_invoke_chat(sys_prompt, msgs, tools=None):
        return LLMResponse(
            content="",
            tool_calls=[{"tool": "reply_to_user", "arguments": {"message": "Grug know fire hot!"}}]
        )

    router.invoke_chat = mock_invoke_chat
    res = router.route_message("remember that fire is hot")
    assert res.success is True


def test_request_state_cleared_on_exception(fresh_env):
    """The request_state context manager must clear threadlocals on exception."""
    _, _, router = fresh_env
    rs = router._request_state

    try:
        with router.request_state(session_id="s1", user_id="u1", channel_id="c1",
                                  on_result=lambda _: None):
            assert rs._dispatch_session_id == "s1"
            raise RuntimeError("boom")
    except RuntimeError:
        pass

    assert rs._dispatch_session_id is None
    assert rs._dispatch_user_id is None
    assert rs._schedule_channel is None
    assert rs._schedule_user is None
    assert rs._schedule_thread_ts is None
    assert rs._dispatch_on_result is None
