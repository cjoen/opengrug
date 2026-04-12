# 🧠 Grug Chat & Memory Refactor: Technical Specification

## 🏛️ 1. Core Architectural Vision: The "Memory Pyramid"

The objective is to move from a **stateless router** to a **stateful agent**. We achieve this by layering memory based on its temporal relevance.

### 1.1 Storage Strategy
*   **Truth Layer (Long-Term):** Raw Markdown files in `brain/daily_notes/`. Human-readable, append-only, permanent.
*   **Context Layer (Medium-Term):** High-density summaries in `brain/summaries/`. Professional tone, 7-workday rolling window.
*   **Session Layer (Short-Term):** SQLite table `sessions` in `memory.db`. Stores active Slack threads and pending actions.
*   **Search Layer (Knowledge):** SQLite-VSS vector index. Provides semantic retrieval over the Truth Layer.

---

## ⚙️ 2. Configuration & Extensibility (`grug_config.json`)

To support different LLMs (Gemma, Llama, Claude) with varying context windows, all memory parameters must be externalized.

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

---

## 🏗️ 3. Database & File System Schema

### 3.1 SQLite `sessions` Table
| Column | Type | Description |
| :--- | :--- | :--- |
| `thread_ts` | TEXT (PK) | Slack thread timestamp; the unique ID for the conversation. |
| `channel_id`| TEXT | Slack channel ID for routing replies. |
| `messages`  | JSON | List of `{"role": "user|assistant", "content": "..."}` objects. |
| `pending_hitl`| JSON | Stores tool name and arguments if waiting for approval. `null` if idle. |
| `last_active` | TIMESTAMP | Used for pruning extremely old sessions. |

---

## 🛠️ 4. Design Decisions & Logic

### 4.1 The "Ollama Chat" Protocol
*   **Decision:** Migrate from `/api/generate` to `/api/chat`.
*   **Implementation:** The `system` message is generated dynamically for *every turn*, containing the persona instructions AND the summaries from the last `summary_days_limit` active days.

### 4.2 The High-Density FIFO (Smart Purge)
*   **Logic:** Summarization only occurs if the daily log contains enough content.
*   **Process:**
    1. Read `daily_notes/YYYY-MM-DD.md`.
    2. If size < `summarization_threshold_bytes`, skip.
    3. Else, prompt LLM: *"Summarize these logs into high-density professional bullets. No caveman voice."*
    4. Save to `summaries/YYYY-MM-DD.summary.md`.
    5. Maintain exactly `summary_days_limit` files in the pool.

---

## 🚀 5. Technical Implementation Notes for Claude

### 5.1 Context Injection Pipeline
When a message arrives:
1.  **Identity:** Determine `thread_ts`.
2.  **Recall:** Fetch the last `thread_history_limit` messages from `sessions`.
3.  **Environment:** Read the active files from `summaries/` and join them.
4.  **Assemble:**
    *   `system`: "You are Grug... [Persona] ... [Summaries]"
    *   `messages`: [History] + [New User Message]
5.  **Safety Check:** If total tokens > `target_context_tokens`, prune the middle of the `messages` list.

### 5.2 Persistence & HITL
The current `PENDING` dictionary in `app.py` is volatile. Claude must refactor this to use the `pending_hitl` column in the SQLite `sessions` table so approvals survive container restarts.

### 5.3 Error Handling
If the LLM fails to generate a summary or the Chat API is unreachable, Grug should fall back to the existing "Last 10 raw notes" method to ensure the bot is never "brain dead."

---

## 🎯 6. Phased Roadmap

1.  **Phase 1:** Core SQLite session store, `grug_config.json`, and Ollama `/api/chat` integration.
2.  **Phase 2:** Nightly/Boot summarization script and FIFO manager.
3.  **Phase 3:** Persistent HITL and automated RAG pre-flight.
