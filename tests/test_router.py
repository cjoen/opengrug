"""Tests for GrugRouter: routing, confidence, multi-action, thinking blocks."""

import json as _json
from core.registry import load_prompt_files


def test_graceful_offline_degradation(fresh_env):
    _, _, router = fresh_env
    base_prompt = load_prompt_files("prompts")

    router.invoke_chat = lambda sys_prompt, msgs: _json.dumps({
        "tool": "ask_for_clarification",
        "arguments": {"reason_for_confusion": "Grug brain foggy. Ollama not responding."},
        "confidence_score": 0
    })
    res = router.route_message("Explain quantum mechanics.", system_prompt=base_prompt)
    assert res.success is True
    assert "Grug" in res.output


def test_low_confidence_returns_clarification(fresh_env):
    _, _, router = fresh_env
    base_prompt = load_prompt_files("prompts")

    router.invoke_chat = lambda sys_prompt, msgs: (
        '{"confidence_score": 3, "tool": "add_note", "arguments": {"content": "unsure"}}'
    )
    res = router.route_message("Complex query", system_prompt=base_prompt)
    assert res.success is True
    assert "not sure" in res.output.lower() or "grug" in res.output.lower()


def test_missing_confidence_defaults_low(fresh_env):
    _, _, router = fresh_env
    base_prompt = load_prompt_files("prompts")

    router.invoke_chat = lambda sys_prompt, msgs: '{"tool": "add_note", "arguments": {"content": "hi"}}'
    res = router.route_message("hello", system_prompt=base_prompt)
    assert "not sure" in res.output.lower() or "grug" in res.output.lower()


def test_high_confidence_executes_tool(fresh_env):
    storage, _, router = fresh_env
    base_prompt = load_prompt_files("prompts")

    router.invoke_chat = lambda sys_prompt, msgs: (
        '{"confidence_score": 9, "tool": "add_note", "arguments": {"content": "High confidence note."}}'
    )
    res = router.route_message("Store: High confidence note.", system_prompt=base_prompt)
    assert res.success is True
    assert "not sure" not in res.output.lower()


def test_confidence_at_threshold_triggers_clarification(fresh_env):
    _, _, router = fresh_env
    base_prompt = load_prompt_files("prompts")

    router.invoke_chat = lambda sys_prompt, msgs: (
        '{"confidence_score": 4, "tool": "add_note", "arguments": {"content": "maybe"}}'
    )
    res = router.route_message("Um, something about notes?", system_prompt=base_prompt)
    assert res.success is True
    assert "not sure" in res.output.lower()
    assert "tell grug which" in res.output.lower()


def test_low_confidence_notes_tool_shows_note_options(fresh_env):
    _, _, router = fresh_env
    base_prompt = load_prompt_files("prompts")

    router.invoke_chat = lambda sys_prompt, msgs: (
        '{"confidence_score": 2, "tool": "add_note", "arguments": {"content": "?"}}'
    )
    res = router.route_message("Do a note thing", system_prompt=base_prompt)
    assert res.success is True
    assert "save a note" in res.output.lower() or "search old notes" in res.output.lower()


def test_low_confidence_tasks_tool_shows_task_options(fresh_env):
    _, registry, router = fresh_env
    registry.register_python_tool(
        name="add_task",
        schema={
            "type": "object",
            "properties": {"title": {"type": "string"}},
            "required": ["title"],
        },
        func=lambda title: f"added task: {title}",
        category="TASKS",
    )
    base_prompt = load_prompt_files("prompts")

    router.invoke_chat = lambda sys_prompt, msgs: (
        '{"confidence_score": 1, "tool": "add_task", "arguments": {"title": "?"}}'
    )
    res = router.route_message("Do a task thing", system_prompt=base_prompt)
    assert res.success is True
    assert "add a task" in res.output.lower() or "complete a task" in res.output.lower()


