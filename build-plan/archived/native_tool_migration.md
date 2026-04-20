# Native Tool Calling Migration Plan

**Status:** ✅ Complete (2026-04-20)

This plan details the architectural refactor to move OpenGrug away from forced JSON outputs to using native tool calling functionality supported by modern edge models (like Gemma 4 E4B) via Ollama's `/api/chat` `tools` payload.

## Goal Description

OpenGrug previously injected text-based schemas into the system prompt and forced the LLM to emit a hand-crafted JSON dictionary (containing `{"thinking": "...", "actions": [...]}`). This taxed the underlying model's probability mass and was highly prone to syntax errors (especially for edge models). 

The routing layer was migrated to use Ollama's native tool payloads. When Ollama natively manages the tools, it uses internal control tokens and grammars to guarantee tool inputs. This aligns with Gemma 4's native tool-calling architecture.

## Resolved: User Review Items

> [!NOTE]
> **Loss of the explicit "thinking" field:** 
> Accepted. Ollama returns natural language "Chain-of-Thought" directly in the standard `message.content` string, alongside a separate `message.tool_calls` array. `message.content` is mapped to the `content` field in `LLMResponse` and stored in trace logs via `storage.log_routing_trace()`.

## Completed Changes

---

### ✅ Core Data Structures & Registry

- [x] **[NEW] `core/interfaces.py`** — Created `LLMResponse` dataclass (`content: str`, `tool_calls: List[Dict]`) and `LLMClient` ABC with `chat(system_prompt, messages, tools) -> LLMResponse`.
- [x] **[MODIFY] `core/registry.py`** — `get_all_schemas()` now returns the OpenAI standard JSON schema format (`{type: "function", function: {name, description, parameters}}`). Removed dead `llm_response` field from `ToolExecutionResult`.

---

### ✅ LLM Client Layer

- [x] **[MODIFY] `core/llm.py`** — `OllamaClient` implements the `LLMClient` interface. Passes `tools` as a first-class API parameter. Removed `format: json`. Normalizes Ollama's `message.tool_calls[].function` to internal `{tool, arguments}` dicts. Strips Gemma 4 `<|channel>` thinking tags. Falls back to `reply_to_user` when the model speaks without calling tools. Removed dead `json` import.

---

### ✅ Routing & Execution Engine

- [x] **[MODIFY] `core/router.py`** — `route_message()` passes tools dynamically via `self.registry.get_all_schemas()`. `_parse_and_execute()` accepts `LLMResponse` object directly — no more `json.loads()`. Confidence score gating fully removed. Dead imports (`re`, `json`, `config`) cleaned up. Docstring updated.

---

### ✅ Session History Parity & Bug Fixes

- [x] **[MODIFY] `core/orchestrator.py`** — Implemented 3-turn history (`user` → `tool` → `assistant`) in both `process_message` and `re_infer`. Bug 1 (HITL context drop) fixed. Dead imports (`Optional`, `field`) cleaned up.
- [x] **[MODIFY] `core/context.py`** — `find_turn_boundary()` scans dynamically for the next `user` role to support variable-length turns.

---

### ✅ Prompt Files

- [x] **[MODIFY] `prompts/system.md`** — Removed JSON format instructions, `thinking`/`actions` schema, `confidence_score`. Now describes persona and behavioral guidance only. Gemma 4 discovers tools from the native `tools` API array.
- [x] **[MODIFY] `prompts/schema_examples.md`** — Replaced JSON few-shot blocks with natural-language behavioral examples.
- [x] **[DELETE] `prompts/argument_extraction.md`** — Dead prompt with no code consumer. Was wasting tokens on every request.
- [x] **[MODIFY] `prompts/rules.md`** — Unchanged (already clean).

---

### ✅ Cleanup

- [x] Removed `low_confidence_threshold` from `core/config.py`
- [x] Removed `confidence_score` from `core/storage.py` trace logging
- [x] Removed dead `llm_response` field from `ToolExecutionResult`

---

## Resolved: Open Questions

1. **Confidence Score:** Fully stripped. `low_confidence_threshold` removed from config, gating logic removed from router, `confidence_score` removed from trace logs and all prompt files.

## Verification

- [x] All 35 pytest tests pass after refactor
- [ ] Manual verification: Deploy locally and test Slack interactions
