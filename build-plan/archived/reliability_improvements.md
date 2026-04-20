# Reliability Improvements — Technical Plan

**Status:** Implemented

Two features aimed at reducing bad tool calls and improving routing accuracy.

---

## Critique of Original Design

1. **2.3 conflicts with existing confidence logic.** `route_message` already rejects calls with `confidence_score < 8` (line 416). The plan proposed a *second* threshold at 4, but didn't address the existing one. Fix: **replace** the hardcoded check with the new configurable, category-aware logic.

2. **Category mapping must exist in code, not just the prompt.** `system.md` can teach Grug the categories, but the orchestrator also needs the mapping to generate clarification messages listing the right tools. A simple dict in the orchestrator is enough.

3. **Argument extraction output format was unspecified.** The fast path still calls Ollama — its response must match the existing `{"tool": "...", "arguments": {...}, "confidence_score": ...}` format so it feeds into the same execution path. The `argument_extraction.md` prompt pre-fills the tool name and tells Grug to return the standard JSON.

4. **`route_message` is doing too much (SRP).** Extract the fast path into its own method rather than adding more inline branches.

---

## 2.2 — Explicit Tool Call Convention (Fast Path)

### Step 1: Config (`core/config.py`)

Add a new `shortcuts` section to `_DEFAULTS`:

```python
"shortcuts": {
    "prefix": "/",
    "aliases": {
        "note": "add_note",
        "task": "add_task"
    }
}
```

In `GrugConfig.__init__`, expose it: `self.shortcuts = ns.shortcuts`

The `aliases` value is a dict (not a nested namespace), so `_dict_to_namespace` will keep it as-is. Access pattern: `config.shortcuts.prefix`, `config.shortcuts.aliases` (a plain dict).

**Verify:** `_dict_to_namespace` converts nested dicts to `SimpleNamespace` recursively. The `aliases` dict is a flat `str→str` dict, so it will become a `SimpleNamespace`. Access will be `config.shortcuts.aliases.note` not `config.shortcuts.aliases["note"]`. This means lookup by dynamic key won't work with dot notation. **Fix:** Either (a) special-case aliases to stay as a dict, or (b) convert back via `vars(config.shortcuts.aliases)` at lookup time. Option (b) is simpler — no config changes needed, one `vars()` call in the orchestrator.

### Step 2: Argument Extraction Prompt (`prompts/argument_extraction.md`)

New file. This prompt is used when the tool is already known. It must:
- Tell Grug which tool was identified
- Provide the tool's schema (injected at runtime)
- Instruct Grug to extract arguments from the user text
- Output the standard JSON format: `{"tool": "<name>", "arguments": {...}, "confidence_score": N}`
- Allow `ask_for_clarification` if critical info is missing

```markdown
# Argument Extraction Mode

The user has explicitly requested the following tool: {{TOOL_NAME}}

Tool schema:
{{TOOL_SCHEMA}}

Extract the arguments from the user's message below. Return ONLY valid JSON in this exact format:
{"tool": "{{TOOL_NAME}}", "arguments": {<extracted args>}, "confidence_score": <0-10>}

If the user's message is missing critical required information, return:
{"tool": "ask_for_clarification", "arguments": {"reason_for_confusion": "<what's missing, in caveman voice>"}, "confidence_score": 10}

User message: {{USER_TEXT}}
```

### Step 3: Orchestrator (`core/orchestrator.py`)

Add a new method `_try_shortcut` to `GrugRouter`:

