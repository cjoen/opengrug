# AI Context: Grug Architecture

<!-- last-updated: 2026-04-20 -->

**ATTENTION FELLOW AI AGENT**: If you are reading this file, the user has tasked you with debugging or extending the Grug repository. Read this context before traversing the codebase.

## System Overview
Grug is a Python-based intelligent router connecting a Slack bot interface to a local LLM (Gemma via Ollama), a local Vector Database (`sqlite-vec`), and strict CLI executables via subprocesses. There is no cloud LLM dependency. The local Ollama model is the only model.

### Core File Structure

**Entrypoint:**
- `app.py`: Wiring layer. Initializes all components, registers tools, creates the `SlackAdapter`, starts message queue and background workers. Target: thin wiring, no business logic.

**Core modules (`core/`):**
- `llm.py`: `OllamaClient` — single class that knows the Ollama HTTP API. Methods: `chat()` (`/api/chat`) and `generate()` (`/api/generate`). All modules receive this as a dependency.
- `orchestrator.py`: `Orchestrator` — the core message-processing pipeline. Owns session management, context assembly (RAG + capped tail), turn pruning, and routing. Returns platform-agnostic event dataclasses (`MessageReply`, `ApprovalRequired`, `ErrorReply`) so adapters can translate them to any UI. Also handles HITL approval flow and post-approval re-inference.
- `registry.py`: `ToolRegistry` — holds schemas, validates args via JSON Schema, enforces HITL gating on destructive tools, executes Python callables or CLI subprocesses. Also contains `ToolExecutionResult`. Tools are registered with a `category` for clarification routing.
- `router.py`: `GrugRouter` — the routing engine. Build prompt → call LLM (native tools) → dispatch tool_calls to registry. Uses Ollama's native tool calling API for multi-tool execution. Tool-output-wins precedence: if an action tool returns output, `reply_to_user` is suppressed. Writes routing traces via `storage.log_routing_trace()`.
- `queue.py`: `GrugMessageQueue` — thread-safe message queue with configurable `worker_count`. Workers drain all messages for the active thread before moving on to the next, keeping LLM context warm. Manages Slack reactions: `📬` (queued), `💭` (processing).
- `utils.py`: Shared utilities — `load_prompt_files()` (concatenates prompt .md files) and `_sanitize_untrusted()` (strips XML close-tags from untrusted input).
- `context.py`: Context assembly — `load_summary_files()`, `build_system_prompt()`, `find_turn_boundary()`, `auto_offload_pruned_turns()`.
- `storage.py`: (The Truth Layer). `GrugStorage` — appends to daily logs at `brain/daily_notes/YYYY-MM-DD.md`. Thread-safe via `threading.Lock()`.
- `vectors.py`: (The Cache Layer). `VectorMemory` — uses `SentenceTransformers` (`all-MiniLM-L6-v2`) to embed and search via `sqlite-vec`. Always-on when dependencies are available; degrades gracefully if model fails to load.
- `sessions.py`: `SessionStore` — SQLite CRUD for `sessions.db` (conversation history, pending HITL actions).
- `summarizer.py`: Three summarization modes (daily FIFO, prune auto-offload, idle session compaction). Takes `OllamaClient` as dependency.
- `scheduler.py`: `ScheduleStore` — SQLite CRUD for `schedules.db`. Supports cron expressions (recurring) and ISO datetime (one-shot). Uses `croniter`.
- `config.py`: `GrugConfig` — reads `grug_config.json`, exposes settings via dot notation. Sections: `llm`, `memory`, `storage`, `shortcuts`, `scheduler`, `queue`. Centralizes env var overrides (`DOCKER`, `OLLAMA_HOST`).

**Tool modules (`tools/`):**
- `notes.py`: `add_note()`, `get_recent_notes()`, `query_memory()`, `search()` — note storage, retrieval, and search. `add_note` auto-generates titles for longer notes via LLM.
- `search.py`: `search()` — keyword search across all notes, summaries, and tasks.
- `tasks.py`: `TaskList` class — `add_task()`, `list_tasks()`, `complete_task()`. Backed by `brain/tasks.md` (Obsidian-friendly markdown). Position numbers are assigned dynamically at display time, not stored.
- `system.py`: `ask_for_clarification()`, `reply_to_user()`, `list_capabilities()` — system/meta tools.
- `scheduler_tools.py`: `add_schedule()`, `list_schedules()`, `cancel_schedule()` — scheduler tool functions.
- `health.py`: `grug_health()`, `system_health()` — internal and infrastructure health checks.
- `TOOL_GUIDE.md`: Template and guide for implementing new tools. Covers function pattern, registration, schema design, and dependency injection.

