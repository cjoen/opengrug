# 💡 open-grug Project Ideas

This file tracks lightweight, high-leverage ideas for Grug. These are not yet active tasks. Approved ideas move to a "todo" document for technical fleshing out before implementation.

---

## 🛠️ 1. Reliability & Self-Diagnosis

### 1.1 `grug_health` Tool
*   **Problem:** When dependencies (Ollama, Backlog CLI, SQLite) fail, Grug's errors are opaque or confusing.
*   **Idea:** A tool Grug can call to check its own "organs." It pings Ollama, checks for the `backlog` binary, and verifies if `memory.db` is writable.
*   **Benefit:** Grug can report "Grug brain hurt, Ollama no talk" to the user, making debugging much faster.

---

## 🧠 2. Routing & Intelligence

### 2.1 The Grug Memory (`grug.md`)
*   **Problem:** Grug repeats the same mistakes (e.g., using wrong status names) because the system prompt is static.
*   **Idea:** A "self-improvement" file similar to `claude.md` or `gemini.md`. Grug should have a tool to `add_instruction` to this file when it realizes it made a mistake or learns a user preference.
*   **Benefit:** Persistent, autonomous learning that allows Grug to "evolve" its own behavior over time.

### 2.2 Explicit Tool Call Convention ✅
*   **Status:** *Implemented — see `build-plan/reliability_improvements.md`*
*   **Problem:** LLM-based routing can be slow and sometimes misses obvious direct commands.
*   **Idea:** Define a convention like `[tool_name] - [data]` that Grug looks for first via simple regex/parsing. If the pattern matches, it skips the expensive "reasoning" step and goes straight to validation/HITL.
*   **Benefit:** Drastically reduces latency for power users and provides a clear "contract" for deterministic behavior.

### 2.3 Tool Hierarchy & Clarification ✅
*   **Status:** *Implemented — see `build-plan/reliability_improvements.md`*
*   **Problem:** As the number of tools grows, the "flat" list becomes confusing for the LLM, leading to "near-miss" tool calls.
*   **Idea:** Categorize tools (e.g., `[BOARD]`, `[NOTES]`, `[SYSTEM]`). If Grug's confidence in a specific tool is low but he's sure of the *category*, he should use `ask_for_clarification` to present the user with a few options from that category.
*   **Benefit:** Prevents accidental tool calls and helps the user discover the right tool for their intent.

---

## 🎭 3. UX & Persona

### 3.1 Configurable Tone & Persona
*   **Problem:** "Caveman Grug" is fun but can be inefficient for technical debugging.
*   **Idea:** Move tone settings into the `grug_config.json`. Allow Grug to adjust its own tone (e.g., switching to "Engineer" mode for complex tasks) or let the user toggle it via a Slack command.
*   **Benefit:** Flexibility to balance personality with professional utility.

### 3.2 Activity Summary (Future-Focused)
*   **Problem:** Hard to track what Grug is doing in the background as more automated/background tasks are added.
*   **Idea:** A tool to summarize the last N tool calls. "Grug created 3 tasks and added 1 note about the API."
*   **Status:** *Lower priority until more automated/background tasks are implemented.*

---

## 🏗️ 4. Advanced Orchestration

### 4.1 Grug's Idle Task Queue
*   **Problem:** Some tasks (long-running searches, batch updates, or "remind me later" actions) shouldn't block the main Slack interaction or are non-urgent.
*   **Idea:** A persistent "deferred task" list in SQLite. Once a conversation is over and the LLM is idle, a heartbeat/reminder system triggers Grug to process these non-urgent requests.
*   **Benefit:** Keeps Grug snappy and responsive while allowing him to perform "background work" or follow-ups without being explicitly prodded every time.

### 4.2 Gemma 4 Control Token Optimization
*   **Problem:** Small edge models like e4b and e2b are highly sensitive to prompt structure and often struggle with complex tool-routing logic or JSON escaping.
*   **Idea:** Implement native support for Gemma 4's special control tokens (`<|think|>`, `<|channel>thought`, `<|"|>`, and the native `system` role). 
*   **Benefit:** 
    *   **Reasoning:** Enables a "Thinking Mode" where Grug can perform internal chain-of-thought before emitting JSON, increasing tool-call accuracy.
    *   **Robustness:** Uses the `<|"|>` string delimiter to prevent user-provided quotes or braces from breaking the JSON structure.
    *   **Efficiency:** Native role tokens reduce the overhead of text-based headers (e.g., `SYSTEM:`), saving precious context for actual "Cave Memory."
*   **Status:** *High leverage for e4b/e2b performance.*

---

## 💾 5. Memory & Storage

### 5.1 Auto-generated Note Titles
*   **Problem:** `add_note` currently only stores raw content and tags. Without a title, the long-term Markdown logs are difficult to skim for specific topics.
*   **Idea:** A sub-function or enhancement to the note-taking process that automatically generates a concise, descriptive title. This title would be stored as a header in the Markdown logs.
*   **Benefit:** Makes the "Truth Layer" much more readable for humans and provides a high-density signal for future RAG/search queries.

### 5.2 High-Density Note Retrieval
*   **Problem:** `get_recent_notes` currently returns a raw list with timestamps, which is visually noisy and hard for the LLM (or user) to digest quickly.
*   **Idea:** Format retrieved notes into a structured "bulletin" or "thematic group" rather than just a chronological dump.
*   **Benefit:** Reduces token usage in the prompt and makes the context much clearer for Grug's reasoning.
