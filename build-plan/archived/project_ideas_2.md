# Project Ideas: Multi-Agent Personas

As the `opengrug` architecture sheds its initial constraints and decouples the entrypoint, the single largest architectural win on the horizon is **Multi-Agent Personas**. 

Currently, Grug is monolithic: every single tool (writing notes, reading the task board, checking infrastructure health, scheduling cron jobs) is jammed into one massive LLM system prompt. 

As features scale, this "God Prompt" approach fails:
1. It suffocates the LLM context window.
2. It increases latency because the LLM has to parse 20 irrelevant schema objects.
3. It confuses lower-parameter edge models (like Gemma or Llama 8B) leading to poor confidence scores.

## The Evolution

Once the codebase delegates tool-registration to specific modules (e.g. `tasks.py` registers its own boundaries rather than `app.py` registering all of them), we can spin up isolated **Sub-Agents**. 

Instead of one generic `GrugRouter`, the Orchestrator initializes specialized personas:

### 1. The Core Orchestrator (The Dispatcher)
The Dispatcher is incredibly fast and uses virtually no tools. Its only job is routing intent. When the user says, *"List my bugs and then start a bash terminal"*, the Orchestrator evaluates the intent and dispatches to the correct Personas sequentially.

### 2. TaskGrug (The Project Manager)
Loaded *only* with `Board`, `Note`, and `Schedule` schemas. It has no idea what system health or bash terminals are. Because its prompt is hyper-focused on project management, its reasoning and schema-compliance is nearly flawless.

### 3. CodeGrug (The Engineer)
Loaded *only* with `CLI`, `Bash`, `Git`, and `File` schemas. Tasked strictly with editing the repository or running safe destructive actions.

### 4. AdminGrug (The SRE)
Loaded *only* with `Health`, `Logs`, and `Infrastructure` schemas. It handles diagnostics without having context stuffed with conversational history from your tasks board.

## The Benefits
- **Token Efficiency:** You only send relevant context to the model that needs it.
- **Model Interoperability:** You could have `CodeGrug` powered by a stronger model, while `TaskGrug` runs locally on a tiny, incredibly fast 7B model.
- **Strict Boundaries:** You never have to worry about a note-taking query accidentally triggering a bash script because the tool simply doesn't exist in that Agent's universe.