**Background workers (`workers/`):**
- `background.py`: `boot_summarize()`, `idle_sweep_loop()`, `nightly_summarize_loop()`, `scheduler_poll_loop()`. All take explicit dependencies.

**Adapters (`adapters/`):**
- `slack.py`: `SlackAdapter` — thin layer that wires Slack Bolt events to `Orchestrator.process_message()` and translates returned events (`MessageReply`, `ApprovalRequired`, `ErrorReply`) into Slack API calls (Block Kit for approve/deny buttons, threaded messages, ephemeral messages). All Slack-specific UI lives here.

**Other:**
- `grug_config.json`: Externalized tuning parameters.
- `prompts/`: System prompt files — `system.md`, `rules.md`, `schema_examples.md`.
- `scripts/test_prompts.py`: Offline prompt regression test harness (requires live Ollama).
- `tests/`: Structured `pytest` suite covering core modules (e.g., `test_router.py`, `test_registry.py`), `conftest.py` fixtures, and YAML-driven `prompt_fixtures.yaml` for routing validation.

### Tool Categories
Tools are registered with a `category` parameter. Categories and their descriptions are registered on the `ToolRegistry`:
- `NOTES` — `add_note`, `get_recent_notes`, `query_memory`, `search`
- `TASKS` — `add_task`, `list_tasks`, `complete_task`
- `SYSTEM` — `ask_for_clarification`, `reply_to_user`, `list_capabilities`, `grug_health`, `system_health`
- `SCHEDULE` — `add_schedule`, `list_schedules`, `cancel_schedule`

### Scheduler System
Reminders are scheduled `reply_to_user` calls. Cron jobs execute any registered tool on a recurring schedule. The `scheduler_poll_loop` worker checks for due jobs every 60 seconds, executes them via `registry.execute()`, posts results to Slack, and advances recurring jobs or deletes one-shots.

### Core Rules for Building & Debugging
1. **SQLite is used for three purposes:** (1) Volatile VSS vector cache in `memory.db`. (2) Ephemeral session state in `sessions.db`. (3) Persistent schedules in `schedules.db`. The Truth Layer (Markdown files in `brain/daily_notes/`) remains the canonical source for notes and logs.
2. **Never allow arbitrary bash execution**. Use `registry.register_cli_tool()` so the orchestrator maps keys directly to `--args` safely. CLI args with `--`-prefixed values are rejected, and a `--` separator is appended.
3. **Persist the Caveman Mode**: If modifying prompt compilers, ensure `{{COMPRESSION_MODE}}` and `{{CURRENT_DATE}}` interpolation remains intact.
4. **Native Tool Calling**: The LLM uses Ollama's native `/api/chat` tools format instead of JSON forcing. The `router` receives a list of `tool_calls` which it executes sequentially and combines the outputs. This enables reliable multi-tool responses in a single turn without brittle schema mapping.
5. **Message queue**: Incoming Slack messages are enqueued, not processed inline. Workers drain all messages for one thread before moving to the next. This prevents race conditions on shared session state and keeps context warm across message bursts. `worker_count` is configurable (default 1, matching single-model Ollama).
6. **Environment**: Runs locally or in Docker via `.env`. Package requirements are handled via `Dockerfile` and `requirements.txt` (pinned versions).
7. **No frontier escalation**: No Anthropic/Claude API dependency. If the LLM is unsure, it calls `ask_for_clarification`. If Ollama is unreachable, `OllamaClient.chat()` returns a safe fallback response.
8. **Single LLM client**: All LLM calls go through `OllamaClient`. Never call Ollama HTTP directly from other modules.
9. **Dependency injection**: Modules receive their dependencies via constructor args. Config is the only singleton (`from core.config import config`).
