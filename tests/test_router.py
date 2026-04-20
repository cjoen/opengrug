"""Tests for GrugRouter: routing, multi-action, unknown tools."""

from core.registry import load_prompt_files
from core.interfaces import LLMResponse


def test_graceful_offline_degradation(fresh_env):
    _, _, router = fresh_env
    base_prompt = load_prompt_files("prompts")

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
