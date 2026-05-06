# AI Context: Grug Architecture

<!-- last-updated: 2026-05-06 -->

**ATTENTION FELLOW AI AGENT**: If you are reading this file, the user has tasked you with debugging or extending the Grug repository. Read this context before traversing the codebase.

## System Overview
Grug is a Python-based multi-agent router connecting a Slack interface to one or more LLM workers (local Ollama and/or Gemini), a vector RAG layer (`sqlite-vec`), and CLI executables. Inbound messages are classified by a `Dispatcher` and routed to per-agent containers (`chat_agent`, `researcher`, etc.) over a priority Task queue. There is no required cloud dependency — Ollama is the default.

### Core File Structure

**Entrypoint:**
- `app.py`: Wiring layer. Initializes worker pool, RAG pool, agents, queue, DLQ, monitor, and Slack adapter. Target: thin wiring, no business logic.

**Core modules (`core/`):**
- `backends/`: Worker backends. `ollama.py` (chat + embedding), `gemini.py`, and `factory.py` which builds the `worker_pool` (e.g., `local-fast`, `local-slow`, `embedder`) from `grug_config.json`. Workers expose `chat()` / `generate()` / `embed()` and `health_check()`.
- `agents.py`: `AgentContainer` and `AgentFactory.create_all()` — builds per-agent objects with their own scoped `ToolRegistry`, RAG database, base prompt, and worker tier.
- `dispatcher.py`: `Dispatcher.classify()` — runs the routing LLM call to pick an agent and produce a distilled context + plan for clean-slate (expert) agents.
- `task.py`: `Task` dataclass + `TaskPriority` (URGENT, BACKGROUND) + `TaskState` machine (QUEUED → RUNNING → COMPLETED/FAILED/CANCELLED). Cooperative cancellation via `cancel_event`; per-task `max_run_time` watchdog.
- `task_queue.py`: `TaskQueue` — heap-based priority queue with session-affinity locking, URGENT same-session batching, BACKGROUND off-hours gating, watchdog timers, retry budget, and DLQ routing on terminal failure.
- `dlq.py`: `DeadLetterQueue` — append-only markdown log at `brain/failed_tasks.md`. Stores failed/cancelled task state for inspection or retry. Methods: `add()`, `list_failed()`, `remove()`, `clear()`, `size()`.
- `orchestrator.py`: `Orchestrator` — owns the `TaskQueue`, classifies messages via the `Dispatcher`, executes tasks against `AgentContainer`s. Two execution paths: chat-agent (full session history, scoped registry) and expert-agent (clean-slate with distilled context + plan). Returns event dataclasses (`MessageReply`, `ApprovalRequired`, `ErrorReply`).
- `registry.py`: `ToolRegistry` — schemas, JSON-Schema validation, HITL gating on destructive tools, and `create_scoped()` for per-agent registries.
- `router.py`: `GrugRouter` — runs the agent's StepLoop: build prompt → call worker → dispatch tool_calls. Tool-output-wins precedence; writes routing traces to `brain/routing_trace.jsonl`.
- `vectors.py`: `VectorMemory` and `create_rag_pool()` — RAG over `sqlite-vec`. Multi-DB: each agent can have its own RAG corpus (e.g., `core_memory`, `research`).
- `sessions.py`: `SessionStore` — SQLite CRUD for `sessions.db` (history + pending HITL).
- `summarizer.py`: Daily, prune-offload, idle compaction, and AAR summarization. Holds a `chat_worker` reference.
- `scheduler.py`: `ScheduleStore` — cron + one-shot schedules in `schedules.db`.
- `config.py`: `GrugConfig` — reads `grug_config.json`. Sections: `workers`, `agents`, `rag`, `dispatcher`, `queue`, `memory`, `storage`, `scheduler`, `grug_tasks`.
- `context.py`, `utils.py`, `interfaces.py`: Prompt assembly, sanitization, abstract worker interface.

**Tool modules (`tools/`):**
- `notes.py`, `tasks.py`, `search.py`, `system.py`, `health.py`, `scheduler_tools.py`, `instructions.py`, `dispatch.py`, `grug_tasks.py`: Per-agent tools registered in the global `ToolRegistry` and exposed to agents via scoped registries.
- `operator.py`: Operator tools — `queue_status`, `retry_dlq`, `clear_dlq`, `drain_queue`, `cancel_task`. Replaces the legacy CLI in `scripts/system_utils.py`. Destructive ones (`clear_dlq`, `drain_queue`, `cancel_task`) are HITL-gated.
- `dispatch.py`: `dispatch_task` — lets `chat_agent` enqueue a task for an expert agent.
- `TOOL_GUIDE.md`: Tool authoring reference.

