# Portable Agent-Agnostic Memory Architecture (Lightweight OpenClaw)

> **ARCHIVED 2026-04-11** — this is the original high-level architecture doc. Most of the design intent here is still accurate and lives on in the shipped code, with these noted drifts:
> - **"SQLite as Cache" now means sentence-transformers embeddings only.** Structured state (tasks) is delegated to the external `backlog` CLI tool, not stored in SQLite tables. Notes remain markdown-as-truth in `brain/daily_notes/`.
> - **HITL shipped** via Slack Block Kit approve/deny cards ([app.py](../../app.py)). See C1/H3 in [../followups.md](../followups.md) for known gaps.
> - **Docker non-root shipped** (`user: "1000:1000"` in `docker-compose.yml`).
> - **`escalate_to_frontier` is real**, not a stub — calls `anthropic.Anthropic.messages.create` with caching. See H9 in followups for the offline-first import gap.
> - **`sqlite-vss` is disabled on macOS** (`HAS_VSS=False`); the Linux/Docker path loads it normally.
> - Sections 1, 2, and 5 below are duplicated (typo in the original — first "Storage Layer" section is a partial/earlier draft that was left in). Read the second instance.
>
> Open work is tracked in [../followups.md](../followups.md). Treat this file as historical design intent, not current state.

This document outlines an architecture for a portable, agent-agnostic "second brain" and assistant. It is conceptually very similar to OpenClaw—a local orchestrator that parses intent into tool execution—but radically simplified and optimized for low-latency edge models (Gemma) falling back to frontier models (Claude Opus).

## User Review Required

> [!IMPORTANT]
> Please review the updated decisions based on your feedback:
> 1. **Language Choice**: Review the Python vs TypeScript pros/cons below and confirm which one you'd like to build the local orchestration script in.

## Proposed Architecture

### 1. The Storage Layer: "Markdown as Truth, DB as Cache"

To solve your concern about Git merge conflicts without losing fast semantic search, we use a hybrid approach:
### 1. The Storage Layer: "Markdown as Truth, DB as Cache"

To solve your concern about Git merge conflicts without losing fast semantic search or causing file bloat, we use a hybrid approach that mirrors popular personal knowledge bases (like Obsidian):
- **Flat Files (Markdown Append Strategy)**: Instead of creating a new `.md` file for every single thought (which causes folder bloat), the local script will append your captures to aggregated files. For example, all captures for a single day go into a `daily_notes/2026-04-10.md` file, or into categorical files like `people.md` and `decisions.md`. This keeps your file count low and human-readable.
- **SQLite + sqlite-vss (The Cache)**: A background thread reads these Markdown files, splits them into "blocks" (paragraphs or bullet points), and generates vector embeddings. **SQLite acts purely as a structured index and semantic search engine**, not the source of truth. If you hit a Git conflict, you simply resolve it in the text file, and SQLite re-indexes the block.

### 2. Tiered Memory System
- **Working (Core) Memory**: The baseline context. Loaded directly from `memory.md` into the system prompt. It describes the environment and the user instantly.
- **Episodic (Recall) Memory**: The rolling N-messages of the current Slack conversation.
- **Archival (Semantic) Memory**: The SQLite cache of your markdown files. Only accessed when Gemma or Claude explicitly uses the `query_memory` tool to find vectors matching a prompt.

### 3. CLI-Native Tool Layer & Extensibility

To keep the system extensible without writing massive Python API wrappers for every service, the tool layer is designed to execute **Command Line Interfaces (CLIs)** natively.
- **CLI Wrappers**: You define a JSON schema (e.g., `gws_calendar_event_create`). Our Python orchestrator safely maps this to a subprocess call (e.g., executing `gws calendar create --summary="Meeting"`). This allows you to instantly integrate any company's CLI (AWS, Google Workspace, GitHub) as a lightweight tool.

