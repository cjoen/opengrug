# Dispatch Rules, Response Rules, and Dispatch Eval Harness

**Date:** 2026-09-22

## Overview

Three co-located features that make the agent loop smarter and measurable:

1. **Dispatch rules** — each tool declares where it should be routed and whether the Dispatcher can execute it directly, replacing the static `prompts/dispatcher.md` routing hints with registry-driven ones.
2. **Response rules** — each tool declares a short prompt fragment injected only when it fires, governing reply tone and length without polluting the global system prompt.
3. **Dispatch eval harness** — a parallel eval harness to `evals/run_evals.py` that tests `dispatcher.classify()` decisions in isolation, enabling model-level tuning of dispatcher accuracy.

Escalating compute and background review are explicitly out of scope.

---

## Section 1: Data Model — Tool Registration

### Registration changes

`register_python_tool` and `register_cli_tool` gain three optional fields:

```python
registry.register_python_tool(
    name="add_note",
    schema={...},
    func=...,
    # existing
    destructive=False,
    friendly_name="Save a note",
    category="NOTES",
    # new
    dispatch_to="chat_agent",
    dispatcher_direct=False,
    response_rules="Confirm in one short line that the note was saved.",
)
```

All three fields are optional and default to safe values (`None`, `False`, `None`), so every existing `register_tools()` call works without modification.

### Internal storage

The existing 5-element tuple `(schema, func, destructive, friendly_name, category)` becomes an 8-element tuple:

```
(schema, func, destructive, friendly_name, category, dispatch_to, dispatcher_direct, response_rules)
```

`create_scoped()` copies new fields alongside existing ones — no special handling needed.

### New accessor methods on `ToolRegistry`

```python
registry.get_dispatch_to(tool_name) → str | None
registry.get_response_rules(tool_name) → str | None
registry.get_dispatcher_direct_tools() → list[str]
```

### Files changed
- `core/registry.py` — tuple expansion, new accessors, `create_scoped` update
- All `tools/*.py` `register_tools()` functions — add fields where appropriate (optional)

---

## Section 2: Dispatcher Changes

### `DispatchDecision` extended

```python
@dataclass
class DispatchDecision:
    agent: str
    context: str
    plan: Optional[list[str]] = None
    direct_tool: Optional[str] = None   # tool to execute directly (no agent)
    direct_args: Optional[dict] = None  # arguments for direct execution
```

### Dynamic routing prompt

`Dispatcher.__init__` gains an optional `registry` parameter. `_load_prompt()` becomes `_build_prompt()`:

1. Reads `prompts/dispatcher.md` as the base (unchanged, still hand-editable)
2. If `registry` is provided, appends a generated routing rules block:

```
## Routing Rules

chat_agent: add_note, get_recent_notes, query_memory, search, add_task, list_tasks
expert_agent: run_code, git_commit

Direct tools (call these yourself, no agent needed):
- get_health() — Check system health
- list_schedules() — List scheduled tasks
```

If `registry=None`, `_build_prompt()` returns the static file contents unchanged — full backward compatibility.

### Single LLM call, two response shapes

The Dispatcher passes `dispatcher_direct` tool schemas as `tools` in the chat call alongside the routing prompt. The response is either:

- **JSON content** → existing `_parse()` behavior, populates `agent`/`context`/`plan`
- **Tool call** → `direct_tool` and `direct_args` are populated; `agent` defaults to `fallback_agent`

### Orchestrator handles `direct_tool`

`Task` gains a `direct_tool: str | None` and `direct_args: dict | None` field, populated by `_classify_and_enqueue` from the `DispatchDecision`. A new branch in `_run_task` (parallel to the existing `scheduled_tool` short-circuit at line 184) checks `task.direct_tool` and executes via `registry.execute()` directly, returning a `MessageReply`.

### Files changed
- `core/dispatcher.py` — `DispatchDecision`, `__init__`, `_build_prompt`, `_parse`
- `core/task.py` — `direct_tool` and `direct_args` fields
- `core/orchestrator.py` — `_classify_and_enqueue`, `_run_task` direct-tool branch
- `app.py` — pass `registry` to `Dispatcher` constructor

---

## Section 3: Response Rules Injection

### How it works

After a tool fires in `route_message`, before the reply-step LLM call, the router collects `response_rules` for each fired tool and appends them to the system prompt for that step only. The augmented prompt does not persist into session history.

```
Step 1: LLM → selects add_note → tool executes → result appended to history
Step 2: LLM called for reply
        ← system_prompt + "\n\nResponse guidance: Confirm in one short line..."
```

### Single-step path

`chat_agent` currently uses `max_steps=1`, where tool selection and reply happen in the same LLM call. When a fired tool has `response_rules`, the router performs one additional reply step with the augmented system prompt (effectively `max_steps=2` for that turn only).

If no tool fires, or if the fired tool has no `response_rules`, single-step behavior is unchanged.

### Implementation in `route_message`

After `_parse_and_execute` returns a non-None `tool_output`:

1. Collect tool names from `llm_response.tool_calls`
2. Look up `response_rules` for each via `active_registry.get_response_rules(name)`
3. If any rules exist, build `augmented_system_prompt = system_prompt + "\n\n## Response Guidance\n" + joined_rules`
4. Use `augmented_system_prompt` for the next `_invoke` call only

### Files changed
- `core/router.py` — `route_message` reply-step augmentation

---

## Section 4: Dispatch Eval Harness

### Golden dataset

`evals/dispatch_golden_dataset.jsonl` — same JSONL format as `evals/golden_dataset.jsonl`, with dispatch-specific fields:

```jsonl
{
  "session_id": "dispatch-001",
  "category": "NOTES",
  "messages": [{"role": "user", "content": "save a note about the standup"}],
  "expected_agent": "chat_agent",
  "expected_direct_tool": null
}
{
  "session_id": "dispatch-002",
  "category": "HEALTH",
  "messages": [{"role": "user", "content": "how are you doing?"}],
  "expected_agent": null,
  "expected_direct_tool": "get_health"
}
```

### Harness (`evals/run_dispatch_evals.py`)

Mirrors `run_evals.py` in structure and CLI flags (`--filter`, `--category`, `--repeat`, `--output`). Key difference: calls `dispatcher.classify()` once per case — no router, no step loop.

Each case checks:
- Did the Dispatcher route to the expected agent?
- Or call the expected direct tool?

`--output` writes a JSON report with model name, provider, timestamp, and per-case results — enabling side-by-side accuracy comparisons across models without changing any other code.

### Files changed
- `evals/run_dispatch_evals.py` — new file
- `evals/dispatch_golden_dataset.jsonl` — new file (initially populated with cases covering each agent and each `dispatcher_direct` tool)

---

## Summary of Files Changed

| File | Change |
|------|--------|
| `core/registry.py` | Tuple expansion, new accessors, `create_scoped` update |
| `core/dispatcher.py` | `DispatchDecision`, `_build_prompt`, `_parse` |
| `core/orchestrator.py` | Direct-tool branch in `_run_task` |
| `core/router.py` | Response rules injection in `route_message` |
| `app.py` | Pass `registry` to `Dispatcher` |
| `tools/*.py` | Add dispatch/response fields where appropriate |
| `evals/run_dispatch_evals.py` | New harness |
| `evals/dispatch_golden_dataset.jsonl` | New golden dataset |
