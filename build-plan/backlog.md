# OpenGrug Backlog

Ideas and future improvements that haven't been implemented yet. These are not active tasks — they're candidates for future work.

---

## RAG & Embeddings

### Ollama Embeddings
Replace SentenceTransformers with Ollama's `/api/embeddings`:
- Drop ~400MB dependency from Ubuntu
- Run embeddings on M2 Apple Silicon (faster)
- One HTTP call per embed (same pattern as inference)
- Tradeoff: network round-trip per embedding vs local CPU encode
- Scoped to `core/vectors.py` only — no architecture change

### RAG Quality Tuning
- **Chunk granularity**: Currently indexes individual `- ` bullet lines. Experiment with paragraph-level chunks.
- **Distance filtering**: Skip hits above a distance threshold to avoid injecting irrelevant context.
- **Result limit**: `grug_config.json` → `memory.rag_result_limit` (currently 3). Tune based on observation.

---

## LLM Backend

### Swappable LLM Backend
Wrap `OllamaClient` behind an interface so backend is config-driven:
```python
# grug_config.json
{"llm": {"backend": "ollama"}}   # or "anthropic" or "google"
```
Worth doing if/when you actually want to try a cloud API.

### ~~Gemma 4 Control Token Optimization~~ ✅ Done
Completed as part of the Native Tool Migration (2026-04-20). Ollama now handles Gemma 4's `<|tool>`, `<|tool_call>`, `<|tool_response>` control tokens automatically when tools are passed via the `/api/chat` `tools` array. No manual token injection needed.

### Gemma 4 Multimodal Input (Image & Audio)
Use Gemma 4's native `<|image>` / `<|audio>` tokens for multimodal input:
- Users could drop screenshots into Slack for Grug to reason about
- Voice notes via audio token path
- Depends on Ollama multimodal support and Slack image/audio payload handling

---

## Intelligence & Autonomy

### Grug Self-Learning Memory (`grug.md`)
A persistent file (like `claude.md`) where Grug records its own mistakes and learned preferences via an `add_instruction` tool. Allows autonomous behavior improvement over time.

### Multi-Agent Personas
Split the single God Prompt into specialized sub-agents:
- **Dispatcher** — fast intent routing, no tools
- **TaskGrug** — Board/Note/Schedule schemas only
- **CodeGrug** — CLI/Bash/Git/File schemas only
- **AdminGrug** — Health/Logs/Infrastructure only

Benefits: token efficiency, model interoperability (strong model for code, fast model for tasks), strict tool boundaries.

---

## UX

### ~~Consistent Tool Completion Responses~~ ✅ Done
Completed 2026-04-20. All action tools now return meaningful confirmation strings (e.g. "Task #4 added: Fix login [high]"). Router implements tool-output-wins precedence — if an action tool returns output, `reply_to_user` is suppressed. No more bare "Done." fallbacks.

### ~~Remove Confidence Score System~~ ✅ Done
Completed as part of the Native Tool Migration (2026-04-20). Removed `confidence_score` from output format, all prompt files and few-shot examples, `_parse_and_execute` gating logic, trace logging, and `low_confidence_threshold` config key.

### ~~Stable IDs for CRUD Items~~ ✅ Done
Completed 2026-04-20. Tasks migrated from markdown line-number references to SQLite-backed `TaskStore` with auto-incrementing IDs (e.g. "complete task #7"). Schedules already had stable IDs. Notes are append-only and don't need CRUD IDs.

### Markdown Skill / Agent Framework
Define multi-step agentic workflows as `.md` files in a `skills/` directory. Each skill file declares a goal, available tools, and instructions. A step loop executes the skill by feeding the file as the system prompt and calling the LLM repeatedly — one tool call per iteration — with accumulated results in history until the goal is complete.

Example skill file:
```markdown
# RSS & YouTube Report
## Goal
Check feeds and channels for new content, summarize into a report.
## Sources
- RSS: https://example.com/feed.xml
- YouTube: @channelname
## Tools Available
- fetch_rss, fetch_youtube, summarize_text, reply_to_user
## Instructions
1. Fetch each source for items from the last 24 hours
2. Summarize new items grouped by source
3. Reply with the report
```

Each step is a single tool call — keeps the model's job simple. Works with e4b since it only needs to reason about one action at a time. Users can create new workflows by dropping a `.md` file without writing Python. Natural complement to the step-loop pattern and stable IDs.

### Configurable Tone & Persona
Move tone settings into `grug_config.json`. Allow switching between "Caveman Grug" and "Engineer" mode for technical work. Could be user-toggled via Slack command.

### Agent Task Queue
Markdown-backed task list for Grug (separate from user tasks) with nightly processing. Users add items via natural language ("add to your task list: xyz") and Grug works through them overnight via the LLM router.

**Build plan:** [`build-plan/agent_tasks.md`](agent_tasks.md)
