# OpenGrug Evolution Roadmap

This roadmap outlines the path to transforming OpenGrug from a reactive Slack bot into an autonomous, Obsidian-integrated second brain capable of daily briefs and background research. 
It follows a strict "stabilize first, expand second" philosophy to maintain Grug-brained simplicity while adhering to SOLID and DRY principles.

## ~~Phase 1: Harden the Foundation~~ ✅ Complete (2026-04-23)
Completed in commit `1bda2be`. Fixed Bug 9 (HITL double-execution via atomic claim), Bug 11 (atomic file rewrite), Bug 10 (append_log sanitization), Simplify #2 (escape `<`), Simplify #5 (remove silent exception swallowing), Simplify #3 (remove fallback LLM call).

## ~~Phase 2: Obsidian Vault RAG Sync (Memory Layer)~~ ✅ Complete (2026-04-24)
Completed in commit `75af46c`. Replaced SentenceTransformer with Ollama `/api/embeddings`, implemented incremental mtime-based sync with debounce, paragraph-level markdown chunking, garbage collection for edited/deleted files, dynamic embedding dimension probing. See [obsidian_rag.md](obsidian_rag.md) for the original plan.

## ~~Phase 3: Decouple the Orchestrator (SOLID Refactoring)~~ ✅ Complete (2026-04-24)
Completed in commit `75af46c`. Abstracted `session_id` + `metadata` interface, internalized queue into Orchestrator with `enqueue()`/`start()`, genericized queue (no Slack code), updated adapter to use callbacks, implemented bounded StepLoop with circuit breaker in router. See [core_decoupling_refactor.md](core_decoupling_refactor.md) for the original plan.

## ~~Phase 4: Agent Task Queue & Autonomy~~ ✅ Complete (2026-04-24)
Completed in commit `75af46c`. Created `GrugTaskQueue` in `tools/grug_tasks.py` backed by `brain/agent_tasks.md`, nightly processing worker (3 AM) in `workers/background.py`, wired to decoupled Orchestrator. See [agent_tasks.md](agent_tasks.md) for the original plan.

## Phase 5: Sub-Agent Router & Priority Queue Engine
Overhaul OpenGrug from a simple chatbot with background tasks into a true Asynchronous OS, using CI/CD-style worker configurations and a priority task queue. See [sub_agent_router_prd.md](sub_agent_router_prd.md) for the full architecture blueprint.

**Implementation Phases:**
1. ~~**[Phase 5.1: Workers & Config Foundation](archive/phase_5_1_workers_config.md):**~~ ✅ Complete (2026-05-03). Replaced `config.llm` with `config.workers` multi-tier system. Split `LLMClient` into `ChatWorker` + `EmbeddingWorker` ABCs with concurrency semaphores. Implemented `WorkerFactory.create_all()`. Migrated all consumers. Stubbed `GeminiChatWorker`. Zero ghost artifacts.
2. ~~**[Phase 5.2: Agents, Prompts & Scoped Registries](archive/phase_5_2_agents_prompts.md):**~~ ✅ Complete (2026-05-04, commit `f8d8e89`). Split prompts into `base.md` + per-agent files. `AgentContainer` with scoped `ToolRegistry` and `VectorMemory`. `AgentFactory` from config. Multi-DB RAG support.
3. ~~**[Phase 5.3: Queue Engine & Dispatcher](archive/phase_5_3_queue_dispatcher.md):**~~ ✅ Complete (2026-05-05, commit `713df1a`). Priority queue with session affinity, URGENT same-session batching, BACKGROUND off-hours gating, watchdog cancellation. Dispatcher-driven Plan-and-Execute orchestrator with chat/expert agent paths.
4. ~~**[Phase 5.4: Migration, Observability & Hardening](archive/phase_5_4_migration_observability.md):**~~ ✅ Complete (2026-05-06). Migrated `scheduler_poll_loop` and `nightly_grug_tasks_loop` to Task producers. Added `core/dlq.py` (markdown-backed DLQ with retry budget), `workers/monitor.py` (health dashboard + transition alerts), and `tools/operator.py` (queue_status / retry_dlq / clear_dlq / drain_queue / cancel_task, with HITL on destructive ops). Full `ai-context.md` rewrite.
5. ~~**[Phase 5.5: Hardening & Cleanup](archive/phase_5_5_hardening.md):**~~ ✅ Complete (2026-05-06). Bugs: URGENT batch FIFO ordering, cancelled tasks no longer pollute session history, session-lock reaping. Refactors: Dispatcher LLM call moved to a dedicated thread (Slack ingress is now non-blocking), `_run_chat_legacy` collapsed into `_execute_with_session`, `router.request_state` context manager replaces open-coded threadlocals. Plus deterministic scheduled-tool execution (no LLM round-trip for cron jobs).
6. **[Phase 5.6: Resilience Improvements](phase_5_6_resilience.md):** Close the gaps surfaced by the 5.4/5.5 code review. Resilience under failure, not new features.
   - **Retry backoff:** add `Task.not_before` + exponential delay in `_try_retry` so flaky workers aren't hammered.
   - **Dispatch worker pool:** replace the single dispatch daemon with a fixed-size pool (2–4) so one slow LLM call doesn't block all users.
   - **`WorkerHealth` protocol:** structured health reported by each worker; monitor stops grepping error strings.
   - **Worker circuit breaker:** track consecutive failures in `WorkerPool`; mark unhealthy + skip dispatch when threshold tripped (pairs with `WorkerHealth`).
   - **Scheduled-tool HITL policy:** filter scheduled execution by `ToolCategory` (skip DESTRUCTIVE) or require explicit `allow_unattended=True` on the schedule entry.
   - **Retry traceability:** preserve original task identity on DLQ retry (e.g., `root_task_id` field, `attempt=N`) so logs and operator tools group retries.
   - **Retry-success integration test:** failing chat_worker → DLQ → `retry_dlq` → success.

