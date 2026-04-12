# 🧠 Grug Chat & Memory Refactor: Technical Specification

## 🏛️ 1. Core Architectural Vision: The "Memory Pyramid"

The objective is to move from a **stateless router** to a **stateful agent**. We achieve this by layering memory based on its temporal relevance.

### 1.1 Storage Strategy
*   **Truth Layer (Long-Term):** Raw Markdown files in `brain/daily_notes/`. Human-readable, append-only, permanent. This is the canonical source of all data.
*   **Context Layer (Medium-Term):** High-density summaries in `brain/summaries/`. Professional tone, 7-workday rolling window.
*   **Session Layer (Short-Term):** SQLite table `sessions` in a **dedicated `sessions.db`** (separate from `memory.db`). Stores active Slack thread conversation history and pending HITL actions. This database is ephemeral — it can be deleted without data loss because all substantive data is always persisted to the Truth Layer first.
*   **Search Layer (Knowledge):** SQLite-VSS vector index in `memory.db`. Provides semantic retrieval over the Truth Layer.

**Database Separation Rationale:** `sessions.db` and `memory.db` are kept as separate SQLite databases. `memory.db` is the RAG vector store — its contents are a volatile cache of the Truth Layer. `sessions.db` tracks active conversation state for the `/api/chat` protocol. Separating them prevents any overlap between session history rows and vector search results, and allows either database to be rebuilt independently.

---

## ⚙️ 2. Configuration & Extensibility (`grug_config.json`)

To support different LLMs (Gemma, Llama, Claude) with varying context windows, all memory parameters must be externalized.

**Note:** This config file governs memory and LLM tuning parameters only. Existing environment variables (`OLLAMA_HOST`, `OLLAMA_MODEL`, `SLACK_BOT_TOKEN`, `DOCKER`, etc.) remain unchanged. Migrating env vars into `grug_config.json` is a separate future task.

```json
{
  "llm": {
    "model_name": "gemma:2b",
    "max_context_tokens": 8192,
    "target_context_tokens": 2048,
    "temperature": 0.1
  },
  "memory": {
    "summary_days_limit": 7,
    "summary_token_budget": 300,
    "summarization_threshold_bytes": 100,
    "thread_history_limit": 10,
    "thread_idle_timeout_hours": 4,
    "idle_sweep_interval_minutes": 15,
    "capped_tail_lines": 100,
    "rag_result_limit": 3
  },
  "storage": {
    "base_dir": "./brain",
    "session_ttl_days": 30
  }
}
```

*   **`max_context_tokens`**: Hard limit for the model.
*   **`target_context_tokens`**: The "sweet spot" where Grug will start aggressively truncating history to maintain reasoning quality.
*   **`summary_days_limit`**: How many days of professional summaries to inject into the `system` message.
*   **`summarization_threshold_bytes`**: Minimum size (in bytes) of a daily note file before the summarizer runs on it. Set intentionally low (100 bytes ≈ 2 short bullet points) so that summarization is aggressive and rarely skips a day. This is a deliberate design choice — raise to ~500 if only substantial days should be summarized.
*   **`idle_sweep_interval_minutes`**: How often the background worker checks for idle sessions to compact.
*   **`capped_tail_lines`**: Maximum number of lines to read from the tail of today's raw daily notes when building the system prompt. Prevents token blowup on busy days.

---

## 🏗️ 3. Database & File System Schema

### 3.1 SQLite `sessions` Table (in `sessions.db`)

The `sessions` table lives in a **dedicated `sessions.db`** file, separate from the vector search `memory.db`. This prevents overlap between session history and RAG vector results.

| Column | Type | Description |
| :--- | :--- | :--- |
| `thread_ts` | TEXT (PK) | Slack thread timestamp; the unique ID for the conversation. |
| `channel_id`| TEXT | Slack channel ID for routing replies. |
| `messages`  | JSON | List of `{"role": "user|assistant", "content": "..."}` objects. |
| `pending_hitl`| JSON | Stores tool name and arguments if waiting for approval. `null` if idle. |
| `last_active` | TIMESTAMP | Used for pruning extremely old sessions via background sweep. |

**DDL:**
```sql
CREATE TABLE IF NOT EXISTS sessions (
    thread_ts   TEXT PRIMARY KEY,
    channel_id  TEXT NOT NULL,
    messages    TEXT NOT NULL DEFAULT '[]',      -- JSON array of message objects
    pending_hitl TEXT DEFAULT NULL,              -- JSON object or null
    last_active TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_sessions_last_active ON sessions(last_active);
```

---

## 🛠️ 4. Design Decisions & Logic

### 4.1 The "Ollama Chat" Protocol
*   **Decision:** Migrate from `/api/generate` to `/api/chat`.
*   **Routing Strategy:** Keep the existing JSON-mode routing pattern. The `/api/chat` endpoint is used **only** to gain multi-turn conversation history — not for native tool calling. The `system` message still instructs the model to output a JSON tool-call object, and the code still parses the response as JSON. This keeps the migration safe and avoids dependency on model-specific tool-calling support (which varies across edge models like Gemma).
*   **Implementation:** The `system` message is generated dynamically for *every turn*, containing the persona instructions AND the summaries from the last `summary_days_limit` active days.

