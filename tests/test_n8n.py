"""Tests for N8nToolLoader and _call_webhook."""

import json
import pytest
import requests as real_requests
from types import SimpleNamespace
from unittest.mock import patch, MagicMock

from core.registry import ToolRegistry
from tools.n8n import N8nToolLoader, _call_webhook


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _wf(name, webhook_path, description, read_only=True, category="N8N",
        parameters=None, required=None):
    """Build a minimal n8n workflow dict with a grug description block."""
    grug = {
        "name": name,
        "webhook_path": webhook_path,
        "description": description,
        "read_only": read_only,
        "category": category,
    }
    if parameters:
        grug["parameters"] = parameters
    if required:
        grug["required"] = required
    return {"name": f"wf-{name}", "description": json.dumps({"grug": grug})}


def _loader(registry=None):
    """Return a loader with a dummy config."""
    if registry is None:
        registry = ToolRegistry()
    cfg = SimpleNamespace(
        base_url="http://n8n.test",
        api_key="test-key",
        tag="grug-tool",
        timeout_seconds=5,
    )
    return N8nToolLoader(registry, cfg)


def _load_with(loader, workflows):
    """Inject fixture workflows and call load()."""
    loader._fetch_workflows = lambda: workflows
    return loader.load()


# ---------------------------------------------------------------------------
# Discovery / registration
# ---------------------------------------------------------------------------

def test_load_registers_tools():
    registry = ToolRegistry()
    loader = _loader(registry)
    count = _load_with(loader, [
        _wf("tool_a", "path-a", "Tool A"),
        _wf("tool_b", "path-b", "Tool B"),
    ])
    assert count == 2
    assert "tool_a" in registry._python_tools
    assert "tool_b" in registry._python_tools


def test_load_skips_no_grug_block():
    registry = ToolRegistry()
    loader = _loader(registry)
    count = _load_with(loader, [{"name": "wf", "description": json.dumps({"other": "data"})}])
    assert count == 0
    assert len(registry._python_tools) == 0


def test_load_skips_parse_error():
    registry = ToolRegistry()
    loader = _loader(registry)
    count = _load_with(loader, [{"name": "wf", "description": "not json at all"}])
    assert count == 0


def test_load_skips_empty_description():
    registry = ToolRegistry()
    loader = _loader(registry)
    count = _load_with(loader, [{"name": "wf", "description": ""}])
    assert count == 0


def test_load_skips_none_description():
    registry = ToolRegistry()
    loader = _loader(registry)
    count = _load_with(loader, [{"name": "wf"}])  # no description key
    assert count == 0


def test_load_skips_missing_required_fields():
    registry = ToolRegistry()
    loader = _loader(registry)
    # webhook_path is missing
    grug = {"name": "tool_x", "description": "Tool X"}
    count = _load_with(loader, [{"name": "wf", "description": json.dumps({"grug": grug})}])
    assert count == 0


def test_load_skips_n8n_duplicate_name():
    registry = ToolRegistry()
    loader = _loader(registry)
    count = _load_with(loader, [
        _wf("tool_a", "path-a1", "Tool A first"),
        _wf("tool_a", "path-a2", "Tool A second"),
    ])
    assert count == 1


def test_load_skips_builtin_name_conflict():
    registry = ToolRegistry()
    registry.register_python_tool(
        name="add_note",
        schema={"description": "builtin", "type": "object", "properties": {}},
        func=lambda: "builtin result",
    )
    loader = _loader(registry)
    count = _load_with(loader, [_wf("add_note", "add-note", "n8n note tool")])
    assert count == 0
    # Builtin is preserved and still executes correctly
    assert registry._python_tools["add_note"][1]() == "builtin result"


def test_read_only_false_maps_to_destructive():
    registry = ToolRegistry()
    loader = _loader(registry)
    _load_with(loader, [_wf("nuke_db", "nuke-db", "Nukes the DB", read_only=False)])
    assert registry.is_destructive("nuke_db") is True