**The Routing & No-Frontier Graceful Degradation Strategy**:
1. **Edge Triage (Gemma)**: Intercepts the Slack message. Generates a JSON tool call based on constraints.
2. **Escalation**: If Gemma finds the prompt too complex, it attempts to use `escalate_to_frontier`.
3. **Graceful Failover / Offline Mode**: The local orchestrator intercepts the `escalate_to_frontier` call.
   - It checks for an active/valid API key for Claude.
   - If available: The payload is routed to Claude, and Claude executes the complex response.
   - If **NOT** available (or offline): The script blocks the escalation and injects a system message back into Gemma's context: `"SYSTEM: The frontier model is offline. Provide a best-effort response locally."` Gemma then provides a synthesized answer with a warning that deeper analysis requires the frontier model.

### 4. Security & Privacy Layer (Fixing OpenClaw's Flaws)

OpenClaw's biggest weakness is unconstrained execution. We secure the environment at three levels:
- **Strict Allow-lists**: AI is absolutely banned from arbitrary `bash` execution. If a CLI tool is used, the LLM provides JSON parameters matching a strict Pydantic/JSON schema, which safe-guards and escapes the arguments before running exactly *one* whitelisted binary.
- **Human-in-the-Loop (HITL) for Destructive Actions**: Any state-changing CLI or external call (e.g., deleting a calendar event, pushing a git commit) will explicitly suspend execution and send an interactive Slack message: `"Gemma wants to run [XYZ]. Approve/Deny?"`
- **Docker Isolation via Least Privilege**: The Python orchestrator runs entirely inside a Docker container using a non-root user. The only directories the container can write to are explicitly mounted volumes (`/app/brain`).

### 4. Language Selection: Python 

We will build the orchestration script in **Python**. 
- It has the best ecosystem and bindings for local AI routing (OpenClaw, llama.cpp, LangChain).
- It binds easily to `sqlite-vss` and embedding models via `sentence-transformers`.
- We will manage dependencies with a simple `requirements.txt` or `pyproject.toml` to ensure portability without excessive complexity.

### 5. Output Compression & Token Efficiency (Caveman Mode)

To maximize token efficiency for edge models and lower API costs for Claude, the assistant's persona (defined in `system.md` / `rules.md`) will include an explicit **Compression Gauge**. 
Drawing from the `caveman` concept (where terse outputs maintain technical accuracy but drastically reduce token overhead):
- **Lite**: Concise, no pleasantries, direct answers only.
- **Full**: Fragmented sentences. "New object ref each render. Wrap in useMemo."
- **Ultra (Caveman Mode)**: Maximum compression. Strict arrow associations, omission of all non-essential verbs. "Inline obj prop -> new ref -> useMemo."

The orchestration layer can dynamically inject the desired compression level into the system prompt, ensuring the LLM does not waste precious tokens on conversational fluff unless explicitly requested.

### 6. Containerized Deployment (Docker)

To make it truly portable between machines, the entire stack will be Containerized.
- A `Dockerfile` configures the Python environment, downloads necessary embedding models locally, and installs your chosen CLIs (like Google Workspace CLI).
- A `docker-compose.yml` mounts your persistent host directories (the local `/brain` folder containing your `.md` and SQLite database) directly to identical volumes inside the container. You can move that folder to any machine, run `docker-compose up`, and your brain powers back on exactly as you left it.

## Open Questions

> [!TIP]
> Does the strict schema-mapping and HITL approach ease your security concerns?
> Are there any specific CLIs besides Google Workspace you want pre-installed in the Docker container?

## Verification Plan

### Automated / Unit Tests
- Create mock inputs for Gemma. Verify that it consistently generates valid JSON corresponding to `add_task`, `escalate_to_frontier`, or `save_insight`.
- Integration tests ensuring that changes to the `.md` flat files trigger an update to the `sqlite-vss` cache layer.

### Manual Verification
- Test Offline Mode: Unset the Claude API key, send a complex synthesis request, and verify Gemma intercepts the "offline" state and gracefully degrades.
- End-to-End Slack Webhook: Post a message in Slack matching an "Insight capture", verify a new markdown file appears in the workspace, and the SQLite cache reflects it.
