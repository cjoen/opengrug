# Revised Implementation Plan

> **ARCHIVED 2026-04-11** — this document is a historical snapshot of a mid-project gap audit. **Almost every "What's missing" item below has since been shipped.** The steel-man review in April 2026 confirmed:
> - Phase 2 background indexer: **shipped** (daemon thread in [core/vectors.py](../../core/vectors.py))
> - Phase 3 schema validation: **shipped** (jsonschema.Draft7Validator in [core/orchestrator.py](../../core/orchestrator.py) `ToolRegistry.execute`)
> - Phase 3 HITL Slack UX: **shipped** (Block Kit approve/deny in [app.py](../../app.py))
> - Phase 4 frontier escalation: **shipped** (real `anthropic.Anthropic` call in `GrugRouter.execute_frontier_escalation`, not a stub)
> - Phase 5 all-four prompt files: **shipped** (`load_prompt_files` concatenates `system.md + rules.md + memory.md + schema_examples.md`)
> - Phase 5 `{{CURRENT_DATE}}` interpolation: **shipped** (`build_system_prompt`)
> - Phase 5 `confidence_score` honored: **shipped** (escalates when < 8 in `route_message`; separate debt about the *default* value is tracked as H5 in followups)
> - Phase 6 test assertions: **shipped** ([test_grug.py](../../test_grug.py) has 6 tests with real `assert` statements)
> - Docker non-root: **shipped** (`user: "1000:1000"` in `docker-compose.yml`)
>
> **Remaining real gaps** (plus brand-new findings from the security review) are all tracked in [../followups.md](../followups.md). Do not re-triage this file — treat it as historical only.

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

**~~What's missing~~ → SHIPPED**:
- [x] Background indexing thread. *(Daemon thread in [core/vectors.py](../../core/vectors.py) watches `brain/daily_notes/` and re-indexes on change. New debt: thread safety around `check_same_thread=False` — see H6 in [../followups.md](../followups.md).)*

---

## Phase 3: Secure Orchestrator & CLI Tool Abstraction

**What's done**: `shell=False` subprocess execution. HITL `requires_approval` flag returned.

**~~What's missing~~ → SHIPPED**:
- [x] Schema validation at execution time. *(`ToolRegistry.execute` in [core/orchestrator.py](../../core/orchestrator.py) now runs `jsonschema.Draft7Validator(schema).validate(arguments)` on both python and CLI tool branches and returns a clean `Invalid args for {tool}` error. Covered by `test_grug.py::test_3_schema_validation_rejects_bad_args`.)*
- [x] HITL Slack interactive message. *(Block Kit approve/deny card in [app.py](../../app.py) — `PENDING` dict at line 15, card assembly at ~line 251, `@app.action("grug_approve")` and `grug_deny` handlers at ~line 288. Follow-up debt: requester identity check (C1) and durable persistence (H3) in [../followups.md](../followups.md).)*

---

## Phase 4: Graceful Degradation & Escalation

**What's done**: `frontier_available` flag, offline re-prompt loop in `route_message()`.

**~~What's missing~~ → SHIPPED**:
- [x] Real frontier escalation. *(`GrugRouter.execute_frontier_escalation` in [core/orchestrator.py](../../core/orchestrator.py) is a real `anthropic.Anthropic(api_key=...).messages.create(...)` call. Uses `CLAUDE_MODEL` env var (default `claude-opus-4-6`), caches the system prompt, and maps `anthropic.APIError`/`APIConnectionError` → `ERROR_OFFLINE` so the degradation path fires. Follow-up debt: top-level `import anthropic` breaks the offline-first guarantee — see H9 in [../followups.md](../followups.md).)*

---

## Phase 5: Prompts & Caveman Compression Mode

**What's done**: LITE/FULL/ULTRA compression modes defined in system.md. `{{COMPRESSION_MODE}}` injected.

**~~What's missing~~ → SHIPPED**:
- [x] Load all 4 prompt files at runtime. *(`load_prompt_files` in [core/orchestrator.py](../../core/orchestrator.py) iterates `["system.md", "rules.md", "memory.md", "schema_examples.md"]` and concatenates each with a `## {filename}` header. Covered by `test_grug.py::test_6_prompt_stitching_and_current_date`.)*
- [x] Inject `{{CURRENT_DATE}}`. *(`GrugRouter.build_system_prompt` substitutes both `{{COMPRESSION_MODE}}` and `{{CURRENT_DATE}}` before sending.)*
- [x] Honor `confidence_score`. *(`GrugRouter.route_message` escalates when `confidence_score < 8`. Covered by `test_grug.py::test_4_confidence_score_forces_escalation`. Separate debt: the default when the field is missing is `10` — fails open instead of closed. Tracked as H5 in [../followups.md](../followups.md).)*

Separate follow-up: `memory.md` is loaded but near-empty — the token cost does not pay off. Decision task M9 in followups.

---

## Phase 6: Integration & Final Verification

**What's done**: Slack bot is wired end-to-end. Docker deployment works.

**~~What's missing~~ → SHIPPED**:
- [x] Test suite with real assertions. *([test_grug.py](../../test_grug.py) now contains 6 tests with real `assert` statements: caveman storage flow, graceful offline degradation, schema validation, confidence-score escalation, HITL field population, and prompt stitching + date interpolation. The "print-only" claim is outdated.)*
- [x] E2E Slack verification. *(The manual E2E checklist is in the [test_grug.py](../../test_grug.py) module docstring.)*

**Still outstanding** (new debt surfaced by the April 2026 review, not in this doc's original scope): `test_list.py` has no assertions; coverage for `ToolRegistry.execute` destructive gating, the CLI branch, and HITL approval flows is thin. Tracked as L3 in [../followups.md](../followups.md).

---

## Non-Phase Gap: Docker Security

The architecture doc specifies the container should run as a **non-root user**. The Dockerfile explicitly comments this out with "disabled to prevent macOS volume permission errors."

- [x] Non-root Docker: **shipped**. *(`docker-compose.yml` sets `user: "1000:1000"`.)*

---

## Summary (Corrected 2026-04-11)

| Phase | Original Claim | Current State |
|-------|---------------|---------------|
| Phase 1: Storage + Docker | Complete | Complete |
| Phase 2: Vector Search | Missing background indexing | **Shipped** (thread safety debt → H6) |
| Phase 3: Orchestrator + HITL | Missing schema validation + HITL Slack UX | **Shipped** (C1/H3 debt on HITL) |
| Phase 4: Frontier Escalation | Escalation is a stub | **Shipped** (H9 debt on top-level import) |
| Phase 5: Prompt Assembly | 3/4 prompt files unused, confidence_score ignored | **Shipped** (H5 debt on default value, M9 on `memory.md` content) |
| Phase 6: Verification | No real tests or E2E verification | **Shipped** (L3 debt on coverage breadth) |
| Docker Security | Running as root | **Shipped** (`user: "1000:1000"`) |

All remaining work is tracked in [../followups.md](../followups.md).
