# Tool Turn History — Implementation Plan

**Status:** ✅ Complete (2026-04-20)
**Created:** 2026-04-20
**Related:** `build-plan/bugs.md` Bug 3

## Problem

Session history stored the LLM's raw JSON plan as the assistant turn. Tool execution results were never saved. When the user referenced data from a previous turn ("mark the first one done", "cancel schedule #3"), the LLM had no idea what data was returned.

## Solution

Store three turns per exchange instead of two:

```
user: "list my tasks"
tool: "3: - Fix login [high]\n4: - Update docs [medium]"
assistant: "Here tasks!"
```

- **user** — what the user said
- **tool** — combined tool execution output (what actually happened)
- **assistant** — the `reply_to_user` message (what Grug said back, plain text)

The LLM's raw JSON plan is NOT stored in history. It's already captured by `storage.log_routing_trace()` for debugging.

## Completed Changes

### ✅ 1. Add `tool_output` field to `ToolExecutionResult` (`core/registry.py`)

Added `tool_output: Optional[str] = None`. The `llm_response` field was subsequently removed as dead code.

### ✅ 2. Separate tool results from reply text (`core/router.py`)

`_parse_and_execute` splits outputs into `tool_outputs` and `reply_outputs` buckets. `tool_output_combined` is set on the returned `ToolExecutionResult`.

### ✅ 3. Update session history storage (`core/orchestrator.py`)

Both `process_message` and `re_infer` now store 3-turn sequences:
```python
new_messages = session["messages"] + [{"role": "user", "content": text}]
if result.tool_output:
    new_messages.append({"role": "tool", "content": result.tool_output})
new_messages.append({"role": "assistant", "content": reply_text})
```

### ✅ 4. Update turn pruning (`core/context.py`)

`find_turn_boundary` scans for the next `user` role to correctly handle 2-3 message turns:
```python
def find_turn_boundary(messages):
    for i in range(1, len(messages)):
        if messages[i].get("role") == "user":
            return i
    return len(messages)
```

### ✅ 5. Remove `llm_response` from session flow

- Removed `llm_response` field from `ToolExecutionResult`
- Removed `llm_response` from `MessageReply` dataclass
- Removed `llm_response` assignment in `route_message`

### ✅ 6. Verify Ollama `tool` role support

Confirmed via curl test against Gemma 4 / Ollama — the `tool` role is accepted and the model correctly references tool content in subsequent turns.

---

## Files Changed

| File | Change | Status |
|------|--------|--------|
| `core/registry.py` | Added `tool_output`, removed `llm_response` | ✅ |
| `core/router.py` | Split tool vs reply outputs, removed `llm_response` assignment | ✅ |
| `core/orchestrator.py` | 3-turn history in `process_message` and `re_infer`, removed `llm_response` from `MessageReply` | ✅ |
| `core/context.py` | Dynamic `find_turn_boundary` | ✅ |

---

## Tests

All 35 tests pass including updated assertions for turn boundary detection and 3-turn history storage.
