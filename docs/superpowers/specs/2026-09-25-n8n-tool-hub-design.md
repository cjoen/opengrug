# n8n Tool Hub Integration — Design Spec

Date: 2026-09-25

## Overview

Integrate n8n as a tool hub for Grug. Each active n8n workflow tagged `grug-tool` becomes a first-class tool in Grug's `ToolRegistry`. Tools are discovered at startup via n8n's REST API and can be reloaded on demand via a Slack command — no code changes or restart required to add, remove, or modify tools.

## Goals

- Add and configure Grug tools through n8n's UI, without writing Python
- Enable/disable tools by activating/deactivating n8n workflows
- Control access per tool: `read_only: true` bypasses HITL gate; `read_only: false` requires human approval before execution
- Reload the tool registry from Slack without restarting Grug

## Non-Goals

- Using n8n's MCP server as the execution layer (webhook approach chosen for edge model accuracy — Gemma4 handles named first-class tools better than generic `execute_workflow(id=...)` calls)
- Automatic background polling for tool changes (on-demand reload is sufficient)
- Per-user or per-session tool access control

## Architecture

```
grug_config.json
  └─ "n8n": { base_url, api_key, tag, timeout_seconds }

N8nToolLoader (tools/n8n.py)
  ├─ load()             — discover workflows from n8n API, register tools
  ├─ reload()           — remove owned tools, call load() again, return Slack reply
  └─ _owned_names: set  — tracks tool names registered by this loader

ToolRegistry (core/registry.py)
  └─ remove_tool(name)  — new method; removes from _python_tools or _cli_tools

GrugConfig (core/config.py)
  └─ self.n8n: SimpleNamespace | None — None when "n8n" key absent from config file

app.py
  └─ if config.n8n:
       _n8n_loader = N8nToolLoader(registry, config.n8n)
       _n8n_loader.load()
       register "reload_n8n_tools" system tool → calls _n8n_loader.reload()
```

## n8n Workflow Setup

Each n8n workflow that should be available as a Grug tool must:

1. Have a **Webhook trigger node** as its entry point
2. Be **active** in n8n
3. Have a **tag** matching `config.n8n.tag` (default: `grug-tool`)
4. Have a **description field** containing a JSON block under the `"grug"` key:

```json
{
  "grug": {
    "name": "send_email",
    "description": "Send an email to a recipient",
    "webhook_path": "send-email",
    "read_only": false,
    "category": "COMMS",
    "parameters": {
      "to":      { "type": "string", "description": "Recipient email address" },
      "subject": { "type": "string", "description": "Email subject line" },
      "body":    { "type": "string", "description": "Email body text" }
    },
    "required": ["to", "subject", "body"]
  }
}
```

Fields:

| Field | Required | Description |
|-------|----------|-------------|
| `name` | yes | Tool name in ToolRegistry — must be unique across all tools |
| `description` | yes | Shown to the LLM; should clearly describe what the tool does and when to use it |
| `webhook_path` | yes | Path suffix: webhook URL is `{base_url}/webhook/{webhook_path}` |
| `read_only` | yes | `true` → `destructive=False` (no HITL); `false` → `destructive=True` (HITL required) |
| `category` | no | Tool category shown in routing prompts (default: `"N8N"`) |
| `parameters` | no | JSON Schema properties object for tool arguments (default: no parameters) |
| `required` | no | List of required parameter names |

## N8nToolLoader

### Class interface

```python
class N8nToolLoader:
    def __init__(self, registry: ToolRegistry, n8n_cfg):
        self._registry = registry
        self._base_url = n8n_cfg.base_url.rstrip("/")
        self._api_key = n8n_cfg.api_key
        self._tag = getattr(n8n_cfg, "tag", "grug-tool")
        self._timeout = getattr(n8n_cfg, "timeout_seconds", 10)
        self._owned_names: set[str] = set()

    def load(self) -> int:
        """Discover active tagged workflows and register tools. Returns count registered."""

    def reload(self) -> str:
        """Remove owned tools, re-discover, return Slack-ready summary string."""
```

### Discovery flow (`load`)