### 4.2 The High-Density FIFO (Smart Purge)
*   **Logic:** Summarization only occurs if the daily log contains enough content. **Important Note for Claude:** This process operates strictly on a *per-day* basis. It does NOT read old summaries or combine days. A singular day's raw log is compressed into that day's singular summary file to avoid compounded redundancy.
*   **Trigger:** Summarization runs on **boot** and on a **nightly cron schedule**. It does NOT run lazily during message handling — this prevents blocking the user's first message of the day while summarization runs.
*   **Process:**
    1. Read `daily_notes/YYYY-MM-DD.md`.
    2. If size < `summarization_threshold_bytes`, skip.
    3. Else, prompt LLM: *"Summarize these logs into high-density professional bullets. No caveman voice."*
    4. Save to `summaries/YYYY-MM-DD.summary.md`.
    5. Maintain exactly `summary_days_limit` files in the pool.

### 4.3 Truth Layer Ingestion (Auto-Offloading & Idle Compaction)
Relying on smaller edge models to manually and correctly call an `add_note` tool is too unreliable. Instead, data graduates to the Truth Layer via two automated mechanisms:

1.  **Auto-Offloading during Pruning:** When an active Slack thread hits the `target_context_tokens` limit, the oldest messages are removed from the active context window. **Before deleting them**, the system runs a fast background LLM call to summarize the pruned chunk ("Summarize the key facts from this part of the conversation"). The resulting summary is automatically appended to today's raw `daily_notes/YYYY-MM-DD.md` log via `storage.append_log()`. All appended summaries MUST use the standard `- TIMESTAMP [source] content` bullet format so the existing vector indexer continues to work.
2.  **Idle Session Compaction:** Pushing an LLM to its maximum token limit makes it slower, more expensive, and degrades reasoning quality. To prevent a Slack thread from bogging down the model over a long working day, the system leverages the `last_active` timestamp in the `sessions` table. 
    *   **Trigger Mechanism:** A dedicated background worker loop or scheduler (like `APScheduler`) runs independently every `idle_sweep_interval_minutes`. It sweeps the `sessions` table looking for threads that have been inactive for `thread_idle_timeout_hours` (e.g., 4 hours).
    *   **Process:** The *entire* remaining conversation of that idle thread is summarized and appended to the Truth Layer (daily notes) using the standard bullet format. After summarization, the SQLite session row is **deleted**. Because the data has already been persisted to the Truth Layer markdown files, no information is lost. When the user returns to that thread, Grug creates a fresh session row with an empty context window, retrieving the older context via the summary/vector layer without carrying the heavy token load.
    *   **Race Condition Mitigation (Optimistic Check):** Before deleting the session row after summarization completes, re-check the `last_active` timestamp. If it has changed since the sweep began (meaning the user sent a new message during compaction), **abort the deletion** — the session is no longer idle. The summary that was already appended to the Truth Layer is still valid data and causes no harm.
    *   **Missing Session Handling:** When a message arrives for a `thread_ts` that does not exist in the `sessions` table (either because it was compacted or is brand new), create a fresh session row with an empty `messages` array. The vector/summary layer provides historical continuity.

---

## 🚀 5. Technical Implementation Notes for Claude

### 5.1 Context Injection Pipeline
When a message arrives:
1.  **Identity:** Determine `thread_ts` using `event.get('thread_ts', event['ts'])`. This means top-level Slack messages start their own session, and thread replies join the parent session.
2.  **Recall:** Look up the session by `thread_ts` in `sessions.db`. If no row exists, create a fresh one. Fetch the last `thread_history_limit` messages from the `messages` JSON array.
3.  **Environment:** Read up to `summary_days_limit` summary files from `summaries/`, sorted by date descending. Additionally, read a **Capped Tail** of today's raw notes from `daily_notes/YYYY-MM-DD.md` (limited to the last `capped_tail_lines` lines). This guarantees the token limit won't blow up if today's log gets massive from multiple offloads, while still providing recent short-term context.
4.  **Assemble:**
    *   `system`: "You are Grug... [Persona] ... [Summaries] ... [Today's Raw Notes (Capped Tail)]"
    *   `messages`: [History] + [New User Message]
