# Task List: Lightweight OpenClaw Memory Architecture

- `[x]` **Phase 1: Project, Docker & Storage Setup**
  - `[x]` Create `Dockerfile` and `docker-compose.yml` defining the secure execution environment.
  - `[x]` Setup Python environment (`requirements.txt` with `sqlite-vss`, `sentence-transformers`, `slack-sdk`, etc.) inside the container.
  - `[x]` Mount persistent volume for the flat-file knowledge base (e.g., `/app/brain/`).
  - `[x]` Implement local script that appends capture payloads (Insight, Task, Person) directly entirely to Markdown files following a daily-append strategy.

- `[ ]` **Phase 2: The DB Cache & Vector Search**
  - `[ ]` Initialize `memory.db` SQLite database with `sqlite-vss` for vector search capabilities.
  - `[ ]` Implement background indexing thread: reads markdown files, extracts "blocks", generates embeddings via `sentence-transformers`, and writes to `memory.db`.
  - `[ ]` Implement `query_memory` Python function to query `memory.db` for semantic matches.

- `[ ]` **Phase 3: Secure Orchestrator & CLI Tool Abstraction**
  - `[ ]` Define strict JSON Schemas mapping to local functions and CLI tools (e.g., Google Workspace CLI).
  - `[ ]` Build the secure subprocess executor that sanitizes JSON values before passing them to whitelisted CLI binaries.
  - `[ ]` Implement Human-in-the-Loop (HITL) suspension for destructive/state-mutating CLI calls.
  - `[ ]` Build the local Python router that passes the user query to the local LLM (Gemma) combined with the base context.

- `[ ]` **Phase 4: Graceful Degradation & Escalation**
  - `[ ]` Implement the `escalate_to_frontier` tool.
  - `[ ]` Embed logic to check for the Claude API key.
  - `[ ]` If key is missing/invalid, trigger the Graceful Failover path: inject offline warning into Gemma's prompt and re-run.

- `[ ]` **Phase 5: Prompts & Caveman Compression Mode**
  - `[ ]` Update `prompts/system.md` and `prompts/rules.md` to establish the Compression Gauge (Lite, Full, Ultra/Caveman).
  - `[ ]` Update the python router to dynamically compile the system prompt with the requested compression tier.

- `[ ]` **Phase 6: Integration & Final Verification**
  - `[ ]` Wire the orchestrator logic into the existing Slack bot endpoint structure.
  - `[ ]` Execute end-to-end Slack test for "Decision" capture (verifying MD file is created and SQLite syncs).
  - `[ ]` Execute end-to-end Slack test for "complex synthesis" (verifying `escalate_to_frontier` triggers and falls back properly if no key).
