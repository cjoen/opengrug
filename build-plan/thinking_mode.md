# Thinking Mode — Implementation Plan

Enable Gemma 4's `<|think|>` control token via a config toggle.

---

## Goal

When enabled, the model performs internal chain-of-thought reasoning before emitting its JSON tool call. The thinking output is stripped before JSON parsing — it never surfaces to the user. A config flag lets it be turned off if responses are slow.

---

## Changes

### 1. Add config toggle (`core/config.py`)

Add `thinking_mode` to the `llm` section of `_DEFAULTS`:

```python
"llm": {
    ...
    "thinking_mode": False,
}
```

Default is `False` — opt-in. Users enable it in `grug_config.json`:
```json
{
  "llm": {
    "thinking_mode": true
  }
}
```

---

### 2. Inject `<|think|>` into system prompt (`core/orchestrator.py`)

`build_system_prompt` is where the final system prompt is assembled. Append the token at the end when the toggle is on:

```python
@staticmethod
def build_system_prompt(base_system_prompt: str, compression_mode: str = "ULTRA") -> str:
    today = datetime.now().strftime("%Y-%m-%d")
    prompt = base_system_prompt.replace("{{COMPRESSION_MODE}}", compression_mode)
    prompt = prompt.replace("{{CURRENT_DATE}}", today)
    if config.llm.thinking_mode:
        prompt += "\n<|think|>"
    return prompt
```

The `<|think|>` token must be at the end of the system prompt — it signals the model to open a reasoning channel before responding.

---

### 3. Strip thinking block before JSON parse (`core/orchestrator.py`)

When thinking mode is on, the model response will look like:

```
<|channel>thought
... internal reasoning ...
<channel|>
{"tool": "add_note", "arguments": {...}, "confidence_score": 5}
```

The thinking block must be stripped before `json.loads`. Add `import re` to the top of the file, then strip in `_parse_and_execute`:

```python
import re

def _parse_and_execute(self, response_text: str, user_message: str) -> ToolExecutionResult:
    # Strip Gemma 4 thinking channel block if present
    response_text = re.sub(r"<\|channel>.*?<channel\|>", "", response_text, flags=re.DOTALL).strip()

    try:
        call_data = json.loads(response_text)
    ...
```

The strip is unconditional — safe even when thinking mode is off (no-op if the tokens aren't present).

---

## Files Changed

| File | Change |
|------|--------|
| `core/config.py` | Add `"thinking_mode": False` to `llm` defaults |
| `core/orchestrator.py` | Add `import re`; append `<|think|>` in `build_system_prompt` when enabled; strip `<|channel>...<channel|>` in `_parse_and_execute` |

---

## Tests

- `build_system_prompt` with `thinking_mode=True`: assert `<|think|>` at end of returned prompt
- `build_system_prompt` with `thinking_mode=False`: assert `<|think|>` NOT in returned prompt
- `_parse_and_execute` with thinking block prefix: assert block stripped and JSON parsed correctly
- `_parse_and_execute` without thinking block: assert existing behaviour unchanged

Run: `python3 -m pytest test_grug.py -q -k "not test_16"` — all 35 tests must still pass.

---

## Notes

- Multi-turn stripping (removing prior `<|channel>` blocks from message history) is NOT included here. The docs say to strip prior thoughts before reinserting history on multi-turn conversations, but that requires changes to the message history management in `app.py`. Defer to a follow-up.
- If responses are slow with thinking mode on, turn it off in `grug_config.json` — no restart needed (config is re-read via the existing hot-reload mechanism).
