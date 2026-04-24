# OpenGrug Evolution Roadmap

This roadmap outlines the path to transforming OpenGrug from a reactive Slack bot into an autonomous, Obsidian-integrated second brain capable of daily briefs and background research. 
It follows a strict "stabilize first, expand second" philosophy to maintain Grug-brained simplicity while adhering to SOLID and DRY principles.

## Phase 1: Harden the Foundation (Immediate Priority)
Before introducing background loops and multi-step reasoning, the core must be thread-safe and resilient.

**Key Actions (from `bugs.md` and `simplify_overcomplexity.md`):**
1. **Fix Concurrency & State Bugs:** Resolve Bug 9 (HITL Double-Execution) and Bug 11 (`open("w")` truncating files) to prevent data loss when background workers start accessing the same files as the Slack UI.
2. **Patch Injection Vectors:** Apply `_sanitize_untrusted()` in `storage.append_log` (Bug 10) and escape `<` completely (Simplify #2) so that external research data (like RSS feeds) doesn't poison the LLM's prompt.
3. **Remove Faux-Resilience:** Remove silent exception swallowing (Simplify #5) and fallback LLM calls (Simplify #3). Let errors surface clearly so they can be handled predictably.

*Why start here?* Adding asynchronous workers on top of existing race conditions or data loss bugs will make the system incredibly hard to debug.

## Phase 2: Obsidian Vault RAG Sync (Memory Layer)
Before doing any agentic logic, the AI's memory must be able to scale and sync with your local Obsidian vault without breaking. **See the detailed technical outline in [obsidian_rag.md](obsidian_rag.md).**

**Key Actions:**
1. **Incremental Sync:** Update `core/vectors.py` to check `os.path.getmtime` so the background worker doesn't constantly rescan the entire vault.
2. **Garbage Collection:** Remove old vector blocks when an Obsidian file is edited, preventing context poisoning.
3. **Smart Chunking & Ollama Embeddings:** Replace the "bullet-point only" logic with a true Markdown paragraph chunker, and swap the heavy RAM-hogging `sentence_transformers` for Ollama's local `/api/embeddings`.

*Why here?* RAG is the heart of Grug. If the memory layer crashes your CPU or feeds the LLM stale data, everything built on top of it will fail.

## Phase 3: Decouple the Orchestrator (SOLID Refactoring)
Currently, `core/orchestrator.py` is tightly coupled to Slack's `thread_ts` and single-turn conversational logic. **See the detailed technical outline in [core_decoupling_refactor.md](core_decoupling_refactor.md) and [solid_architecture.md](solid_architecture.md).**

**Key Actions:**
1. **Abstract the Request Context:** Refactor `process_message` to take a generic `session_id` and `metadata` interface instead of Slack-specific arguments. 
2. **Internalize Concurrency:** Push the `GrugMessageQueue` into the Orchestrator so all adapters share the same thread-safe locks.
3. **Implement the `StepLoop` / Skill Runner:** Modify the router to support the "Markdown Skill Framework" (from `backlog.md`). Instead of a strict `think-then-act` single turn, allow a bounded `while not finished and steps < max_steps` loop for complex research tasks.

*Why here?* To safely support Web UI and background workers, the engine must establish a strict "Dumb Input, Smart Core" boundary.

## Phase 4: Agent Task Queue & Autonomy
Implement the async capability so Grug can do "light work" overnight.

**Key Actions (from `agent_tasks.md`):**
1. **Create `GrugTaskQueue`:** Implement `tools/grug_tasks.py` backed by `brain/agent_tasks.md` (keeping it natively Obsidian-compatible).
2. **Nightly Processing Worker:** Add `nightly_grug_tasks_loop()` in `workers/background.py`.
3. **Connect to the StepLoop:** Have the background worker pop tasks from the queue and feed them to the newly decoupled Orchestrator, storing the markdown results directly in `brain/daily_notes/`.

*Why here?* Now that the Orchestrator can handle background requests safely (Phase 3), we can turn on the asynchronous loop without breaking the Slack experience.

## Phase 5: Multi-Agent Personas & External I/O
Give Grug the tools to research the web, but protect the small model's context window.

**Key Actions (from `backlog.md`):**
1. **Multi-Agent Prompts:** Split the "God Prompt" into specialized Personas (e.g., Dispatcher, TaskGrug, ResearcherGrug). The Dispatcher simply routes intent, then hands off to the specialized persona.
2. **Research Tools:** Implement `tools/research.py` with tools like `fetch_rss`, `search_web`, and `read_url`. Register these tools *only* to the `ResearcherGrug` persona.
3. **Daily Brief Skill:** Create a `skills/daily_brief.md` that instructs the Researcher persona to fetch news feeds, summarize them, and save them as an Obsidian note before you wake up.

*Why here?* Smaller edge models will hallucinate or fail if provided with 20 tools at once. Personas keep the prompt lightweight while unlocking massive capabilities.
