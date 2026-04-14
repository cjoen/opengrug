# Thread Context Fix — Technical Plan

Two bugs that together cause Grug to lose conversational context within a thread.

---

## Bug 1: Assistant Session Messages Stored in Wrong Format

### Problem

When a thread turn completes, `process_message` saves this to the session:

```python
new_messages = session["messages"] + [
    {"role": "user", "content": text},
    {"role": "assistant", "content": result.output},  # ← plain text tool output
]
```

`result.output` is the human-readable tool execution result (e.g. `"Grug say hi!"`). But
the LLM is instructed to always emit JSON in this format:

```json
{"thinking": "...", "actions": [{"tool": "reply_to_user", "arguments": {"message": "Grug say hi!"}}]}
```

When the next message arrives, the LLM sees prior `assistant` turns in plain text while its
system prompt tells it to output JSON. Smaller models like Gemma treat format consistency as
a strong signal — a plain-text assistant turn looks like a different conversation modality, so
the model doesn't properly use it as context and behaves as if it has no memory of prior turns.

### Fix

Store the LLM's raw JSON response as the `assistant` content in session history. The model
then sees its own format in prior turns and correctly extracts prior intent, tool calls, and
user context.

### Token impact

The JSON wrapper + thinking adds ~60–150 tokens per assistant turn vs plain text. With
`thread_history_limit: 10` (up to 5 assistant turns), worst case is ~300–750 extra tokens.
Against `target_context_tokens: 2048`, that's 15–37% more history usage — but the existing
turn-based pruning loop handles overflow (fewer turns kept, not a crash).

#### Step 1: `core/registry.py` — Add field to `ToolExecutionResult`

```python
class ToolExecutionResult(BaseModel):
    success: bool
    output: str
    requires_approval: bool = False
    tool_name: Optional[str] = None
    arguments: Optional[dict] = None
    llm_response: Optional[str] = None
```

#### Step 2: `core/router.py` — Attach raw response to result

Both the normal path and the shortcut path call the LLM, so both need `llm_response`
attached. Only attach when parse succeeded — if the LLM returned malformed JSON, storing
garbage as the assistant turn is worse than storing the error message.

Normal path in `route_message`:

```python
response_text = self.invoke_chat(augmented_system, message_history)
result = self._parse_and_execute(response_text, user_message)
if result.success:
    result.llm_response = response_text
return result
```

Shortcut path in `_try_shortcut`:

```python
response_text = self.invoke_chat(extraction_prompt, messages)
result = self._parse_and_execute(response_text, user_message)
if result.success:
    result.llm_response = response_text
return result
```

#### Step 3: `app.py` — Use `llm_response` when saving session

Three places save assistant turns to the session. All three need updating:

**a) `process_message` (line 282)** — normal message flow:

```python
assistant_content = result.llm_response if result.llm_response else result.output
new_messages = session["messages"] + [
    {"role": "user", "content": text},
    {"role": "assistant", "content": assistant_content},
]
```

**b) `handle_approve` (line 366)** — after HITL approval. This is a direct
`registry.execute` with no LLM call, so there is no `llm_response`. The existing
`f"[Tool executed: {pending['tool_name']}] {result.output}"` format is fine here — the
model will see it as a system-inserted message between its own JSON turns.

**c) `_re_infer` (line 385)** — follow-up inference after HITL approval. This *does* go
through `router.route_message`, so `follow_up.llm_response` should be used:

```python
assistant_content = follow_up.llm_response if follow_up.llm_response else follow_up.output
messages_now.append({"role": "assistant", "content": assistant_content})
```

---

## Bug 2: `{{CURRENT_TIME}}` Not Replaced in System Prompt

### Problem

`prompts/rules.md` contains:

```
* **Current Time**: {{CURRENT_TIME}} (use this to calculate relative times...)
```

This placeholder is replaced in `GrugRouter.build_system_prompt` (the static method in
`core/router.py`). However, the **actual call path** in `process_message` uses
`build_system_prompt` imported from `core/context.py`, which only replaces
`{{COMPRESSION_MODE}}` and `{{CURRENT_DATE}}` — not `{{CURRENT_TIME}}`.

The literal string `{{CURRENT_TIME}}` leaks into the LLM's system prompt on every request.

### Fix

#### `core/context.py` — Add `{{CURRENT_TIME}}` replacement

Add imports for `timezone`, `ZoneInfo`, `ZoneInfoNotFoundError`. Replace the existing
date-only logic with timezone-aware time calculation:

```python
from datetime import datetime, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

def build_system_prompt(base, summaries, capped_tail, compression_mode=None):
    if compression_mode is None:
        compression_mode = config.llm.default_compression

    try:
        tz = ZoneInfo(config.scheduler.timezone)
    except ZoneInfoNotFoundError:
        tz = ZoneInfo("UTC")

    now_local = datetime.now(tz=tz)
    today = now_local.strftime("%Y-%m-%d")
    current_time = now_local.strftime("%H:%M %Z")

    prompt = base.replace("{{COMPRESSION_MODE}}", compression_mode)
    prompt = prompt.replace("{{CURRENT_DATE}}", today)
    prompt = prompt.replace("{{CURRENT_TIME}}", current_time)

    if summaries:
        prompt += f"\n\n## Recent Summaries (last {config.memory.summary_days_limit} days)\n{summaries}"
    if capped_tail:
        prompt += f"\n\n## Today's Notes\n{capped_tail}"

    return prompt
```

The static `GrugRouter.build_system_prompt` in `core/router.py` is used only by legacy test
callers. It can remain as-is (it already handles `{{CURRENT_TIME}}`).

---

## Files Changed

| File | Change |
|------|--------|
| `core/registry.py` | Add `llm_response: Optional[str] = None` to `ToolExecutionResult` |
| `core/router.py` | Attach `response_text` to result in both normal and shortcut paths (guarded by `result.success`) |
| `app.py` | Use `result.llm_response` in `process_message` and `_re_infer` (falling back to `result.output`); `handle_approve` left as-is (no LLM call) |
| `core/context.py` | Add `{{CURRENT_TIME}}` replacement with timezone-aware `datetime.now()` |

---

## Implementation Order

1. Fix `core/context.py` (isolated, no dependencies)
2. Add `llm_response` to `ToolExecutionResult` (additive, no breakage)
3. Attach `llm_response` in `route_message` normal path and `_try_shortcut`
4. Use `llm_response` in `process_message` and `_re_infer`
5. Run tests — verify no regressions
