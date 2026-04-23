# Plan: Grug Self-Learning Memory

## Context
From `build-plan/backlog.md` — "Grug Self-Learning Memory (`memory.md`)". A three-stage memory improvement loop:

1. **Reflect** — AAR reviews conversation history, identifies candidate learnings
2. **Save** — User manually approves which insights become persistent instructions
3. **Retrieve** — Tagged instructions injected into every system prompt, grouped by tag

Grug never auto-writes to its own memory. AAR proposes; the human decides what sticks.

---

## Part 1: Instruction Storage & Retrieval

### 1a. `core/config.py` — add default
Add `"instructions_max_chars": 1500` to `_DEFAULTS["memory"]`.

### 1b. `core/storage.py` — add instruction methods to `GrugStorage`
File: `brain/memory.md`. Format: tagged bullet lines (`- #tag instruction text`).
Valid tags: `tasks`, `notes`, `scheduling`, `conversation`, `general`.

New methods (all use existing `_write_lock`):

- **`get_instructions() -> list[dict]`** — parse `brain/memory.md`, return `[{"tag": "tasks", "text": "..."}, ...]`
- **`add_instruction(instruction: str, tag: str, max_chars: int) -> str`** — substring dedup (case-insensitive), char budget check, append `- #{tag} {instruction}\n`. Per-instruction limits: min 10 chars, max 200 chars.
- **`edit_instruction(number: int, instruction: str, tag: str = None) -> str`** — 1-based, replace text (and optionally tag) at that index, rewrite file. Same dedup/length validation as add.
- **`remove_instruction(number: int) -> str`** — 1-based removal, rewrite file
- **`get_instructions_block() -> str`** — return instructions grouped by tag as formatted text for prompt injection. Empty string if no file/instructions. Output format:
  ```
  [TASKS]
  - Always use stable task IDs, never line numbers
  [CONVERSATION]
  - Keep responses under 3 sentences for simple questions
  ```

### 1c. `tools/instructions.py` — new file, instruction tools
Pattern follows `tools/notes.py` with `register_tools(registry, storage, config)`:

| Tool | Destructive | Category | Schema |
|------|------------|----------|--------|
| `add_instruction` | No | SELF | `instruction` (str, required), `tag` (str, required, enum) |
| `list_instructions` | No | SELF | no params — returns numbered list (`1. #tag instruction`, `2. #tag instruction`, ...) |
| `edit_instruction` | No | SELF | `instruction_number` (int, required), `instruction` (str, required), `tag` (str, optional, enum) |
| `remove_instruction` | Yes (HITL) | SELF | `instruction_number` (int, required) |

`list_instructions` output format:
```
1. #tasks Always use stable task IDs, never line numbers
2. #conversation Keep responses under 3 sentences for simple questions
3. #general Check user timezone before scheduling
```
Numbers correspond to `instruction_number` params in edit/remove.

### 1d. `core/context.py` — inject instructions into system prompt
Add `instructions_block=""` param to `build_system_prompt`. Inject as `## Self-Instructions` after base prompt, before RAG and activity:

```
[Base prompt: persona + rules]
## Self-Instructions          <-- NEW
## Relevant Memory            <-- existing RAG
## Today's Activity           <-- existing capped tail
```

### 1e. `core/orchestrator.py` — wire instructions through
Update 3 call sites to pass `instructions_block=self.storage.get_instructions_block()`:
- `_build_context()` — main path
- `re_infer()` — post-approval follow-up
- `process_message()` fallback

### 1f. `app.py` — register tools
```python
from tools.instructions import register_tools as register_instruction_tools
register_instruction_tools(registry, storage, config)
```

### 1g. `prompts/system.md` — add SELF tool category only
Add to Tool Categories:
```
- **SELF**: add_instruction, list_instructions, remove_instruction — recording and managing learned rules
```
Add to "When to Use Tools":
```
- **Self-improvement**: Use `add_instruction` when the user asks you to remember a preference or rule. Use `list_instructions` to review what you've learned.
```
No changes to the Memory Context section — self-instructions are injected dynamically by `build_system_prompt`, separate from the static prompt files.

---

## Part 2: After Action Report (AAR)

**Status:** Manual AAR implemented. Nightly AAR deferred — revisit once manual AAR proves useful.

### 2a. `core/summarizer.py` — add AAR method ✅ DONE
New method `generate_aar(messages: list[dict]) -> str`.
Returns the raw LLM output as a formatted report.

### 2b. `tools/instructions.py` — add `run_aar` tool ✅ DONE

| Tool | Destructive | Category | Schema |
|------|------------|----------|--------|
| `run_aar` | No | SELF | no params |

Implementation:
1. Get current thread via `router._request_state._schedule_thread_ts`
2. Pull session for that thread from `session_store`
3. Call `summarizer.generate_aar(messages)`
4. Return the report as text — user reads it, manually calls `add_instruction` for items they want to persist

**Design decision:** AAR is scoped to the current thread (not all sessions). User invokes it from whatever thread they want reviewed. Sessions persist for 7 days (was 4 hours), so threads remain available for AAR well after the conversation ends.

### 2c. `workers/background.py` — nightly AAR (DEFERRED)
Not implemented. If manual AAR proves useful, add:
- `nightly_aar_loop(session_store, summarizer, storage, config)` in `workers/background.py`
- Pulls all sessions with activity from today
- Generates AAR report, saves to `brain/aar/YYYY-MM-DD.md` if findings exist
- Does NOT auto-save instructions — report only
- Start as daemon thread in `app.py` alongside other background workers
- Will need `get_sessions_active_since(cutoff_datetime)` method on `SessionStore`

### 2d. `app.py` — wire tool dependencies ✅ DONE
- `register_instruction_tools(registry, storage, session_store, summarizer, router)` — all deps passed

---

## Files Modified/Created

| File | Action | Status |
|------|--------|--------|
| `core/config.py` | Add `instructions_max_chars` default, change idle timeout to 168h | ✅ |
| `core/storage.py` | Add 5 instruction methods + `_rewrite_instructions` helper | ✅ |
| `core/context.py` | Add `instructions_block` param to `build_system_prompt` | ✅ |
| `core/orchestrator.py` | Pass `instructions_block` in 3 call sites | ✅ |
| `core/summarizer.py` | Add `generate_aar()` method | ✅ |
| `tools/instructions.py` | **New** — 5 tools (add/list/edit/remove/run_aar) | ✅ |
| `app.py` | Register instruction tools | ✅ |
| `prompts/system.md` | Add SELF category docs | ✅ |
| `tests/test_instructions.py` | **New** — 23 tests for instructions + AAR | ✅ |
| `tests/test_config.py` | Update idle timeout assertion | ✅ |
| `evals/golden_dataset.jsonl` | Add 8 SELF category eval cases | ✅ |
| `workers/background.py` | Nightly AAR loop | DEFERRED |

---

## Verification
1. ✅ `python3 -m pytest tests/` — 66 tests pass
2. ✅ New tests for storage instruction methods (add/list/edit/remove/dedup/budget/tags)
3. ✅ New tests for `generate_aar()` prompt construction and error handling
4. ✅ Golden eval cases added for SELF category (8 cases)
5. Manual Slack test: tell Grug to remember a rule, verify it appears in subsequent prompts
6. Manual Slack test: run AAR in a thread, review output, add an instruction from it
