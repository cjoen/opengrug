# Dispatch Rules, Response Rules, and Dispatch Eval Harness

**Date:** 2026-09-22

## Overview

This design adds three features to the agent loop.

1. **Dispatch rules** — Each tool specifies its target agent and whether the Dispatcher can call it directly. These rules replace the static route hints in `prompts/dispatcher.md` with rules from the registry.
2. **Response rules** — Each tool specifies a short prompt fragment. The router injects the fragment into the system prompt only when that tool executes. The fragment does not change the global system prompt.
3. **Dispatch eval harness** — A new eval harness that tests `dispatcher.classify()` decisions in isolation. Use the harness to tune the model and measure dispatcher accuracy.

This design does not include escalating compute or background review.

---

## Section 1: Data Model — Tool Registration

### Registration changes

Add three optional fields to `register_python_tool` and `register_cli_tool`:

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

All three fields are optional. Each field has a safe default value (`None`, `False`, `None`). Existing `register_tools()` calls do not need modification.

### Internal storage

Expand the existing 5-element tuple `(schema, func, destructive, friendly_name, category)` to an 8-element tuple:

```
(schema, func, destructive, friendly_name, category, dispatch_to, dispatcher_direct, response_rules)
```

`create_scoped()` copies the new fields with the existing fields. No special handling is required.

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

Add an optional `registry` parameter to `Dispatcher.__init__`. Rename `_load_prompt()` to `_build_prompt()`. The new `_build_prompt()` function does the following:

1. Read `prompts/dispatcher.md` as the base prompt. This file does not change and remains editable.
2. If `registry` is present, append a generated routing rules block to the base prompt:

```
## Routing Rules

chat_agent: add_note, get_recent_notes, query_memory, search, add_task, list_tasks
expert_agent: run_code, git_commit

Direct tools (call these yourself, no agent needed):
- get_health() — Check system health
- list_schedules() — List scheduled tasks
```

If `registry=None`, `_build_prompt()` returns the static file contents without changes. This preserves backward compatibility.

### Single LLM call, two response shapes

The Dispatcher passes the `dispatcher_direct` tool schemas as `tools` in the chat call. The Dispatcher includes the routing prompt in the same call. The LLM response has one of two shapes:

- **JSON content** → The `_parse()` function uses the existing behavior to populate `agent`, `context`, and `plan`.
- **Tool call** → The system populates `direct_tool` and `direct_args`. The `agent` field defaults to `fallback_agent`.

### Orchestrator handles `direct_tool`

Add a `direct_tool: str | None` field and a `direct_args: dict | None` field to `Task`. The `_classify_and_enqueue` function sets these fields from the `DispatchDecision`. Add a new branch in `_run_task` parallel to the existing `scheduled_tool` branch at line 184. This branch checks `task.direct_tool` and calls `registry.execute()` directly. It returns a `MessageReply`.

### Files changed
- `core/dispatcher.py` — `DispatchDecision`, `__init__`, `_build_prompt`, `_parse`
- `core/task.py` — `direct_tool` and `direct_args` fields
- `core/orchestrator.py` — `_classify_and_enqueue`, `_run_task` direct-tool branch
- `app.py` — pass `registry` to `Dispatcher` constructor

---

## Section 3: Response Rules Injection

### How it works

When a tool executes in `route_message`, the router collects the `response_rules` for that tool. The router appends the rules to the system prompt before the reply-step LLM call. The augmented system prompt applies to that step only. It does not persist into session history.

```
Step 1: LLM → selects add_note → tool executes → result appended to history
Step 2: LLM called for reply
        ← system_prompt + "\n\nResponse guidance: Confirm in one short line..."
```

### Single-step path

The `chat_agent` currently uses `max_steps=1`. In this mode, tool selection and the reply occur in the same LLM call. When a tool executes and has `response_rules`, the router adds one reply step. The router uses the augmented system prompt for that step. This is equivalent to `max_steps=2` for that turn only.

If no tool executes, or if the tool has no `response_rules`, the single-step behavior does not change.

### Implementation in `route_message`

After `_parse_and_execute` returns a non-None `tool_output`, do the following steps:

1. Collect the tool names from `llm_response.tool_calls`.
2. Look up the `response_rules` for each tool with `active_registry.get_response_rules(name)`.
3. If any rules exist, build `augmented_system_prompt = system_prompt + "\n\n## Response Guidance\n" + joined_rules`.
4. Use `augmented_system_prompt` for the next `_invoke` call only.

### Files changed
- `core/router.py` — `route_message` reply-step augmentation

---

## Section 4: Dispatch Eval Harness

### Golden dataset

The `evals/dispatch_golden_dataset.jsonl` file uses the same JSONL format as `evals/golden_dataset.jsonl`. It includes these dispatch-specific fields:

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

The harness follows the same structure and CLI flags as `run_evals.py`. These flags are: `--filter`, `--category`, `--repeat`, `--output`. The harness calls `dispatcher.classify()` once for each test case. The harness does not use a router or a step loop. For each test case, the harness verifies that the Dispatcher routed to the expected agent and called the expected direct tool.

The `--output` flag writes a JSON report. The report contains the model name, provider, timestamp, and the result for each test case. Use the report to compare accuracy across models without changing any other code.

### Files changed
- `evals/run_dispatch_evals.py` — new file
- `evals/dispatch_golden_dataset.jsonl` — new file (initially populated with cases covering each agent and each `dispatcher_direct` tool)

---

## Summary of Files Changed

| File | Change |
|------|--------|
| `core/registry.py` | Tuple expansion, new accessors, `create_scoped` update |
| `core/dispatcher.py` | `DispatchDecision`, `_build_prompt`, `_parse` |
| `core/task.py` | `direct_tool` and `direct_args` fields |
| `core/orchestrator.py` | Direct-tool branch in `_run_task` |
| `core/router.py` | Response rules injection in `route_message` |
| `app.py` | Pass `registry` to `Dispatcher` |
| `tools/*.py` | Add dispatch/response fields where appropriate |
| `evals/run_dispatch_evals.py` | New harness |
| `evals/dispatch_golden_dataset.jsonl` | New golden dataset |
