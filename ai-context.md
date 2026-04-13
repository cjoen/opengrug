# AI Context: Grug Architecture

<!-- last-updated: 2026-04-13 | git-ref: f130949 -->

**ATTENTION FELLOW AI AGENT**: If you are reading this file, the user has tasked you with debugging or extending the Grug repository. Read this context before traversing the codebase.

## System Overview
Grug is a Python-based intelligent router connecting a Slack bot interface to a local LLM (specifically edge models like Gemma), a local Vector Database (`sqlite-vss`), and strict CLI executables via subprocesses. There is no cloud LLM dependency — the Anthropic/Claude frontier escalation path was removed. The local Ollama model is the only model.

### Core File Structure
- `app.py`: The main entrypoint. Listens via Slack Bolt using `SocketModeHandler`. Registers tools into the `ToolRegistry` and mounts the `GrugRouter`. Starts background workers for idle session compaction and nightly summarization. Also defines a pure-Python markdown task board (`add_task`, `list_tasks`, `edit_task`) backed by `brain/tasks.md`.
- `core/storage.py`: (The Truth Layer). Provides `GrugStorage`, which appends event dictionaries directly into human-readable daily logs at `brain/daily_notes/YYYY-MM-DD.md`. Thread-safe via `threading.Lock()`.
- `core/vectors.py`: (The Cache Layer). Provides `VectorMemory`, which uses `SentenceTransformers` (`all-MiniLM-L6-v2`, pinned revision) locally to ingest the Markdown text, compute embeddings, and UPSERT them into a highly optimized index via `sqlite_vss`. Thread-safe via `threading.Lock()`. VSS extension loading is gated behind the `VECTORS_LOAD_EXTENSION=1` env flag. Exposes `query_memory(query)`.
- `core/orchestrator.py`: (The Brain).
  - `load_prompt_files()`: Concatenates `system.md`, `rules.md`, `schema_examples.md` from `prompts/`.
  - `ToolRegistry`: Holds schemas and strictly manages execution of Python functions or CLI binaries (`subprocess.check_output(shell=False)`). Tools are registered with a `friendly_name` for display. CLI tools have `--`-prefixed value rejection, a `--` separator to prevent flag injection, and a configurable subprocess timeout (`GRUG_SUBPROCESS_TIMEOUT` env var, default 30s).
  - `GrugRouter`: Orchestrates prompting via Ollama `/api/chat` (multi-turn), intercepts JSON routing, checks Human-In-The-Loop approval (`requires_approval=True`), and returns a clarification response for low-confidence calls (confidence < 8). Hot-reloads prompt files on mtime change. Writes routing traces to `brain/routing_trace.jsonl`.
- `core/sessions.py`: `SessionStore` class providing SQLite CRUD for `sessions.db` — tracks active Slack thread conversation history and pending HITL actions.
- `core/summarizer.py`: Summarization engine — daily FIFO summarization (boot + nightly cron), idle session compaction, and prune auto-offload.
- `core/config.py`: Configuration loader — reads `grug_config.json` and exposes settings via dot notation with built-in defaults. Sections: `llm` (model, context tokens, temperature, `default_compression`), `memory` (summary limits, RAG settings, capped tail), `storage` (base_dir, session TTL).
- `grug_config.json`: Externalized memory and LLM tuning parameters.
- `prompts/`: System prompt files — `system.md`, `rules.md`, `schema_examples.md`. No `memory.md` (removed).
- `scripts/test_prompts.py`: Offline prompt regression test harness (requires live Ollama). Reads fixtures from `tests/prompt_fixtures.yaml`.
- `tests/prompt_fixtures.yaml`: YAML-driven test cases for prompt routing validation.

### Tool Categories
Tools are tagged with category prefixes in their descriptions:
- `[NOTES]` — `add_note`, `get_recent_notes`, `query_memory`
- `[BOARD]` — `add_task`, `list_tasks`, `edit_task`, `summarize_board`
- `[CHAT]` — `ask_for_clarification`, `reply_to_user`
- `[META]` — `list_capabilities`

### Core Rules for Building & Debugging
1. **SQLite is used for two purposes:** (1) The volatile VSS vector cache in `memory.db` — a searchable index over the Truth Layer that can be deleted and rebuilt by re-indexing daily notes. (2) Ephemeral session state in `sessions.db` — tracks active Slack thread conversation history and pending HITL actions. Both databases are ephemeral — all substantive data is always persisted to the Truth Layer (Markdown files in `brain/daily_notes/`) before session deletion. The Truth Layer remains the canonical, permanent source of all data.
2. **Never allow arbitrary bash execution**. If adding a new CLI ability, you must use `registry.register_cli_tool()` so the orchestrator maps keys directly to `--args` safely, preventing pipe injections. CLI args with `--`-prefixed values are rejected, and a `--` separator is appended to prevent flag injection.
3. **Persist the Caveman Mode**: If modifying prompt compilers, ensure `{{COMPRESSION_MODE}}` interpolation remains intact. The default compression mode is configured via `config.llm.default_compression` in `grug_config.json`.
4. **Environment**: It runs locally or inside a Docker container via `.env`. DO NOT assume host packages exist; package requirements are handled via `Dockerfile` (pinned base image digest) and `requirements.txt` (pinned versions). No Node.js dependency.
5. **No frontier escalation**: There is no Anthropic/Claude API dependency. Low-confidence responses (confidence < 8) trigger `ask_for_clarification` instead. If Ollama is unreachable, `invoke_chat` returns a safe `ask_for_clarification` JSON fallback via `json.dumps`.
