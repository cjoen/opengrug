"""Tests for ToolRegistry: schema validation, CLI tools, HITL gating."""

import os
import subprocess as _sp
from core.registry import ToolRegistry


def test_schema_validation_rejects_bad_args(fresh_env):
    _, registry, _ = fresh_env
    res = registry.execute("add_note", {"wrong_field": 1})
    assert res.success is False
    assert "Invalid args" in res.output


def test_hitl_requires_approval_populates_fields(fresh_env):
    _, registry, _ = fresh_env
    registry.register_python_tool(
        name="delete_note",
        schema={
            "type": "object",
            "properties": {"note_id": {"type": "integer"}},
            "required": ["note_id"],
        },
        func=lambda note_id: f"deleted {note_id}",
        destructive=True,
    )
    res = registry.execute("delete_note", {"note_id": 42})
    assert res.requires_approval is True
    assert res.tool_name == "delete_note"
    assert res.arguments == {"note_id": 42}


def test_cli_flag_injection_blocked():
    registry = ToolRegistry()
    registry.register_cli_tool(
        name="test_cli",
        schema={"type": "object", "properties": {"title": {"type": "string"}}, "required": ["title"]},
        base_command=["echo"],
        destructive=False,
    )
    res = registry.execute("test_cli", {"title": "--assignee=evil"})
    assert res.success is False
    assert "must not start with" in res.output


def test_subprocess_timeout():
    os.environ["GRUG_SUBPROCESS_TIMEOUT"] = "1"
    registry = ToolRegistry()
    registry.register_cli_tool(
        name="test_slow",
        schema={"type": "object", "properties": {}, "required": []},
        base_command=["bash", "-c", "sleep 60"],
        destructive=False,
    )
    res = registry.execute("test_slow", {})
    assert res.success is False
    assert "timed out" in res.output
    os.environ.pop("GRUG_SUBPROCESS_TIMEOUT", None)


def test_called_process_error_output_surfaced():
    registry = ToolRegistry()

    def failing_func():
        raise _sp.CalledProcessError(returncode=1, cmd=["test"], output="detailed error info")

    registry.register_python_tool(
        name="test_fail",
        schema={"type": "object", "properties": {}},
        func=failing_func,
    )
    res = registry.execute("test_fail", {})
    assert res.success is False
    assert "detailed error info" in res.output


def test_cli_tool_valid_args_produce_correct_argv():
    registry = ToolRegistry()
    registry.register_cli_tool(
        name="test_echo",
        schema={
            "type": "object",
            "properties": {"message": {"type": "string"}},
            "required": ["message"]
        },
        base_command=["echo"],
        destructive=False,
    )
    res = registry.execute("test_echo", {"message": "hello world"})
    assert res.success is True
    assert "hello world" in res.output


def test_cli_tool_schema_validation():
    registry = ToolRegistry()
    registry.register_cli_tool(
        name="test_cli",
        schema={
            "type": "object",
            "properties": {"count": {"type": "integer"}},
            "required": ["count"]
        },
        base_command=["echo"],
        destructive=False,
    )
    res = registry.execute("test_cli", {"count": "not_a_number"})
    assert res.success is False
    assert "Invalid args" in res.output


def test_destructive_cli_tool_gated_by_hitl():
    registry = ToolRegistry()
    registry.register_cli_tool(
        name="test_destroy",
        schema={"type": "object", "properties": {}},
        base_command=["rm"],
        destructive=True,
    )
    res = registry.execute("test_destroy", {})
    assert res.requires_approval is True
    assert res.tool_name == "test_destroy"


# New tests for dispatch and response rules

def _make_registry():
    r = ToolRegistry()
    r.register_python_tool(
        name="add_note",
        schema={"description": "save a note", "type": "object", "properties": {"content": {"type": "string"}}},
        func=lambda content: "saved",
        dispatch_to="chat_agent",
        dispatcher_direct=False,
        response_rules="Confirm in one short line.",
    )
    r.register_python_tool(
        name="get_health",
        schema={"description": "check health", "type": "object", "properties": {}},
        func=lambda: "ok",
        dispatcher_direct=True,
    )
    r.register_python_tool(
        name="no_rules_tool",
        schema={"description": "plain tool", "type": "object", "properties": {}},
        func=lambda: "done",
    )
    return r


def test_get_dispatch_to_returns_value():
    r = _make_registry()
    assert r.get_dispatch_to("add_note") == "chat_agent"


def test_get_dispatch_to_returns_none_when_unset():
    r = _make_registry()
    assert r.get_dispatch_to("no_rules_tool") is None


def test_get_dispatch_to_returns_none_for_unknown_tool():
    r = _make_registry()
    assert r.get_dispatch_to("ghost") is None


def test_get_response_rules_returns_value():
    r = _make_registry()
    assert r.get_response_rules("add_note") == "Confirm in one short line."


def test_get_response_rules_returns_none_when_unset():
    r = _make_registry()
    assert r.get_response_rules("no_rules_tool") is None


def test_get_dispatcher_direct_tools_lists_flagged():
    r = _make_registry()
    assert "get_health" in r.get_dispatcher_direct_tools()
    assert "add_note" not in r.get_dispatcher_direct_tools()


def test_get_direct_tool_schemas_returns_only_direct():
    r = _make_registry()
    schemas = r.get_direct_tool_schemas()
    names = [s["function"]["name"] for s in schemas]
    assert "get_health" in names
    assert "add_note" not in names


def test_create_scoped_copies_new_fields():
    r = _make_registry()
    scoped = r.create_scoped(["add_note", "get_health"])
    assert scoped.get_dispatch_to("add_note") == "chat_agent"
    assert scoped.get_response_rules("add_note") == "Confirm in one short line."
    assert "get_health" in scoped.get_dispatcher_direct_tools()


def test_register_without_new_fields_uses_safe_defaults():
    r = ToolRegistry()
    r.register_python_tool(
        name="plain",
        schema={"type": "object", "properties": {}},
        func=lambda: "ok",
    )
    assert r.get_dispatch_to("plain") is None
    assert r.get_response_rules("plain") is None
    assert "plain" not in r.get_dispatcher_direct_tools()


def test_remove_python_tool():
    registry = ToolRegistry()
    registry.register_python_tool(
        name="my_tool",
        schema={"description": "test", "type": "object", "properties": {}},
        func=lambda: "ok",
    )
    assert registry.remove_tool("my_tool") is True
    assert "my_tool" not in registry._python_tools


def test_remove_cli_tool():
    registry = ToolRegistry()
    registry.register_cli_tool(
        name="my_cli",
        schema={"description": "test", "type": "object", "properties": {}},
        base_command=["echo"],
    )
    assert registry.remove_tool("my_cli") is True
    assert "my_cli" not in registry._cli_tools


def test_remove_missing_tool_returns_false():
    registry = ToolRegistry()
    assert registry.remove_tool("nonexistent") is False
