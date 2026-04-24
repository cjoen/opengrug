# Build Plan: SOLID Architecture (Grug-Brained)

**Status:** Ready to implement
**Priority:** High (Pair with Phase 2 of the Roadmap)
**Goal:** Fix the "God File" bloat and decouple dependencies using simple, explicit Pythonic patterns (avoiding overly abstracted enterprise boilerplate) to prepare the engine for Multi-Agent and background worker expansion.

---

## 1. App Factory Pattern (Fixing `app.py` Bloat)
**The Problem:** `app.py` manually instantiates every class, registers every tool, and starts every thread. As we add WebUI and background adapters, it will become a massive, unreadable script.
**The Fix:**
- Create `core/factory.py` with a `create_engine(config)` function.
- This function explicitly wires the dependencies (Storage, LLM, VectorDB, Registry, Queue) and returns a unified `GrugEngine` object (which wraps the Orchestrator and Queue).
- **Why it supports the roadmap:** In Phase 3, an offline script or an alternative adapter can simply call `engine = create_engine(config)` to get a fully functioning, thread-safe core without needing to know how to wire 15 different database classes together.

## 2. Explicit Dependency Injection for Tools (Fixing Registration Bloat)
**The Problem:** Registering tools requires passing different combinations of core dependencies to each tool module (e.g., `register_health_tools(registry, vector_memory, session_store, message_queue, schedule_store, llm_client, base_dir)`).
**The Fix:**
- Instead of a massive `SystemContext` object (which acts as a "Service Locator" anti-pattern), update `tools/__init__.py` to expose a `register_all(registry, **dependencies)` function.
- The `register_all` function acts as the single wiring boundary, explicitly mapping only the required dependencies to each individual tool group (e.g., `register_health_tools(registry, llm_client=dependencies['llm_client'])`).
- **Why it supports the roadmap:** In Phase 4, we will add many specialized tools for Multi-Agent Personas (Web tools, RSS tools). This pattern keeps `app.py` clean (`tools.register_all(registry, **engine.get_services())`) while strictly enforcing the Principle of Least Privilege for each tool, making unit tests much easier to mock. (Note: Registering all tools at boot is safe because the *Router* will filter which tools the LLM is allowed to see based on its current Persona).

## 3. The Orchestrator Pipeline (Fixing Split Personalities)
**The Problem:** `core/orchestrator.py` mixes system prompt logic, context-window pruning, HITL state mutation, and execution.
**The Fix:**
- Refactor `process_message` into a strict linear pipeline: `Load State -> Build Context -> Execute StepLoop -> Save State`.
- Push the complex HITL approval text-matching (checking for "yes/no") completely into a `SessionStore` method (e.g., `consume_hitl_approval(text)`).
- **Why it supports the roadmap:** Phase 2 introduces the `StepLoop` (the ability for the LLM to call multiple tools in a row). A clean, linear Orchestrator pipeline allows the `StepLoop` to sit neatly in the "Execute" phase without tangling with context assembly or database saves.

## 4. The Service Layer (Fixing Background Worker Bloat)
**The Problem:** `workers/background.py` imports raw databases and handles complex business logic (like summarizing old threads and deleting SQLite rows).
**The Fix:**
- Move business logic out of the worker scripts and into standalone functions in `core/services.py` (e.g., `compact_idle_sessions(session_store, summarizer)`).
- The worker loops simply import and execute these isolated commands on a timer.
- **Why it supports the roadmap:** This ensures background maintenance tasks remain lightweight. When we build the `nightly_grug_tasks_loop` in Phase 3, we can safely distinguish between heavy "Agent Tasks" (which instantiate the full Engine) and lightweight "Maintenance Tasks" (which just run a Service function without booting the LLM context).
