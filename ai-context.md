# AI Context: Grug Architecture

<!-- last-updated: 2026-04-14 | git-ref: queue-and-think-then-act -->

**ATTENTION FELLOW AI AGENT**: If you are reading this file, the user has tasked you with debugging or extending the Grug repository. Read this context before traversing the codebase.

## System Overview
Grug is a Python-based intelligent router connecting a Slack bot interface to a local LLM (Gemma via Ollama), a local Vector Database (`sqlite-vec`), and strict CLI executables via subprocesses. There is no cloud LLM dependency. The local Ollama model is the only model.

### Core File Structure

**Entrypoint:**
- `app.py`: Wiring layer. Initializes all components, registers tools, defines Slack event/HITL handlers, starts message queue and background workers. The Slack `message` handler enqueues to `GrugMessageQueue`; `process_message()` is the worker callback. Target: thin wiring, no business logic.

**Core modules (`core/`):**
- `llm.py`: `OllamaClient` — single class that knows the Ollama HTTP API. Methods: `chat()` (`/api/chat`) and `generate()` (`/api/generate`). All modules receive this as a dependency.
- `registry.py`: `ToolRegistry` — holds schemas, validates args via JSON Schema, enforces HITL gating on destructive tools, executes Python callables or CLI subprocesses. Also contains `ToolExecutionResult`, `load_prompt_files()`, and `_sanitize_untrusted()`. Tools are registered with a `category` for clarification routing.
- `router.py`: `GrugRouter` — the routing engine. Shortcut check → build prompt → call LLM → parse JSON → dispatch to registry. Supports **think-then-act** response format: `{"thinking": "...", "actions": [...]}` with multi-tool execution. Also accepts legacy single-tool format for backwards compatibility. Registers core system tools (clarification, reply, capabilities). Hot-reloads prompt files on mtime change. Writes routing traces (including thinking + all actions) to `brain/routing_trace.jsonl`.
- `queue.py`: `GrugMessageQueue` — thread-safe message queue with configurable `worker_count`. Workers drain all messages for the active thread before moving on to the next, keeping LLM context warm. Manages Slack reactions: `👀` (queued), `💭` (processing).
- `context.py`: Context assembly — `load_summary_files()`, `build_system_prompt()`, `find_turn_boundary()`, `auto_offload_pruned_turns()`.
- `storage.py`: (The Truth Layer). `GrugStorage` — appends to daily logs at `brain/daily_notes/YYYY-MM-DD.md`. Thread-safe via `threading.Lock()`.
- `vectors.py`: (The Cache Layer). `VectorMemory` — uses `SentenceTransformers` (`all-MiniLM-L6-v2`) to embed and search via `sqlite-vec`. Always-on when dependencies are available; degrades gracefully if model fails to load.
- `sessions.py`: `SessionStore` — SQLite CRUD for `sessions.db` (conversation history, pending HITL actions).
- `summarizer.py`: Three summarization modes (daily FIFO, prune auto-offload, idle session compaction). Takes `OllamaClient` as dependency.
- `scheduler.py`: `ScheduleStore` — SQLite CRUD for `schedules.db`. Supports cron expressions (recurring) and ISO datetime (one-shot). Uses `croniter`.
- `config.py`: `GrugConfig` — reads `grug_config.json`, exposes settings via dot notation. Sections: `llm`, `memory`, `storage`, `shortcuts`, `scheduler`, `queue`. Centralizes env var overrides (`DOCKER`, `OLLAMA_HOST`).

**Tool modules (`tools/`):**
- `notes.py`: `add_note()`, `get_recent_notes()` — note storage and retrieval.
- `tasks.py`: `TaskBoard` class — `add_task()`, `list_tasks()`, `edit_task()`, `summarize_board()`. Backed by `brain/tasks.md`.
- `system.py`: `ask_for_clarification()`, `reply_to_user()`, `list_capabilities()` — system/meta tools.
- `scheduler_tools.py`: `add_schedule()`, `list_schedules()`, `cancel_schedule()` — scheduler tool functions.

**Background workers (`workers/`):**
- `background.py`: `boot_summarize()`, `idle_sweep_loop()`, `nightly_summarize_loop()`, `scheduler_poll_loop()`. All take explicit dependencies.

**Other:**
- `grug_config.json`: Externalized tuning parameters.
- `prompts/`: System prompt files — `system.md`, `rules.md`, `schema_examples.md`, `argument_extraction.md`.
- `scripts/test_prompts.py`: Offline prompt regression test harness (requires live Ollama).
- `tests/prompt_fixtures.yaml`: YAML-driven test cases for prompt routing validation.

### Tool Categories
Tools are registered with a `category` parameter. Categories and their descriptions are registered on the `ToolRegistry`:
- `NOTES` — `add_note`, `get_recent_notes`, `query_memory`
- `TASKS` — `add_task`, `list_tasks`, `edit_task`, `summarize_board`
- `SYSTEM` — `ask_for_clarification`, `reply_to_user`, `list_capabilities`
- `SCHEDULE` — `add_schedule`, `list_schedules`, `cancel_schedule`

### Scheduler System
Reminders are scheduled `reply_to_user` calls. Cron jobs execute any registered tool on a recurring schedule. The `scheduler_poll_loop` worker checks for due jobs every 60 seconds, executes them via `registry.execute()`, posts results to Slack, and advances recurring jobs or deletes one-shots.

### Core Rules for Building & Debugging
1. **SQLite is used for three purposes:** (1) Volatile VSS vector cache in `memory.db`. (2) Ephemeral session state in `sessions.db`. (3) Persistent schedules in `schedules.db`. The Truth Layer (Markdown files in `brain/daily_notes/`) remains the canonical source for notes and logs.
2. **Never allow arbitrary bash execution**. Use `registry.register_cli_tool()` so the orchestrator maps keys directly to `--args` safely. CLI args with `--`-prefixed values are rejected, and a `--` separator is appended.
3. **Persist the Caveman Mode**: If modifying prompt compilers, ensure `{{COMPRESSION_MODE}}` and `{{CURRENT_DATE}}` interpolation remains intact.
4. **Think-then-act response format**: The LLM outputs `{"thinking": "...", "actions": [...]}`. The `thinking` field gives the model reasoning room before tool selection. `actions` is an array enabling multi-tool responses in a single turn. The router executes actions sequentially and combines outputs. Legacy single-tool format (`{"tool": ..., "arguments": ...}`) is still accepted.
5. **Message queue**: Incoming Slack messages are enqueued, not processed inline. Workers drain all messages for one thread before moving to the next. This prevents race conditions on shared session state and keeps context warm across message bursts. `worker_count` is configurable (default 1, matching single-model Ollama).
6. **Environment**: Runs locally or in Docker via `.env`. Package requirements are handled via `Dockerfile` and `requirements.txt` (pinned versions).
7. **No frontier escalation**: No Anthropic/Claude API dependency. Low-confidence responses trigger `ask_for_clarification`. If Ollama is unreachable, `OllamaClient.chat()` returns a safe fallback JSON.
8. **Single LLM client**: All LLM calls go through `OllamaClient`. Never call Ollama HTTP directly from other modules.
9. **Dependency injection**: Modules receive their dependencies via constructor args. Config is the only singleton (`from core.config import config`).