1. `GET {base_url}/api/v1/workflows?active=true&tags={tag}` with `X-N8N-API-KEY: {api_key}` header
2. For each workflow, attempt to parse `workflow["description"]` as JSON and extract `["grug"]` block
3. Skip (with warning log) if:
   - Description is missing or not valid JSON
   - No `"grug"` key in the parsed JSON
   - Missing required fields (`name`, `webhook_path`, `description`)
   - `name` already in `_owned_names` (duplicate across n8n workflows — first one wins)
   - `name` already registered in the registry by a non-n8n tool (builtin conflict — n8n tool is skipped, builtin is preserved)
4. Register a Python tool in `ToolRegistry`:
   - `name`: from grug block
   - `schema`: built from `parameters`, `required`, `description`
   - `func`: `partial(_call_webhook, base_url, webhook_path, timeout)`
   - `destructive`: `not read_only`
   - `category`: from grug block (default `"N8N"`)
   - `friendly_name`: same as `name`
5. Add name to `_owned_names`
6. Return count of successfully registered tools

### Webhook executor (`_call_webhook`)

- HTTP POST to `{base_url}/webhook/{webhook_path}` with tool arguments as JSON body
- Sets `Content-Type: application/json`
- Returns response body as a plain string (consistent with other tool return values)
- On non-2xx HTTP status: returns `"n8n error {status}: {body}"` (no exception raised)
- On connection error or timeout: returns `"n8n tool unavailable: {reason}"` (no exception raised)

n8n webhooks do not require authentication by default; the API key is only used for the discovery call, not webhook execution.

### Reload flow

1. For each name in `_owned_names`: call `registry.remove_tool(name)`
2. Clear `_owned_names`
3. Call `load()`
4. Return `"Reloaded n8n tools: {count} registered."` on success, or error summary if n8n is unreachable

## ToolRegistry Changes

Add `remove_tool(name: str) -> bool` to `core/registry.py`:

```python
def remove_tool(self, name: str) -> bool:
    if name in self._python_tools:
        del self._python_tools[name]
        return True
    if name in self._cli_tools:
        del self._cli_tools[name]
        return True
    return False
```

This is safe to call mid-session: scoped registries snapshot at agent-run start, so in-flight requests are unaffected.

## Config Changes

### grug_config.json

Add an optional `"n8n"` section. Omitting it entirely disables the loader:

```json
"n8n": {
  "base_url": "http://localhost:5678",
  "api_key": "your-n8n-api-key",
  "tag": "grug-tool",
  "timeout_seconds": 10
}
```

### core/config.py

Add to `GrugConfig.__init__` (after the namespace conversion):

```python
self.n8n = getattr(ns, 'n8n', None)
```

No entry in `_DEFAULTS` — the section is opt-in and has no meaningful defaults without real credentials.

## app.py Changes

After the existing tool registration block:

```python
_n8n_loader = None
if getattr(config, 'n8n', None):
    from tools.n8n import N8nToolLoader
    _n8n_loader = N8nToolLoader(registry, config.n8n)
    _n8n_loader.load()
    registry.register_python_tool(
        name="reload_n8n_tools",
        schema={
            "description": "[SYSTEM] Reload Grug's n8n tool registry. Use when n8n workflows have been added, changed, or removed.",
            "type": "object",
            "properties": {},
        },
        func=_n8n_loader.reload,
        category="SYSTEM",
        friendly_name="Reload n8n tools",
    )
```

## Unit Tests (tests/test_n8n.py)

All tests use `unittest.mock.patch` to stub HTTP calls. No live n8n instance required.

| Test | What it covers |
|------|---------------|
| `test_load_registers_tools` | Happy path: two mock workflows → two tools registered in registry |
| `test_load_skips_no_grug_block` | Workflow with no `"grug"` key → skipped, nothing registered |
| `test_load_skips_parse_error` | Description is not valid JSON → skipped gracefully, no exception |
| `test_load_skips_missing_required_fields` | Grug block missing `webhook_path` → skipped |
| `test_load_skips_duplicate_name` | Two workflows with same `name` → second skipped, warning logged |
| `test_load_skips_builtin_name_conflict` | n8n workflow name matches existing builtin → n8n tool skipped, builtin preserved |
| `test_read_only_false_maps_to_destructive` | `read_only: false` → `registry.is_destructive(name)` returns `True` |
| `test_read_only_true_maps_to_not_destructive` | `read_only: true` → `registry.is_destructive(name)` returns `False` |
| `test_webhook_executor_success` | POST returns 200 → result is response body text |
| `test_webhook_executor_http_error` | POST returns 500 → result is error string, no exception |
| `test_webhook_executor_timeout` | POST times out → result is timeout string, no exception |
| `test_reload_removes_stale_tools` | After reload, tools from removed workflows are gone |
| `test_reload_picks_up_new_tool` | After reload, newly added workflow appears as registered tool |
| `test_remove_tool_python` | `registry.remove_tool(name)` removes from `_python_tools` |
| `test_remove_tool_cli` | `registry.remove_tool(name)` removes from `_cli_tools` |
| `test_remove_tool_missing` | `registry.remove_tool("nonexistent")` returns `False`, no crash |
| `test_n8n_disabled_when_no_config` | `config.n8n = None` → loader not initialized, no crash at startup |

