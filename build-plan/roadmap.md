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

## Phase 5: Multi-Agent Personas & External I/O
Give Grug the tools to research the web, but protect the small model's context window.

**Key Actions (from `backlog.md`):**
1. **Multi-Agent Prompts:** Split the "God Prompt" into specialized Personas (e.g., Dispatcher, TaskGrug, ResearcherGrug). The Dispatcher simply routes intent, then hands off to the specialized persona.
2. **Research Tools:** Implement `tools/research.py` with tools like `fetch_rss`, `search_web`, and `read_url`. Register these tools *only* to the `ResearcherGrug` persona.
3. **Daily Brief Skill:** Create a `skills/daily_brief.md` that instructs the Researcher persona to fetch news feeds, summarize them, and save them as an Obsidian note before you wake up.
4. **Persona-Specific Temperatures:** Bind temperature settings to the Personas (e.g., `0.0` for Router/TaskRunner for deterministic tool use, `0.6` for Summarizer/AAR for creative synthesis).

*Why here?* Smaller edge models will hallucinate or fail if provided with 20 tools at once. Personas keep the prompt lightweight while unlocking massive capabilities, and binding temperature strictly to the persona ensures edge models don't creatively guess tool schemas.