*Why here?* To safely run complex background research tasks without locking up the bot during active chat, we must migrate to a non-blocking, priority-based architecture before adding heavy web scraping and multi-agent flows.

## Phase 6: Architectural Cleanup (post-5.6 code review)
Internal-only refactors surfaced by the 2026-05-06 KISS/DRY/SOLID review. No PRD-promised behavior changes — all features remain. Each item below is a future build plan; ordering is smallest blast radius first.

**Standalone plans:**
1. **Drop dead `--` sentinel in CLI tool execution** (`core/registry.py`). Reject any `-` prefix on CLI arg values; remove the useless trailing `--`. Closes the open §1 item in `simplify_overcomplexity.md`.
2. **`RegisteredTool` dataclass in `ToolRegistry`.** Replace 5-tuple storage and magic indices (`[0]`, `[2]`, `[4]`) with a named dataclass. Collapse `_python_tools` / `_cli_tools` parallel dicts. Sets up later refactors.
3. **Category-driven router branching.** Replace hard-coded `_chat_tools = {"ask_for_clarification", "reply_to_user"}` in `core/router.py` with a category check (`REPLY`-style). Removes OCP violation.
4. **`Orchestrator` god-class split** (`core/orchestrator.py`, 427 LOC). Extract `TaskExecutor` (chat-agent path, expert-agent path, prompt build, turn pruning) and leave `Orchestrator` as ingress + wiring. Largest readability win.

**Bundled plans:**
5. **Dependency wiring cleanup** (combines #4, #5, #6 from review). Introduce a `ToolDeps`/`OrchestrationContext` injected once. Removes the late-bind `holder` dict in `tools/dispatch.py`, retires the `_request_state` threadlocal stamping in `core/router.py`, and unifies the ad-hoc `register_tools(...)` signatures across `tools/*.py`. All three are the same circular-wiring problem at different layers — fix together.
6. **Orchestrator slimming** (combines #8, #9 from review). Extract `ScheduledToolRunner` out of `Orchestrator._run_scheduled_tool`. Verify PRD §8 `nightly_grug_tasks_loop` migration is complete; if so, drop the synchronous `Orchestrator.process_message` shim. Both shrink the orchestrator surface and touch the same wiring.

**Singletons:**
7. **Retry timer leak.** Replace `threading.Timer` per retry in `core/task_queue.py:_try_retry` with a `not_before_ts` field on `Task` and a heap-based delay check. Survives shutdown; removes untracked daemon timers.

*(Dropped from review:* dispatcher native JSON mode — tried, response quality regressed.*)*
