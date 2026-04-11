# Slack-Gemma SQLite Bot - Implementation Plan

This document outlines the architecture and tasks for building the Python middleware that connects Slack (Socket Mode) to a local Gemma edge model and SQLite database. **(Instructions intended for Claude)**

## 1. Technical Stack
* **Language**: Python 3.11+
* **Slack Integration**: `slack_bolt` (using Socket Mode to avoid exposing webhooks)
* **LLM Engine**: `ollama` Python client (running the Gemma model locally via localhost:11434)
* **Database**: `sqlite3` (built-in Python library)
* **Date Parsing**: `datetime` / `python-dateutil`

## 2. Python Architecture & Modules to Build

### A. `database.py` (SQLite Wrapper)
* **Goal:** Handle all SQLite operations safely safely.
* **Functions to implement:**
  - `init_db()`: Checks if `notes.db` exists. Creates `notes` and `tasks` tables if not.
  - `add_note(content: str, tags: list[str])` -> Returns success message/ID
  - `add_task(description: str, due_date: str, assignee: str)` -> Returns success message/ID
  - `get_recent_notes(limit=3)` -> Returns recent notes for RAG context

### B. `llm_router.py` (Ollama & Prompt Construction)
* **Goal:** Stitch the `.md` context files together and manage the local LLM interaction.
* **Functions to implement:**
  - `build_prompt(slack_text)`: Reads `prompts/system.md`, `prompts/rules.md`, `prompts/memory.md`, and `prompts/schema_examples.md`. Concatenates them, injects `datetime.now().strftime("%Y-%m-%d")` into the placeholder, and appends the user's slack message.
  - `ask_gemma(prompt)`: Calls the local Ollama API. **Crucial:** Must pass `format="json"` in the API request to pin the model output to structured JSON.

### C. `tools.py` (JSON Validation & Execution)
* **Goal:** Act as the validation harness to keep the model honest and manage escalation.
* **Functions to implement:**
  - `execute_tool(json_payload)`: 
    1. Parse the JSON from Gemma.
    2. Check `confidence_score`. If under 8, OR if `tool == "escalate_to_frontier"`, bypass Gemma and forward the original Slack message to a larger cloud model (like Anthropic Claude API) to process the intent instead.
    3. If the frontier model also fails, or if `tool == "ask_for_clarification"`, return the `reason_for_confusion` to the user.
    4. Call the corresponding function in `database.py` based on the `tool` key.

### D. `app.py` (Slack Socket App)
* **Goal:** Main entry point listening to the event stream.
* **Tasks:**
  - Initialize the `App(token=SLACK_BOT_TOKEN)` and `SocketModeHandler(app, SLACK_APP_TOKEN)`.
  - Create a `@app.message(".*")` listener.
  - On incoming message:
    1. Send a visually un-intrusive "thinking" reaction (e.g., :thought_balloon: ) to the slack message.
    2. Pass text to `llm_router`.
    3. Pass resulting JSON into `tools.py`.
    4. Post the execution result back (e.g., *"Added task for next Monday."*) to the thread or channel.

## 3. Database Schema
**Table: notes**
- `id` (INTEGER PRIMARY KEY AUTOINCREMENT)
- `content` (TEXT)
- `tags` (TEXT) - stored as comma-separated string
- `created_at` (TIMESTAMP DEFAULT CURRENT_TIMESTAMP)

**Table: tasks**
- `id` (INTEGER PRIMARY KEY AUTOINCREMENT)
- `description` (TEXT)
- `due_date` (DATE nullable)
- `assignee` (TEXT)
- `status` (TEXT DEFAULT 'pending')
- `created_at` (TIMESTAMP DEFAULT CURRENT_TIMESTAMP)
