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
1. ~~**[Phase 5.1: Workers & Config Foundation](phase_5_1_workers_config.md):**~~ ✅ Complete (2026-05-03). Replaced `config.llm` with `config.workers` multi-tier system. Split `LLMClient` into `ChatWorker` + `EmbeddingWorker` ABCs with concurrency semaphores. Implemented `WorkerFactory.create_all()`. Migrated all consumers. Stubbed `GeminiChatWorker`. Zero ghost artifacts.
2. **[Phase 5.2: Agents, Prompts & Scoped Registries](phase_5_2_agents_prompts.md):** Split prompts into `base.md` + per-agent files. Create `AgentContainer` with scoped `ToolRegistry` and scoped `VectorMemory`. Build `AgentFactory` from config. Multi-DB RAG support.
3. **[Phase 5.3: Queue Engine & Dispatcher](phase_5_3_queue_dispatcher.md):** Rewrite the queue as a priority Task queue with session affinity and message batching. Refactor the Orchestrator into a Dispatcher-driven Plan-and-Execute engine. Implement the Agent Result Return Path.
4. **[Phase 5.4: Migration, Observability & Hardening](phase_5_4_migration_observability.md):** Migrate background workers to Task producers. Implement Dead Letter Queue, health monitoring, operator CLI tools. Full documentation update.

*Why here?* To safely run complex background research tasks without locking up the bot during active chat, we must migrate to a non-blocking, priority-based architecture before adding heavy web scraping and multi-agent flows.