```python
def _try_shortcut(self, user_message: str, system_prompt: str) -> Optional[ToolExecutionResult]:
    """Fast path: if message starts with shortcut prefix, route directly.
    
    Returns a ToolExecutionResult if handled, None to fall through to normal routing.
    """
    prefix = config.shortcuts.prefix
    if not user_message.startswith(prefix):
        return None

    # Parse: "/note fire is hot" → alias="note", text="fire is hot"
    rest = user_message[len(prefix):]
    parts = rest.split(None, 1)  # split on first whitespace
    if not parts:
        return None  # bare prefix, fall through

    alias = parts[0].lower()
    aliases_dict = vars(config.shortcuts.aliases)  # SimpleNamespace → dict
    tool_name = aliases_dict.get(alias)

    if tool_name is None:
        return None  # unknown alias, fall through to normal routing

    user_text = parts[1] if len(parts) > 1 else ""
    if not user_text.strip():
        return ToolExecutionResult(
            success=True,
            output=f"Grug need words after {prefix}{alias}. What Grug do?"
        )

    # Load argument extraction prompt and fill placeholders
    prompt_path = os.path.join(self._prompt_dir, "argument_extraction.md")
    with open(prompt_path, "r", encoding="utf-8") as f:
        extraction_prompt = f.read()

    # Get the tool schema from registry
    tool_schema = None
    for s in self.registry.get_all_schemas():
        if s["name"] == tool_name:
            tool_schema = json.dumps(s["schema"], indent=2)
            break

    extraction_prompt = extraction_prompt.replace("{{TOOL_NAME}}", tool_name)
    extraction_prompt.replace("{{TOOL_SCHEMA}}", tool_schema or "{}")
    extraction_prompt = extraction_prompt.replace("{{USER_TEXT}}", user_text)

    # Call Ollama with focused prompt — single-turn, no history needed
    messages = [{"role": "user", "content": user_text}]
    response_text = self.invoke_chat(extraction_prompt, messages)

    # From here, reuse the existing JSON parse + execute logic
    return None  # placeholder — see note below
```

**Important:** The JSON parsing and execution logic after `invoke_chat` (parse JSON, trace, confidence check, `registry.execute`) is currently inline in `route_message`. To avoid duplication, **extract** that logic into a shared method `_parse_and_execute(response_text, user_message)` that both `_try_shortcut` and the normal path in `route_message` call.

```python
def _parse_and_execute(self, response_text: str, user_message: str) -> ToolExecutionResult:
    """Parse LLM JSON response, log trace, check confidence, execute tool."""
    try:
        call_data = json.loads(response_text)
    except json.JSONDecodeError:
        return ToolExecutionResult(success=False, output="Edge model failed to emit valid JSON.")

    tool_name = call_data.get("tool")
    args = call_data.get("arguments", {})
    confidence_score = call_data.get("confidence_score", 0)

    # Trace logging
    try:
        trace_entry = json.dumps({
            "ts": datetime.now().isoformat(),
            "user_msg": user_message[:200],
            "tool": tool_name,
            "args": args,
            "confidence": confidence_score,
        })
        trace_path = os.path.join("brain", "routing_trace.jsonl")
        os.makedirs(os.path.dirname(trace_path), exist_ok=True)
        with open(trace_path, "a", encoding="utf-8") as tf:
            tf.write(trace_entry + "\n")
    except Exception:
        pass

    # Confidence check (replaced by 2.3 category-aware logic — see below)
    if confidence_score <= config.llm.low_confidence_threshold and tool_name not in ("ask_for_clarification", "reply_to_user"):
        category = self._get_tool_category(tool_name)
        options = self._get_category_tools_description(category)
        return ToolExecutionResult(
            success=True,
            output=f"Grug not sure what you mean. You want Grug to: {options}? Tell Grug which."
        )

    return self.registry.execute(tool_name, args)
```

Then `route_message` becomes:
```python
def route_message(self, user_message, ...):
    self._check_prompt_reload()
    # ... legacy API handling (unchanged) ...

    # Fast path
    shortcut_result = self._try_shortcut(user_message, system_prompt)
    if shortcut_result is not None:
        return shortcut_result

    # Normal routing path
    # ... build augmented_system (unchanged) ...
    response_text = self.invoke_chat(augmented_system, message_history)
    return self._parse_and_execute(response_text, user_message)
```

### Step 4: Tests (`test_grug.py`)

Add tests for:
- `/note fire is hot` → calls `add_note` with extracted args
- `/task fix the login` → calls `add_task` with extracted args  
- `/note` (empty) → returns error message, no LLM call
- `/unknown blah` → falls through to normal routing
- Normal message (no prefix) → normal routing (unchanged behavior)

---

## 2.3 — Tool Hierarchy & Clarification

### Step 1: Config (`core/config.py`)

Add `low_confidence_threshold` to the `llm` defaults:

```python
"llm": {
    ...
    "low_confidence_threshold": 4,
}
```

### Step 2: Categories in System Prompt (`prompts/system.md`)

Add a tool categories section. This teaches Grug the groupings so his confidence scores are category-aware:

