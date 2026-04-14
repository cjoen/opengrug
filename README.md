# Grug: The Caveman Context Router

Grug is a lightweight, edge-first Slack bot designed for speed, portability, and zero-bullshit token compression.

Unlike heavy cloud memory systems that hide your thoughts in opaque databases, Grug uses **Markdown as Truth** and **SQLite as Cache**. You write plain text, and Grug seamlessly vectors it for semantic search.

## Quick Start

Just run the wizard. It will ask for your keys, carve out your local memory caves, and start Docker.
```bash
chmod +x setup.sh
./setup.sh
```

## Philosophy

1. **Lightweight & Portable**: Everything runs in a `docker-compose` sandbox. Moving machines? Zip the `/brain` folder and `docker-compose up` on your new host.
2. **"Caveman" Token Compression**: Edge models like Gemma e4b have strict context lengths. Grug compresses system prompts using maximum brevity to save tokens.
3. **No Arbitrary Bash**: The AI is banned from arbitrary execution. Grug safely maps the LLM's JSON into strict Python arguments and whitelisted CLI binaries, pausing for Human-in-the-Loop (HITL) approval on anything destructive.
4. **Fully Local — No Cloud LLM**: Runs entirely on local Ollama. Low-confidence responses ask the user for clarification instead of escalating to a cloud model.

## Architecture

```
app.py                  — Wiring: init, register tools, Slack handlers, main
core/
  llm.py                — OllamaClient (single Ollama HTTP integration point)
  registry.py           — ToolRegistry + schema validation + HITL gate
  router.py             — GrugRouter: shortcut → LLM → parse → dispatch
  context.py            — System prompt assembly + turn pruning
  storage.py            — Daily markdown notes (the Truth Layer)
  sessions.py           — SQLite session store for Slack threads
  summarizer.py         — LLM-powered summarization (daily, prune, idle)
  scheduler.py          — SQLite scheduler for cron jobs + one-shot tasks
  vectors.py            — Sentence-transformer embeddings + sqlite-vss
  config.py             — Config loader (grug_config.json + env overrides)
tools/
  notes.py              — add_note, get_recent_notes
  tasks.py              — TaskBoard (add/list/edit tasks, summarize board)
  system.py             — clarification, reply, list capabilities
  scheduler_tools.py    — add/list/cancel scheduled tasks
workers/
  background.py         — Boot summarize, idle sweep, nightly cron, scheduler poll
```

## Storage
All memories are saved to `/brain/daily_notes/`. Tasks live in `/brain/tasks.md` as plain markdown checkboxes. Schedules are stored in `/brain/schedules.db`.

If something gets corrupted, **forget the database**. Edit the markdown directly — Grug's background `VectorMemory` daemon will detect changes and re-index the cache.

## Scheduler

Grug can run any registered tool on a cron schedule or at a specific time. Reminders are just scheduled `reply_to_user` calls.

```
"remind me to check deploys every Monday 9am"
→ add_schedule(tool_name="reply_to_user", schedule="0 9 * * 1")

"save a daily checkpoint note at midnight"
→ add_schedule(tool_name="add_note", schedule="0 0 * * *")

"remind me to review the PR at 3pm today"
→ add_schedule(tool_name="reply_to_user", schedule="2026-04-14T15:00:00")
```

## Configuration

Settings live in `grug_config.json`. Sections:
- `llm` — model name, ollama host, context tokens, temperature, confidence threshold
- `memory` — summary limits, capped tail lines, idle timeout, RAG settings
- `storage` — base directory, session TTL, subprocess timeout
- `shortcuts` — prefix and alias mappings (e.g. `/note` → `add_note`)
- `scheduler` — poll interval, database file

Environment variable overrides: `OLLAMA_HOST`, `DOCKER`, `SLACK_BOT_TOKEN`, `SLACK_APP_TOKEN`.

## Host Volume Permissions

The container runs as UID 1000 (non-root). Before the first `docker-compose up`:

```bash
sudo chown -R 1000:1000 ./brain
```

To match a different host UID, pass `--build-arg UID=<your-uid> --build-arg GID=<your-gid>` to `docker build` and update `docker-compose.yml`.

## Adding CLI Tools

To add a CLI to the `ToolRegistry`, it must pass these criteria:

1. **Stateless Auth**: Authenticates via env vars or mounted credentials. No interactive browser OAuth.
2. **Structured Output**: Supports `--output json` or equivalent. No colorful ASCII tables.
3. **Non-Interactive**: Accepts all parameters via flags. No `"Are you sure? [y/N]"` prompts.
4. **Predictable Exit Codes**: Returns 0 on success, >0 on failure.
