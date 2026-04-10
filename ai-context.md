# AI Context: Grug Architecture

**ATTENTION FELLOW AI AGENT**: If you are reading this file, the user has tasked you with debugging or extending the Grug repository. Read this context before traversing the codebase.

## System Overview
Grug is a Python-based intelligent router connecting a Slack bot interface to a local LLM (specifically edge models like Gemma), a local Vector Database (`sqlite-vss`), and strict CLI executables via subprocesses.

### Core File Structure
- `app.py`: The main entrypoint. Listens via Slack Bolt using `SocketModeHandler`. Registers tools into the `ToolRegistry` and mounts the `GrugRouter`.
- `core/storage.py`: (The Truth Layer). Provides `GrugStorage`, which appends event dictionaries directly into human-readable daily logs at `brain/daily_notes/YYYY-MM-DD.md`.
- `core/vectors.py`: (The Cache Layer). Provides `VectorMemory`, which uses `SentenceTransformers` (`all-MiniLM-L6-v2`) locally to ingest the Markdown text, compute embeddings, and UPSERT them into a highly optimized index via `sqlite_vss`. Exposes `query_memory(query)`.
- `core/orchestrator.py`: (The Brain). 
  - `ToolRegistry`: Holds schemas and strictly manages execution of Python functions or CLI binaries (`subprocess.check_output(shell=False)`).
  - `GrugRouter`: Orchestrates prompting, intercepts JSON routing, checks Human-In-The-Loop approval (`requires_approval=True`), and handles Graceful Degradation if `escalate_to_frontier` is called without API keys.

### Core Rules for Building & Debugging
1. **Never write raw SQL for state tracking**. Everything MUST be appended to the Markdown files in `core/storage.py`. The SQL vector-store is strictly a volatile cache.
2. **Never allow arbitrary bash execution**. If adding a new CLI ability, you must use `registry.register_cli_tool()` so the orchestrator maps keys directly to `--args` safely, preventing pipe injections.
3. **Persist the Caveman Mode**: If modifying prompt compilers, ensure `{{COMPRESSION_MODE}}` interpolation remains intact. The system relies heavily on token optimization.
4. **Environment**: It runs locally or inside a Docker container via `.env`. DO NOT assume host packages exist; package requirements are handled via `Dockerfile` and `requirements.txt`.
