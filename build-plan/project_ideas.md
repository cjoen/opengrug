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
*   **Idea:** Implement native support for Gemma 4's special control tokens across three areas:

    **A. Thinking Mode (chain-of-thought before tool calls)**
    - Add `<|think|>` to the system prompt to activate Grug's internal reasoning channel.
    - The model will emit `<|channel>thought<channel|>` blocks before generating a tool call — internal reasoning that isn't shown to the user but improves decision quality.
    - On multi-turn conversations, strip prior `<|channel>...<channel|>` blocks before reinserting history (prevents cyclical reasoning). **Exception:** preserve thoughts between tool calls within a single turn.
    - For long-running agentic sessions, summarize previous thoughts as plain text and reinject rather than passing raw channel output — prevents compounding loops.
    - Optionally, tune system instructions to modulate thinking depth (can reduce thinking token overhead ~20%).

    **B. Native tool call tokens**
    - Replace the current text-based tool schema injection with native tokens:
        - `<|tool>` / `<tool|>` — wraps tool definitions
        - `<|tool_call>` / `<tool_call|>` — wraps the model's tool invocation request
        - `<|tool_response>` / `<tool_response|>` — wraps the result returned to the model after execution
    - This aligns Grug's tool protocol with what Gemma 4 was trained on, reducing hallucinated or malformed calls.

    **C. String delimiter for JSON safety**
    - Wrap all string values inside tool call and tool response blocks with `<|"|>` instead of standard `"` quotes.
    - Prevents user-provided text containing quotes, braces, or backticks from corrupting the structured data block — the main cause of JSON parse failures with e4b/e2b.

*   **Benefit:** 
    *   **Accuracy:** Thinking mode lets Grug reason about ambiguous requests before committing to a tool call.
    *   **Robustness:** Native tokens + string delimiters eliminate the two most common failure modes (wrong tool, broken JSON).
    *   **Efficiency:** Fewer wasted tokens on text-based scaffolding means more context budget for Cave Memory and conversation history.
*   **Status:** *High leverage for e4b/e2b performance. Requires verifying Ollama exposes raw token control or that the model API accepts raw token strings in the prompt body.*

### 4.3 Gemma 4 Multimodal Input (Image & Audio)
*   **Problem:** Grug is text-only. Users can't share a screenshot of a bug, a photo of a whiteboard, or a voice note.
*   **Idea:** Use Gemma 4's native multimodal control tokens to support image and audio in user messages:
    - `<|image>` / `<image|>` — wrap image embedding blocks
    - `<|audio>` / `<audio|>` — wrap audio embedding blocks
    - `<|image|>` and `<|audio|>` — inline placeholder tokens inside user turn content, replaced by actual soft embeddings at inference time
    - Multiple instances of either type can appear in a single turn (e.g., "compare these two screenshots")
    - Prompt structure stays the same — placeholders just appear inside the user turn where the media is referenced

    Example shape:
    ```
    <|turn>user
    Here is the error screenshot: <|image|>
    What is broken?<turn|>
    <|turn>model
    ```

*   **Benefit:** 
    *   Users can drop a screenshot into Slack and Grug can reason about it directly.
    *   Voice notes could feed the audio token path, enabling async speech input.
    *   No separate vision/transcription pipeline needed — the model handles it natively.
*   **Status:** *Future / exploratory. Depends on Ollama exposing multimodal embedding support for Gemma 4 and on Slack delivering image/audio payloads to the message handler.*

---

## 💾 5. Memory & Storage

### 5.1 Fix `query_memory` Vector Search ✅
*   **Status:** *Resolved — migrated from sqlite-vss to sqlite-vec, replaced `HAS_VSS`/`VECTORS_LOAD_EXTENSION` gates with single `self._enabled` flag. See `build-plan/phase1_preflight_rag.md`.*

### 5.2 Auto-generated Note Titles ✅
*   **Status:** *Implemented — see `build-plan/note_and_board_formatting.md`*
*   **Problem:** `add_note` currently only stores raw content and tags. Without a title, the long-term Markdown logs are difficult to skim for specific topics.
*   **Idea:** A sub-function or enhancement to the note-taking process that automatically generates a concise, descriptive title. This title would be stored as a header in the Markdown logs.
*   **Benefit:** Makes the "Truth Layer" much more readable for humans and provides a high-density signal for future RAG/search queries.

### 5.2 High-Density Note Retrieval ✅
*   **Status:** *Implemented — see `build-plan/note_and_board_formatting.md`*
*   **Problem:** `get_recent_notes` currently returns a raw list with timestamps, which is visually noisy and hard for the LLM (or user) to digest quickly.
*   **Idea:** Format retrieved notes into a structured "bulletin" or "thematic group" rather than just a chronological dump.
*   **Benefit:** Reduces token usage in the prompt and makes the context much clearer for Grug's reasoning.
