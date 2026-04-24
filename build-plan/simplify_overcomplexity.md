# Build Plan: Simplify Overcomplicated Code

**Status:** Ready to implement
**Priority:** Medium
**Discovered:** 2026-04-23 (code review)
**Estimated scope:** Mostly removing/replacing code, net negative lines

---

## 1. Remove Broken CLI Argument Injection Defense

**File:** `core/registry.py:138-146`

The `--` sentinel appended as the last token does nothing — there are no positional args after it. The `startswith("--")` check misses single-dash flags. This is security theater.

**Fix:** Remove the `--` append. Replace the `startswith("--")` check with a proper allowlist approach: CLI tools should declare their allowed flags at registration time. For now, the simplest safe fix is to reject any value starting with `-` (not just `--`).

---

## 2. Fix Incomplete Sanitizer

**File:** `core/utils.py:20-22`

`_sanitize_untrusted` strips close tags (`</tag>`) but not open tags (`<tag>`). An injected open tag can still shift prompt parsing context.

**Fix:** Strip both open and close tags for the sensitive delimiters, or escape `<` to `&lt;` in untrusted input entirely. The latter is simpler and more robust.

---

## 3. Remove Orchestrator Fallback LLM Call

**File:** `core/orchestrator.py:143-157`

On any exception, the orchestrator calls `route_message` again with no history and a hardcoded fallback prompt. If the LLM was unreachable, this also fails. The second call masks the original error.

**Fix:** Remove the fallback `route_message` call. Return an `ErrorReply` with the original exception message instead. The adapter already knows how to handle `ErrorReply`.

---

## 4. Remove Redundant `tool_output_combined` Variable

**File:** `core/router.py:87-98`

`tool_output_combined` and the `tool_outputs` branch of `combined` produce identical strings from the same list. Two passes, one result.

**Fix:** Remove `tool_output_combined`. Compute `combined` once. Use it for both the return value's `output` and `tool_output` fields.

---

## 5. Remove Silent Exception Swallowing in `auto_offload_pruned_turns`

**File:** `core/context.py:53-64`

Catches all exceptions and prints. But the pruned turns are already gone from memory — if the offload fails, they're lost forever. The try/except makes data loss silent.

**Fix:** Remove the try/except. Let the exception propagate so callers in the orchestrator can avoid discarding turns when offload fails.

---

## 6. Replace No-Client Fake Tool Call with Clear Error

**File:** `core/router.py:33-42`

When `llm_client is None`, returns a fake `ask_for_clarification` tool call. Disguises a config error as a user-facing message.

**Fix:** Log an error and return an `LLMResponse` with `content="LLM client not configured"` and empty `tool_calls`. The orchestrator will surface this as a reply. Tests that don't inject a client should mock it instead.

---

## Files Modified

| File | Change |
|---|---|
| `core/registry.py` | Fix CLI arg check (reject `-` prefix, remove useless `--` append) |
| `core/utils.py` | Escape `<` in untrusted input instead of selective tag stripping |
| `core/orchestrator.py` | Remove fallback LLM call, return `ErrorReply` |
| `core/router.py` | Remove redundant variable, fix no-client fallback |
| `core/context.py` | Remove silent exception swallow in `auto_offload_pruned_turns` |

---

## Verification

```bash
python3 -m pytest tests/ -v
```

The orchestrator fallback removal may require updating `test_router.py` if any test relies on the no-client fake tool call behavior.
