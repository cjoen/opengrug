# Revised Implementation Plan

Audit of build-plan/ tasks against current implementation. Phases 2–6 were marked incomplete
in task.md; this document details what remains and the concrete steps to finish each.

> **Note**: Docker OS dependencies and Mac volume/SQLite mounting issues have already been resolved.
> The remaining gaps below are all code-level changes.

## Recommended Execution Order
1. **Prompt stitching** — quickest win for LLM accuracy; unblocks confidence_score and rules
2. **Frontier escalation** — wire the actual Anthropic API call
3. **HITL Slack UX** — Block Kit approve/deny flow
4. **Background indexer** — daemon thread or watchdog for live vector sync
5. **Schema validation** — harden tool arg checking
6. **Tests** — add assertions, E2E checklist
7. **Docker non-root** — fix volume permissions

---

## Phase 2: DB Cache & Vector Search

**What's done**: VectorMemory class exists, schema correct, query_memory works when HAS_VSS=True.

**What's missing**:
- [ ] Background indexing thread: `index_markdown_directory()` is called once at startup but never again. New notes written during a session are invisible to semantic search until restart.
  - **Fix**: Spawn a background thread (or use `watchdog`) that monitors `brain/daily_notes/` for file changes and re-indexes new blocks incrementally.

---

## Phase 3: Secure Orchestrator & CLI Tool Abstraction

**What's done**: `shell=False` subprocess execution. HITL `requires_approval` flag returned.

**What's missing**:
- [ ] Schema validation at execution time: `ToolRegistry.execute()` calls `func(**arguments)` with zero validation. LLM-provided args could be wrong types, missing required fields, or malformed.
  - **Fix**: Before calling the function, validate `arguments` against the registered schema dict (required fields, types). Return a descriptive error if invalid instead of a Python exception.

- [ ] HITL Slack interactive message: when `requires_approval=True` is returned, nothing happens — no Slack message is sent, the action is simply blocked silently.
  - **Fix**: In `app.py handle_message()`, check `result.requires_approval`. If True, post an interactive Slack Block Kit message with Approve/Deny buttons. Wire a `@app.action()` handler to resume execution on approval.

---

## Phase 4: Graceful Degradation & Escalation

**What's done**: `frontier_available` flag, offline re-prompt loop in `route_message()`.

**What's missing**:
- [ ] Real frontier escalation: `execute_frontier_escalation()` is a stub that never calls the Anthropic API.
  - **Fix**: Implement actual Claude API call using `anthropic` SDK. Pass the original user message + context as the user turn. Return the Claude response as the tool output. Handle `anthropic.APIError` gracefully (treat as offline).

---

## Phase 5: Prompts & Caveman Compression Mode

**What's done**: LITE/FULL/ULTRA compression modes defined in system.md. `{{COMPRESSION_MODE}}` injected.

**What's missing**:
- [ ] Load all 4 prompt files at runtime: `rules.md`, `memory.md`, and `schema_examples.md` are never read. The architecture specifies they should all be concatenated into every prompt.
  - **Fix**: In `build_system_prompt()` (or in `route_message()`), read and concatenate all 4 files: `system.md + rules.md + memory.md + schema_examples.md`. Inject `{{CURRENT_DATE}}` into the combined string before sending.

- [ ] Inject `{{CURRENT_DATE}}`: `prompts/rules.md` contains the placeholder but it is never interpolated.
  - **Fix**: After concatenating prompt files, replace `{{CURRENT_DATE}}` with `datetime.now().strftime("%Y-%m-%d")`.

- [ ] Honor `confidence_score`: Gemma is prompted to output a `confidence_score` field (per schema_examples.md) but `route_message()` never reads it.
  - **Fix**: After parsing Gemma's JSON response, check `confidence_score`. If below threshold (e.g., < 8), trigger `escalate_to_frontier` regardless of which tool Gemma selected.

---

## Phase 6: Integration & Final Verification

**What's done**: Slack bot is wired end-to-end. Docker deployment works.

**What's missing**:
- [ ] Test suite with real assertions: `test_grug.py` is print-only with no pass/fail signal.
  - **Fix**: Add `assert` statements (or migrate to `pytest`) verifying: note was written to markdown, vector DB was updated, degradation path returns non-empty output.

- [ ] E2E Slack verification: No documented or automated test for the full Slack → LLM → tool → Slack round trip.
  - **Fix**: Add a manual verification checklist (or integration test) that posts a real Slack message and checks the brain/daily_notes/ file was updated.

---

## Non-Phase Gap: Docker Security

The architecture doc specifies the container should run as a **non-root user**. The Dockerfile explicitly comments this out with "disabled to prevent macOS volume permission errors."

- [ ] Investigate and fix non-root user in Docker: The correct fix is to `chown` the mounted volume to the non-root user UID, not to run as root. Example: add `user: "1000:1000"` to docker-compose.yml and set the host `./brain` directory permissions accordingly.

---

## Summary

| Phase | Status |
|-------|--------|
| Phase 1: Storage + Docker | Complete |
| Phase 2: Vector Search | Missing background indexing |
| Phase 3: Orchestrator + HITL | Missing schema validation + HITL Slack UX |
| Phase 4: Frontier Escalation | Escalation is a stub |
| Phase 5: Prompt Assembly | 3/4 prompt files unused, confidence_score ignored |
| Phase 6: Verification | No real tests or E2E verification |
| Docker Security | Running as root |