```markdown
## Tool Categories
When choosing a tool, consider which category the user's request falls into:
- [NOTES]: add_note, query_memory — for saving or retrieving information
- [TASKS]: add_task, edit_task, list_tasks, summarize_board — for project board work
- [SYSTEM]: reply_to_user, ask_for_clarification, list_capabilities — for conversation and help
```

### Step 3: Category Mapping in Orchestrator (`core/orchestrator.py`)

Add a class-level constant and two helper methods to `GrugRouter`:

```python
# Tool → category mapping (update when tools are added)
_TOOL_CATEGORIES = {
    "add_note": "NOTES",
    "query_memory": "NOTES",
    "add_task": "TASKS",
    "edit_task": "TASKS",
    "list_tasks": "TASKS",
    "summarize_board": "TASKS",
    "reply_to_user": "SYSTEM",
    "ask_for_clarification": "SYSTEM",
    "list_capabilities": "SYSTEM",
}

# Plain-language descriptions for clarification messages
_CATEGORY_DESCRIPTIONS = {
    "NOTES": "save a note, or search old notes",
    "TASKS": "add a task, edit a task, list tasks, or get a board summary",
    "SYSTEM": "chat, ask for help, or see what Grug can do",
}

def _get_tool_category(self, tool_name: str) -> str:
    return self._TOOL_CATEGORIES.get(tool_name, "SYSTEM")

def _get_category_tools_description(self, category: str) -> str:
    return self._CATEGORY_DESCRIPTIONS.get(category, "help Grug figure out what you need")
```

### Step 4: Replace Existing Confidence Check

**Delete** the existing hardcoded check in `route_message` (line 416-420):
```python
# DELETE THIS:
if confidence_score < 8 and tool_name not in ("ask_for_clarification", "reply_to_user"):
    return ToolExecutionResult(
        success=True,
        output=f"Grug not very sure (confidence {confidence_score}/10)..."
    )
```

This is now handled inside `_parse_and_execute` using `config.llm.low_confidence_threshold` and category-aware clarification (see 2.2 Step 3 above).

### Step 5: Tests (`test_grug.py`)

Add tests for:
- Confidence score above threshold → tool executes normally
- Confidence score at/below threshold for a NOTES tool → clarification lists note options
- Confidence score at/below threshold for a TASKS tool → clarification lists task options
- `ask_for_clarification` and `reply_to_user` bypass the threshold check (never blocked)

---

## Implementation Order

### Phase 1: Refactor (prerequisite for both features)
1. Extract `_parse_and_execute` from `route_message` inline logic
2. Verify existing tests still pass

### Phase 2: Feature 2.3 (Tool Hierarchy)
1. Add `low_confidence_threshold` to config defaults + expose in `GrugConfig`
2. Add `_TOOL_CATEGORIES`, `_CATEGORY_DESCRIPTIONS`, and helper methods to `GrugRouter`
3. Add categories section to `prompts/system.md`
4. Replace hardcoded confidence check with category-aware logic in `_parse_and_execute`
5. Add tests

### Phase 3: Feature 2.2 (Fast Path)
1. Add `shortcuts` section to config defaults + expose in `GrugConfig`
2. Create `prompts/argument_extraction.md`
3. Add `_try_shortcut` method to `GrugRouter`
4. Add fast-path call at top of `route_message`
5. Add tests

### Why 2.3 before 2.2
The refactor in Phase 1 extracts `_parse_and_execute`, which 2.3 modifies (confidence logic) and 2.2 reuses (shortcut execution). Building 2.3 first means 2.2 inherits the category-aware confidence check for free — even shortcut-routed calls benefit from it.

---

## Files Changed

| File | Phase | Change |
|------|-------|--------|
| `core/config.py` | 1, 2, 3 | Add `low_confidence_threshold` and `shortcuts` to defaults, expose in `GrugConfig` |
| `core/orchestrator.py` | 1, 2, 3 | Extract `_parse_and_execute`, add category mapping, add `_try_shortcut`, simplify `route_message` |
| `prompts/system.md` | 2 | Add tool categories section |
| `prompts/argument_extraction.md` | 3 | New file — focused arg extraction prompt |
| `test_grug.py` | 2, 3 | Tests for confidence threshold + category clarification, shortcut fast path |