**Background workers (`workers/`):**
- `background.py`:
  - `boot_summarize`, `idle_sweep_loop`, `nightly_summarize_loop` — summarization workers using `Summarizer` (which holds a `chat_worker`, so the worker's semaphore enforces GPU concurrency).
  - `scheduler_poll_loop(schedule_store, task_queue, config)` — translates due schedules into URGENT `Task`s. No direct Slack calls; result delivery flows through the chat_agent and adapter callback path.
  - `nightly_grug_tasks_loop(grug_task_queue, task_queue, storage, config)` — drains pending grug-tasks into the priority queue as BACKGROUND tasks; the off-hours window in the queue handles dispatch timing.
- `monitor.py`: `health_monitor_loop(worker_pool, task_queue, dlq, alert_callback, config)` — plain Python thread. Polls each worker's `health_check()`, queue depth, and DLQ size. Writes `brain/system_health.md` (Obsidian dashboard). Calls `alert_callback(message)` on transitions (worker degraded/recovered, DLQ over threshold). Never imports Slack.

**Adapters (`adapters/`):**
- `slack.py`: `SlackAdapter` — translates Slack Bolt events to `Orchestrator.enqueue()` and renders returned events back into Slack API calls (Block Kit, threaded messages, ephemeral). The Slack alert callback for the health monitor is wired here as well.

### Dispatcher → Agent Flow
1. Inbound message arrives at the adapter and is forwarded to `Orchestrator.enqueue()`.
2. The `Dispatcher` classifies the message: `(agent_name, distilled_context, plan)`.
3. A `Task` is built with the classification result and pushed onto the priority queue.
4. A queue worker dequeues the task (URGENT first; URGENT same-session batches collapse). The session-affinity lock guarantees no two workers run the same session concurrently.
5. The orchestrator's `_run_task()` selects the chat or expert path, runs it through the agent's scoped `ToolRegistry`, and emits an event via `task.on_result`.
6. On terminal failure, the queue retries up to `max_retries` times (default 1), then routes the task to the DLQ.

### Worker / Agent / RAG Config
- `workers.<tier>.{backend, model, concurrency, target_context_tokens, ...}` — each tier is a worker process with its own concurrency semaphore.
- `agents.<name>.{worker_tier, base_prompt, tools, rag}` — each agent picks a worker tier, scopes a tool subset, and binds a RAG corpus.
- `rag.<name>.{db_path, watch_dirs, embedder_tier}` — each named corpus is its own `sqlite-vec` database with its own indexer.
- `dispatcher.worker_tier` — which tier classifies inbound messages.
- `queue.{worker_count, background_window, max_retries, health_poll_seconds}` — queue concurrency and behavior knobs.

### Tool Categories
Tools register a `category` displayed during clarification routing.
- `NOTES`, `TASKS`, `SYSTEM`, `SCHEDULE`, `SELF`, `OPERATOR`, `DISPATCH`.

### Session Compaction
Slack thread sessions are stored in SQLite (`sessions.db`), keyed by `thread_ts`. The idle-sweep worker compacts sessions inactive longer than `thread_idle_timeout_hours` and appends the summary to `brain/daily_logs/` as an `idle-compaction` entry, then deletes the row.

### Scheduler System
Scheduled jobs become URGENT `Task`s when due. The `scheduler_poll_loop` enqueues them; the chat_agent path executes the configured tool and the adapter delivers the result.

### Dead Letter Queue
- Failed or cancelled tasks (after retry budget is exhausted) are written to `brain/failed_tasks.md`.
- Operators can inspect via `queue_status`, retry via `retry_dlq`, or purge via `clear_dlq` (HITL-gated).
- The format is markdown, parseable by `DeadLetterQueue.list_failed()` for re-enqueue.

### Health Monitoring
- `workers/monitor.py` writes `brain/system_health.md` every `queue.health_poll_seconds`.
- Alerts fire on transitions only (no spam). The callback is provider-agnostic; the Slack adapter wires it to a configured ops channel via `GRUG_OPS_CHANNEL`.

### Operator Tools
Live in `tools/operator.py`, registered with category `OPERATOR`. Available in Slack via the normal tool dispatch path (no separate CLI). The legacy `scripts/system_utils.py` is deprecated.

### Core Rules for Building & Debugging
1. **SQLite usage:** vector caches per RAG corpus, `sessions.db`, `schedules.db`, optional `grug_tasks.db`. Markdown files under `brain/` remain the canonical source for notes, logs, DLQ, and the health dashboard.
2. **Never allow arbitrary bash execution**. Use `registry.register_cli_tool()`; values starting with `--` are rejected and a `--` separator is appended.
3. **Worker abstraction**: All LLM calls go through a `Worker` (Ollama, Gemini, ...). Never hit a backend HTTP API directly.
4. **Native Tool Calling**: Workers use the underlying provider's native tool format. `router` executes returned `tool_calls` sequentially through the scoped registry.
5. **Priority queue**: Incoming messages are classified and enqueued as `Task`s. URGENT ahead of BACKGROUND; same-session URGENTs batch; BACKGROUND tasks are gated by an off-hours window (when configured). `worker_count` controls concurrency.
6. **Cancellation**: Cooperative — `cancel_event` is set; long-running paths must check it. Watchdog enforces `max_run_time`.
7. **Failure routing**: terminal-state tasks retry up to `max_retries`, then route to the DLQ. Don't bypass — the DLQ is the operator-facing record.
8. **Dependency injection**: Modules receive their dependencies via constructor args. Config is the only singleton (`from core.config import config`).
9. **Adapter isolation**: Slack-specific code lives only in `adapters/slack.py` and the alert callback wired in `app.py`. `core/`, `workers/`, and `tools/` must not import Slack.

### Evals Pipeline
The `evals/` directory is separate from `tests/` by design:
- **`tests/`** = deterministic, fast, no LLM. Verifies code logic (queue, DLQ, registry, schema validation, monitor, operator tools). Run on every commit.
- **`evals/`** = probabilistic, slow, hits live workers. Verifies LLM reasoning (tool selection, argument extraction, injection resistance). Run when changing prompts, schemas, or models.

**When adding a new tool**, add at least one eval case to `evals/golden_dataset.jsonl` covering the happy path and one boundary/disambiguation case.