5.  **Safety Check & Auto-Offload:** If total tokens (estimated via simple heuristic: `total_characters / 4`) > `target_context_tokens`, prune the *oldest* messages from the `messages` list (taking care to preserve the system prompt directly). This heuristic is intentionally simple — expand to a proper tokenizer later if needed.
    *   **Crucial Rule - Turn-Based Pruning:** Do NOT simply pop the oldest individual messages, as this risks slicing between a `tool_call` and its corresponding `tool_result`, which will cause API validation errors. Pruning must be done by atomic **"Turns"**. A Turn boundary is defined by the *next user message*. Everything between two user messages is one Turn: (User Message → Assistant Tool Call(s) → Tool Result(s) → Assistant Final Reply). If the assistant calls multiple tools in a single turn, all tool calls, all tool results, and the final assistant reply are part of that same atomic Turn and must be pruned together. Never prune a partial Turn.
    *   **Crucial Rule - Auto-Offload:** Before discarding the pruned Turn(s), pass them to a background summarization LLM call and thread-safely append the result to today's Truth Layer log using the standard `- TIMESTAMP [source] content` bullet format.

### 5.2 Persistence & HITL Re-entry
The current `PENDING` dictionary in `app.py` is volatile. Claude must refactor this to use the `pending_hitl` column in the SQLite `sessions` table (in `sessions.db`) so approvals survive container restarts.
Once a human approves an action via Slack, the system must:
1. Execute the approved tool.
2. Append the tool's result to the JSON `messages` array in the `sessions` table.
3. Re-trigger the inference call (pass the updated history back to the LLM) so Grug can read the tool output and formulate a final reply to the thread.

### 5.3 Background Processing
The LLM inference call can take several seconds. To avoid blocking the Slack event loop (which would make Grug unresponsive to other messages while "thinking"), push the LLM call to a background task (e.g., a worker thread or `asyncio` task). The Slack Bolt `SocketModeHandler` handles connection-level acking automatically, but long-running handlers still block the event processing queue.

### 5.4 Error Handling & File Lock Concurrency
1.  **Truth Layer File Safety:** Because auto-offloaded chunks and idle compactions from multiple threads can run simultaneously in background tasks, `storage.append_log()` MUST be thread-safe. Use a locking mechanism (like Python's `threading.Lock()` or the `filelock` library) when appending to `daily_notes/YYYY-MM-DD.md` to prevent data corruption.
2.  **Truth Layer Format Consistency:** All automated appends to daily notes (auto-offloads, idle compactions) MUST use the same `- TIMESTAMP [source] content` bullet format as manual `add_note` calls. This ensures the existing vector indexer (which parses `- ` prefixed lines) continues to work without modification.
3.  **Graceful Degradation:** If the LLM fails to generate a summary or the Chat API is unreachable, Grug should fall back to the existing "Last 10 raw notes" method to ensure the bot is never "brain dead."

### 5.5 RAG Tool Prompting (For Smaller Edge Models)
Because Grug aims to run fast edge models (like Gemma), getting the bot to proactively use the `query_memory` tool for context older than the Capped Tail can be challenging. To give the model the extra "kick" it needs:
*   **Explicit System Nudge:** Add a highly directive, all-caps rule in the `system` prompt: *"CRITICAL: If the user refers to an event, task, or conversation from earlier today or a past day that is NOT visible in your logs above, you MUST use the `query_memory` tool to search your memory database before replying. Do not guess."*
*   **Tool Description:** Ensure the `query_memory` tool description is written plainly (e.g., "Use this tool to remember past conversations or search for older notes").

### 5.6 Update `ai-context.md`
The current `ai-context.md` states: *"Never write raw SQL for state tracking. The SQL vector-store is strictly a volatile cache."* This rule must be updated to reflect the new architecture. Specifically:
*   SQLite is now used for **two purposes**: the volatile VSS vector cache (`memory.db`) AND ephemeral session state (`sessions.db`).
*   The Truth Layer (Markdown files) remains the canonical, permanent source of all data. Both SQLite databases can be deleted and rebuilt — `memory.db` from re-indexing the daily notes, and `sessions.db` because all conversation data is persisted to the Truth Layer before session deletion.
*   Update the Core File Structure section to reflect the new files (`core/sessions.py`, `core/summarizer.py`, `core/config.py`).

---

## 📁 6. File Layout

The refactor introduces the following new files and modifies existing ones:

```
core/sessions.py    — SessionStore class (SQLite CRUD for sessions.db)
core/summarizer.py  — Summarization logic (daily FIFO, idle compaction, prune offload)
core/config.py      — Config loader (reads grug_config.json)
core/orchestrator.py — Rewritten GrugRouter (migrated to /api/chat, multi-turn history)
core/storage.py     — Updated with thread-safe append_log()
app.py              — Slack event handlers + background scheduler startup
ai-context.md       — Updated to reflect new architecture (see §5.6)
grug_config.json    — New config file (see §2)
```

---

## 🎯 7. Phased Roadmap

1.  **Phase 1:** Core SQLite session store (`sessions.db`), `grug_config.json`, Ollama `/api/chat` integration (JSON-mode routing preserved), and update `ai-context.md`.
2.  **Phase 2:** Boot + nightly cron summarization script and FIFO manager. Idle session compaction with background sweep.
3.  **Phase 3:** Persistent HITL and automated RAG pre-flight (Note: Currently RAG is invoked manually by the LLM via the `query_memory` tool. Phase 3 will auto-inject vector search results into the context before the LLM decides on tools).
