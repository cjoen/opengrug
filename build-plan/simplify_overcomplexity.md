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

## ~~2. Fix Incomplete Sanitizer~~ ✅ Done (Phase 1, 2026-04-23)
Fixed in `core/utils.py` — now escapes all `<` to `&lt;`.

---

## ~~3. Remove Orchestrator Fallback LLM Call~~ ✅ Done (Phase 1, 2026-04-23)
Removed fallback `route_message` call. Orchestrator now returns `ErrorReply` on exception.

---

## 4. Remove Redundant `tool_output_combined` Variable

**File:** `core/router.py:87-98`

`tool_output_combined` and the `tool_outputs` branch of `combined` produce identical strings from the same list. Two passes, one result.

**Fix:** Remove `tool_output_combined`. Compute `combined` once. Use it for both the return value's `output` and `tool_output` fields.

---

## ~~5. Remove Silent Exception Swallowing in `auto_offload_pruned_turns`~~ ✅ Done (Phase 1, 2026-04-23)
Removed try/except — exceptions now propagate.

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