## Eval Harness

### Fixture file: `evals/n8n_workflows_fixture.json`

A JSON array of mock workflow objects in n8n API response format. Provides a representative set of tools for dry-run evals without requiring a live n8n instance. Includes a mix of read-only and destructive tools across different domains to exercise semantic routing:

| Tool name | Domain | read_only | Tests |
|-----------|--------|-----------|-------|
| `send_email` | COMMS | false | destructive path, arg extraction |
| `get_weather` | INFO | true | read-only path, location arg |
| `create_calendar_event` | CALENDAR | false | destructive, time parsing |
| `search_notes` | MEMORY | true | distinguishes from built-in `search` tool |
| `create_task` | TASKS | false | distinguishes from built-in `add_task` tool |

### evals/run_evals.py changes

Add `_register_n8n_fixture_tools(registry)`:
- Loads `evals/n8n_workflows_fixture.json`
- Instantiates `N8nToolLoader` with a mock config (dummy base URL, no real API calls)
- Replaces the internal `_fetch_workflows()` call with the fixture data
- Registers all fixture tools into the eval registry alongside production tools

Call this from `_register_production_schemas()` when the fixture file exists (graceful skip if absent).

### evals/golden_dataset.jsonl additions

New cases tagged `"category": "N8N"`:

| session_id | Input | Expected tool | Intent tested |
|------------|-------|--------------|----------------|
| `n8n-001` | "send an email to sarah@example.com about the meeting tomorrow" | `send_email` | Right tool selected, `to` arg populated |
| `n8n-002` | "what's the weather like in Portland?" | `get_weather` | Read-only tool selected, location extracted |
| `n8n-003` | "schedule a meeting with the team for 3pm Friday" | `create_calendar_event` | Destructive tool, time args |
| `n8n-004` | "email john about the project status" | `send_email` | Tool selected even with partial/missing `to` address |
| `n8n-005` | "search my notes for last week's standup" | `search_notes` | Prefers n8n `search_notes` over built-in `search` |

### Running n8n evals

```bash
# Dry-run against default model
python evals/run_evals.py --category N8N

# Compare models
GRUG_MODEL=gemma4:grug  python evals/run_evals.py --category N8N --output n8n_gemma4.json
GRUG_MODEL=llama3.2     python evals/run_evals.py --category N8N --output n8n_llama32.json

# Flake detection
python evals/run_evals.py --category N8N --repeat 5
```

## Architectural Direction

The long-term intent is for n8n to handle the majority of tool calls. The builtin tool set will shrink over time to a minimal core — health checks, system diagnostics, and the `reload_n8n_tools` command itself. Everything else migrates to n8n workflows.

This design supports that trajectory:
- n8n tools coexist with builtins today with no changes required
- Builtins can be removed one at a time as equivalent n8n workflows are proven out
- The name collision guard ensures accidental overlaps are loud (logged warning) rather than silent

The minimal builtin set to preserve long-term: `grug_health`, `system_health`, `reload_n8n_tools`.

## File Summary

| File | Change |
|------|--------|
| `tools/n8n.py` | New — `N8nToolLoader`, `_call_webhook` |
| `core/registry.py` | Add `remove_tool(name)` method |
| `core/config.py` | Add `self.n8n = getattr(ns, 'n8n', None)` |
| `grug_config.json` | Add optional `"n8n"` section |
| `app.py` | Wire loader + `reload_n8n_tools` tool if `config.n8n` is set |
| `tests/test_n8n.py` | New — unit tests (16 cases) |
| `evals/n8n_workflows_fixture.json` | New — mock workflows for dry-run evals |
| `evals/run_evals.py` | Add `_register_n8n_fixture_tools()` |
| `evals/golden_dataset.jsonl` | Add 5 N8N-category test cases |
