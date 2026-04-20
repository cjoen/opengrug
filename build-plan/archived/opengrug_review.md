# OpenGrug Project Review & Critique

Following a deep dive into the `opengrug` repository, here is an architectural assessment to keep the project slim, simple, and "Grug-brained" while better aligning with SOLID principles and standard practices like Pytest.

The existing codebase shines through its dependency injection inside `app.py` and its stateless, strictly validated `ToolRegistry`. However, there are significant bottlenecks in how logic is pooled and files are organized. The critique is split into two actionable parts: **Code Refactor** and **Project Organization**.

---

## Part 1: Code Refactor (Structural Logic & SOLID Violations)

The first step to reducing bloat is untangling the code execution paths. Right now, to keep file-count low, disparate concepts have been jammed into God components.

### 1. Decoupling the `app.py` Entrypoint
At 500+ lines, `app.py` is severely overloaded. Moving the `process_message` logic out is a start, but true decoupling requires treating three distinct violations:
- **Hardcoded UI (Slack UI adapter):** The core routing engine builds Slack Block Kit JSON objects inline. **Fix:** Create a `SlackAdapter` class. The core `Orchestrator` returns pure state representations (e.g. `ApprovalRequired`), and the adapter dictates how to translate that to Slack buttons. This ensures the core engine is platform-agnostic.
- **The Wall of Tool Registrations:** `app.py` directly defines schemas for 15+ sub-tools. **Fix:** Let modules register themselves. `tools/tasks.py` should expose `register_task_tools(registry)`, removing 200+ lines of config code from the entrypoint.
- **HITL (Human-in-the-Loop) State Logic:** Re-inference heavily dictates session state manipulation inside the Slack `@action` callback. **Fix:** The `Orchestrator` must own `execute_approved_action()`, shrinking the Slack receiver to a thin wrapper.

### 2. The Router Does Non-Routing Things (SRP/OCP Violation)
`core/router.py` tries to handle too much outside of pure routing.
- **The Issue:**
  1. It uses `os.makedirs` + `with open("brain/routing_trace.jsonl", "a")` to log traces silently on the hot-path.
  2. It contains an inline recursive scan checking filesystem `.st_mtime` to hot-reload prompts on *every single request*.
  3. It mutates its injected dependency inside the constructor by registering built-in tools (`ask_for_clarification`).
- **The Grug Fix:** Strip the file I/O out. Have the Router accept a logger abstraction or let `storage.py` handle traces. Remove constant prompt hot-reloading (just require process reboot or add a `/reload` tool; heavy I/O loops are bad). Register core tools in `app.py` alongside tasks/notes. 

### 3. Utilities Leaking into Core Components
- **The Issue:** `core/registry.py` contains `load_prompt_files()` and `_sanitize_untrusted()`. A Tool Registry's sole responsibility is maintaining schemas and executing them securely.
- **The Grug Fix:** Extract these loose string manipulation methods into a separate `core/utils.py` or move them to `core/context.py` where prompt building naturally lives.

---

## Part 2: Project Organization (Preparing for Pytest)

The current layout sits at the root, making testing and modularity difficult to navigate as the project scales. Reorganizing the layout is a quick win.

### 1. Migrate to a standard `tests/` Directory
- **The Issue:** You have `test_grug.py`, a massive 860+ line file sitting at the root directory containing 45 different tests (LLM mocking, concurrent storage threads, routing schema checks, CLI flag safety configs).
- **The Grug Fix:** Pytest expects, and thrives on, a hierarchical `tests/` folder. Delete `test_grug.py` from the root and split it logically:
  - `tests/test_storage.py` (Concurrency logs and capped tails)
  - `tests/test_router.py` (LLM format ingestion and route tracing)
  - `tests/test_registry.py` (Schema validations, CLI flags, HITL gates)
  - `tests/test_queue.py` (Thread locking and batching tests)
- **Benefit:** `pytest tests/` will now parallelize naturally, and it makes finding the source of a break incredibly obvious rather than digging to line 657 of `test_grug.py`.

### 2. Centralizing the Prompts Context
- **The Issue:** System logic relies on loose markdown chunks. While effective, ensuring they don't drift or lose tests is important.
- **The Grug Fix:** Keep `prompts/` as is, but alongside moving the prompt loaders (`load_prompt_files()`) into `core/context.py`, add explicit fixtures in the new `tests/` tree that validate placeholders like `{{COMPRESSION_MODE}}` are rendered cleanly.

### 3. Move the Storage "Brain" Path Controls
- **The Issue:** Paths like `brain/daily_notes/` or `tasks.md` are sometimes hardcoded in scripts or the router trace logs, but passed organically via `config.json` elsewhere.
- **The Grug Fix:** Anchor all test path generations and artifacts firmly inside a top-level `conftest.py` setup for Pytest. This ensures running tests doesn't accidentally spray actual `brain/` directories locally.

---

## Implementation Blueprint

When you are ready to execute these changes, follow this execution order to minimize breakage:

1. [x] **Test Migration:** Delete `test_grug.py` and break it into `tests/test_router.py`, `test_registry.py`, etc. Get `pytest` running green.
2. [x] **Tool Delegation:** Move tool dictionary schemas out of `app.py` and into `register_tools()` functions inside `tools/notes.py`, `tools/tasks.py`, etc.
3. [x] **UI Decoupling:** Extract the Block Kit lists from `app.py` into a new `adapters/slack.py` script.
4. [x] **Orchestrator Move:** Rip `process_message` out of `app.py` and move it into `core/orchestrator.py`. Ensure the Orchestrator returns pure Event objects rather than Slack blocks. 
5. [x] **Router Cleanup:** Remove the `os.makedirs` and `_check_prompt_reload` functions from `GrugRouter`.