def test_ask_for_clarification_bypasses_threshold(fresh_env):
    _, _, router = fresh_env
    base_prompt = load_prompt_files("prompts")

    router.invoke_chat = lambda sys_prompt, msgs: (
        '{"confidence_score": 0, "tool": "ask_for_clarification", '
        '"arguments": {"reason_for_confusion": "Grug need more info."}}'
    )
    res = router.route_message("Something vague", system_prompt=base_prompt)
    assert res.success is True
    assert "Grug confused" in res.output or "Grug need more info" in res.output


def test_reply_to_user_bypasses_threshold(fresh_env):
    _, _, router = fresh_env
    base_prompt = load_prompt_files("prompts")

    router.invoke_chat = lambda sys_prompt, msgs: (
        '{"confidence_score": 0, "tool": "reply_to_user", '
        '"arguments": {"message": "Grug here to help!"}}'
    )
    res = router.route_message("Hello", system_prompt=base_prompt)
    assert res.success is True
    assert "Grug here to help" in res.output


def test_prefixed_message_routes_through_llm(fresh_env):
    _, _, router = fresh_env
    invocations = []

    def mock_invoke_chat(sys_prompt, msgs):
        invocations.append(True)
        return '{"tool": "add_note", "arguments": {"content": "fire is hot"}, "confidence_score": 10}'

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

    def mock_invoke_chat(sys_prompt, msgs):
        return '{"tool": "add_task", "arguments": {"title": "fix the login"}, "confidence_score": 10}'

    router.invoke_chat = mock_invoke_chat
    res = router.route_message("/task fix the login")
    assert res.success is True
    assert "fix the login" in res.output


def test_multi_action_response(fresh_env):
    _, _, router = fresh_env

    def mock_invoke_chat(sys_prompt, msgs):
        return '{"thinking": "two actions", "actions": [{"tool": "reply_to_user", "arguments": {"message": "done"}, "confidence_score": 10}]}'

    router.invoke_chat = mock_invoke_chat
    res = router.route_message("hello")
    assert res.success is True
    assert "done" in res.output


def test_unknown_tool_returns_error(fresh_env):
    _, _, router = fresh_env

    def mock_invoke_chat(sys_prompt, msgs):
        return '{"tool": "nonexistent_tool", "arguments": {}, "confidence_score": 10}'

    router.invoke_chat = mock_invoke_chat
    res = router.route_message("do something weird")
    assert "not found" in res.output.lower()


def test_normal_routing_calls_llm(fresh_env):
    _, _, router = fresh_env
    routing_called = []

    def mock_invoke_chat(sys_prompt, msgs):
        routing_called.append(True)
        return '{"tool": "reply_to_user", "arguments": {"message": "Grug here!"}, "confidence_score": 10}'

    router.invoke_chat = mock_invoke_chat
    res = router.route_message("hello grug")
    assert len(routing_called) == 1
    assert res.success is True


def test_routing_handles_prefixed_messages(fresh_env):
    _, _, router = fresh_env

    def mock_invoke_chat(sys_prompt, msgs):
        return '{"tool": "reply_to_user", "arguments": {"message": "Grug know fire hot!"}, "confidence_score": 10}'

    router.invoke_chat = mock_invoke_chat
    res = router.route_message("remember that fire is hot")
    assert res.success is True


def test_thinking_block_stripped_before_parse(fresh_env):
    _, _, router = fresh_env
    thinking_response = (
        '<|channel>thought\nUser wants to add a note about fire.\n<channel|>'
        '{"confidence_score": 10, "tool": "add_note", "arguments": {"content": "fire is hot"}}'
    )
    router.invoke_chat = lambda sys_prompt, msgs: thinking_response
    res = router.route_message("remember fire is hot", system_prompt=load_prompt_files("prompts"))
    assert res.success is True


def test_no_thinking_block_still_parses(fresh_env):
    _, _, router = fresh_env
    plain_response = '{"confidence_score": 10, "tool": "add_note", "arguments": {"content": "plain note"}}'
    router.invoke_chat = lambda sys_prompt, msgs: plain_response
    res = router.route_message("remember plain note", system_prompt=load_prompt_files("prompts"))
    assert res.success is True