def test_read_only_true_maps_to_not_destructive():
    registry = ToolRegistry()
    loader = _loader(registry)
    _load_with(loader, [_wf("get_weather", "get-weather", "Gets weather", read_only=True)])
    assert registry.is_destructive("get_weather") is False


def test_fetch_workflows_handles_bare_list():
    """n8n API may return a bare list instead of {"data": [...]}."""
    registry = ToolRegistry()
    loader = _loader(registry)
    workflows = [_wf("tool_a", "path-a", "Tool A")]
    with patch("tools.n8n.requests") as mock_req:
        mock_req.get.return_value = MagicMock(
            ok=True,
            json=lambda: workflows,  # bare list, not {"data": [...]}
        )
        mock_req.get.return_value.raise_for_status = MagicMock()
        count = loader.load()
    assert count == 1


def test_fetch_workflows_returns_empty_on_http_error():
    registry = ToolRegistry()
    loader = _loader(registry)
    with patch("tools.n8n.requests") as mock_req:
        mock_req.get.side_effect = Exception("connection refused")
        count = loader.load()
    assert count == 0  # no crash, graceful empty


# ---------------------------------------------------------------------------
# Webhook executor
# ---------------------------------------------------------------------------

def test_webhook_success():
    with patch("tools.n8n.requests") as mock_req:
        mock_req.post.return_value = MagicMock(ok=True, text="email sent")
        mock_req.exceptions.Timeout = real_requests.exceptions.Timeout
        mock_req.exceptions.RequestException = real_requests.exceptions.RequestException
        result = _call_webhook("http://n8n.test", "send-email", 5, to="a@b.com", subject="hi")
    assert result == "email sent"


def test_webhook_http_error():
    with patch("tools.n8n.requests") as mock_req:
        mock_req.post.return_value = MagicMock(ok=False, status_code=500, text="internal error")
        mock_req.exceptions.Timeout = real_requests.exceptions.Timeout
        mock_req.exceptions.RequestException = real_requests.exceptions.RequestException
        result = _call_webhook("http://n8n.test", "bad-path", 5)
    assert "500" in result
    assert "internal error" in result


def test_webhook_timeout():
    with patch("tools.n8n.requests") as mock_req:
        mock_req.post.side_effect = real_requests.exceptions.Timeout()
        mock_req.exceptions.Timeout = real_requests.exceptions.Timeout
        mock_req.exceptions.RequestException = real_requests.exceptions.RequestException
        result = _call_webhook("http://n8n.test", "slow-path", 5)
    assert "timed out" in result.lower()


# ---------------------------------------------------------------------------
# Reload
# ---------------------------------------------------------------------------

def test_reload_removes_stale_tools():
    registry = ToolRegistry()
    loader = _loader(registry)
    _load_with(loader, [_wf("old_tool", "old", "Old tool")])
    assert "old_tool" in registry._python_tools

    loader._fetch_workflows = lambda: []
    loader.reload()
    assert "old_tool" not in registry._python_tools


def test_reload_picks_up_new_tool():
    registry = ToolRegistry()
    loader = _loader(registry)
    _load_with(loader, [_wf("tool_a", "path-a", "Tool A")])

    loader._fetch_workflows = lambda: [_wf("tool_b", "path-b", "Tool B")]
    result = loader.reload()

    assert "tool_a" not in registry._python_tools
    assert "tool_b" in registry._python_tools
    assert "1 registered" in result


def test_reload_returns_error_string_when_n8n_unreachable():
    registry = ToolRegistry()
    loader = _loader(registry)
    _load_with(loader, [_wf("tool_a", "path-a", "Tool A")])

    def _boom():
        raise ConnectionError("refused")

    loader._fetch_workflows = _boom
    result = loader.reload()
    # Must return a string, not raise
    assert isinstance(result, str)
    assert "tool_a" not in registry._python_tools  # old tools still removed before failure
