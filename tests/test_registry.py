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
