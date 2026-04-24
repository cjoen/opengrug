# Phase 5: Multi-Agent Personas & External I/O Plan

This phase transforms OpenGrug from a single monolithic agent ("God Prompt") into a specialized swarm of personas. By doing this, we keep the context window small for edge models (like Gemma 4), improve tool usage reliability by limiting the number of tools presented at once, and allow targeted tuning (e.g., temperature) per persona.

## 1. The Multi-Agent Architecture

We will implement a **Dispatcher -> Specialized Persona** pattern.

### The Dispatcher (The Router)
- **Role:** Analyzes the user's input and determines the required persona to handle the request.
- **Prompt:** Extremely lightweight. Only knows about available personas, not tools.
- **Tools:** None. It simply returns the intent/persona name.
- **Config:** Temperature `0.0`.

### The Personas
1. **TaskGrug (Current Core)**
   - **Role:** Handles task lists, notes, scheduling, and CRUD operations.
   - **Tools:** `add_note`, `query_memory`, `add_task`, `list_tasks`, `complete_task`, `remind_me`, `add_schedule`, etc.
   - **Temperature:** `0.0` (needs high precision for tool calls).
2. **ResearcherGrug (New)**
   - **Role:** Fetches external information, reads web pages, parses RSS feeds.
   - **Tools:** `fetch_rss`, `search_web`, `read_url` (to be implemented).
   - **Temperature:** `0.3` (slight creativity for extracting/summarizing information).
3. **SummarizerGrug / AARGrug (New)**
   - **Role:** Analyzes conversation history to generate daily briefs or extract lessons learned.
   - **Tools:** `add_note` (to save the brief).
   - **Temperature:** `0.6` (high creativity for synthesis).

## 2. Implementation Steps

### Step 1: Tool Registry Updates
Update `core/registry.py` and `tools/__init__.py` to support namespaced or tagged tools.
- Instead of the LLM receiving *all* registered tools, the `Orchestrator` will request only the tools associated with the active Persona.

### Step 2: Prompt Refactoring
- Split `prompts/system.md` into smaller files:
  - `prompts/personas/dispatcher.md`
  - `prompts/personas/task_grug.md`
  - `prompts/personas/researcher.md`
- The context builder will need to dynamically load the prompt based on the active persona.

### Step 3: The Dispatcher Implementation
- When a user message arrives, the `Orchestrator` first calls the LLM with the Dispatcher persona.
- The Dispatcher evaluates the text and returns an enum/structured response (e.g., `ROUTE_TO_RESEARCHER`).
- The `Orchestrator` then loads the specific Persona prompt, injects the correct tools, and starts the standard `StepLoop`.

### Step 4: External I/O Tools (`tools/research.py`)
- Implement `tools/research.py` with external network calls.
- **`search_web(query)`**: Using a lightweight API (like DuckDuckGo or a Serper API) to return search results.
- **`read_url(url)`**: Fetch a webpage, strip HTML, convert to markdown, and return.
- **`fetch_rss(feed_url)`**: Parse an XML feed and return recent items.

### Step 5: Markdown Skills (The Daily Brief)
- Create the first declarative skill in `skills/daily_brief.md`.
- Instructs ResearcherGrug to read predefined RSS feeds, summarize them, and save a "Daily Brief" note to Obsidian.
- Wire this skill to the `workers/background.py` to trigger every morning via the task queue.

## 3. Discussion Points / Brainstorming
1. **Routing Strategy:** Should the Dispatcher be an LLM call, or a faster classification technique (like embedding similarity or a tiny local classification model)? An LLM call is simpler but adds latency.
2. **Context Passing:** When switching from Dispatcher to Persona, do we clear the conversation history or keep it?
3. **Multi-Step Workflows (Skills):** The backlog mentions defining multi-step agentic workflows in Markdown. How complex do we want the executor to be? Should it be a simple sequential loop, or an autonomous "plan and execute" loop?
