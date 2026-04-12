# Task List: Lightweight OpenClaw Memory Architecture

> **ARCHIVED 2026-04-11** — every phase in this checklist is now shipped. Status has been updated in place to match the current code. For outstanding work see [../followups.md](../followups.md).

- `[x]` **Phase 1: Project, Docker & Storage Setup**
  - `[x]` Create `Dockerfile` and `docker-compose.yml` defining the secure execution environment.
  - `[x]` Setup Python environment (`requirements.txt` with `sqlite-vss`, `sentence-transformers`, `slack-sdk`, etc.) inside the container.
  - `[x]` Mount persistent volume for the flat-file knowledge base (e.g., `/app/brain/`).
  - `[x]` Implement local script that appends capture payloads (Insight, Task, Person) directly entirely to Markdown files following a daily-append strategy.

- `[x]` **Phase 2: The DB Cache & Vector Search**
  - `[x]` Initialize `memory.db` SQLite database with `sqlite-vss` for vector search capabilities. *(`sqlite-vss` is disabled on macOS — `HAS_VSS=False` fallback path in [core/vectors.py](../../core/vectors.py). Full VSS works under Linux/Docker.)*
  - `[x]` Implement background indexing thread. *(Live in [core/vectors.py](../../core/vectors.py) — daemon thread watches `brain/daily_notes/` and re-indexes on change. Thread safety debt logged as H6 in [../followups.md](../followups.md).)*
  - `[x]` Implement `query_memory` Python function to query `memory.db` for semantic matches.

- `[x]` **Phase 3: Secure Orchestrator & CLI Tool Abstraction**
  - `[x]` Define strict JSON Schemas mapping to local functions and CLI tools.
  - `[x]` Build the secure subprocess executor. *(`shell=False`, schemas validated at execute time via `jsonschema.Draft7Validator` in [core/orchestrator.py](../../core/orchestrator.py) `ToolRegistry.execute`. CLI flag-injection hardening tracked as H1/H2 in followups.)*
  - `[x]` Implement Human-in-the-Loop (HITL) suspension. *(Block Kit approve/deny flow in [app.py](../../app.py) — `PENDING` dict + `handle_approve`/`handle_deny` handlers. Known gaps: requester identity check (C1) and durable persistence (H3) in followups.)*
  - `[x]` Build the local Python router. *(`GrugRouter` in [core/orchestrator.py](../../core/orchestrator.py).)*

- `[x]` **Phase 4: Graceful Degradation & Escalation**
  - `[x]` Implement the `escalate_to_frontier` tool. *(Real Anthropic API call in `GrugRouter.execute_frontier_escalation`, not a stub. Uses `CLAUDE_MODEL` env var, defaults to `claude-opus-4-6`.)*
  - `[x]` Embed logic to check for the Claude API key. *(`frontier_available` flag derived from `CLAUDE_API_KEY` at router init.)*
  - `[x]` If key is missing/invalid, trigger the Graceful Failover path. *(Re-prompts Gemma with `SYSTEM WARNING: The frontier model is OFFLINE` marker and returns a "Degraded Response:" envelope.)*

- `[x]` **Phase 5: Prompts & Caveman Compression Mode**
  - `[x]` Update `prompts/system.md` and `prompts/rules.md` to establish the Compression Gauge.
  - `[x]` Update the python router to dynamically compile the system prompt. *(`load_prompt_files` concatenates all four prompt files; `build_system_prompt` substitutes both `{{COMPRESSION_MODE}}` and `{{CURRENT_DATE}}`.)* Compression mode is currently hardcoded to `FULL` at the call site — follow-up tracked as M11 in followups.

- `[x]` **Phase 6: Integration & Final Verification**
  - `[x]` Wire the orchestrator logic into the existing Slack bot endpoint structure.
  - `[x]` Execute end-to-end Slack test for capture. *(Covered by `test_grug.py::test_1_caveman_storage_flow` with real assertions.)*
  - `[x]` Execute end-to-end Slack test for "complex synthesis" with offline fallback. *(Covered by `test_grug.py::test_2_graceful_offline_degradation` and `test_4_confidence_score_forces_escalation`.)*
