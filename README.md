# OpenGrug

**A lightweight, self-hosted LLM assistant built for edge models (2B-10B parameters) and minimal infrastructure.**

OpenGrug is a Slack-integrated AI assistant that runs alongside local or remote LLMs inside a single Docker container. It was designed for homelab setups or budget cloud deployments where resources are limited but reliability matters.

## Key Features

- **Native Tool Calling** — Setup with Ollama's `/api/chat` tool interface for direct, multi-tool invocations per turn. No brittle JSON parsing.
- **RAG with Semantic Search** — Sentence-transformer embeddings stored in `sqlite-vec` for fast, local vector search over your notes, history, and tools.
- **Markdown as Truth, SQLite as Cache** — All persistent data lives in plain markdown files. SQLite indexes and vectors are derived and rebuildable.
- **Human-in-the-Loop Gating** — Destructive tools require explicit user approval before execution. Safe tools run immediately.
- **Scheduler** — Cron-based and one-shot task scheduling backed by SQLite. Reminders, recurring notes, and automated tool calls.
- **Message Queue** — Incoming messages are queued and drained one thread at a time if the self hosted llm is busy, preventing race conditions during concurrent use.
- **Portable** — Everything runs via `docker-compose`. Move hosts by copying the `/brain` directory.

## Architecture

```
app.py                  — Entry point: init, tool registration, Slack handlers
core/
  llm.py                — OllamaClient (single Ollama HTTP integration point)
  registry.py           — ToolRegistry + JSON Schema validation + HITL gate
  router.py             — Shortcut → LLM → parse → dispatch (think-then-act)
  queue.py              — Thread-draining message queue
  context.py            — System prompt assembly + turn pruning
  storage.py            — Daily markdown notes (the Truth Layer)
  sessions.py           — SQLite session store for Slack threads
  summarizer.py         — LLM-powered summarization (daily, prune, idle)
  scheduler.py          — Cron + one-shot task scheduler
  vectors.py            — Sentence-transformer embeddings + sqlite-vec
  config.py             — Config loader (grug_config.json + env overrides)
tools/
  notes.py              — add_note, get_recent_notes
  tasks.py              — TaskBoard (add/list/edit tasks, summarize board)
  system.py             — clarification, reply, list capabilities
  scheduler_tools.py    — add/list/cancel scheduled tasks
workers/
  background.py         — Boot summarize, idle sweep, nightly cron, scheduler poll
evals/
  run_evals.py          — LLM reasoning eval harness (hits live Ollama)
  golden_dataset.jsonl  — Test cases: routing, args, adversarial, multi-tool
tests/
  test_*.py             — Deterministic pytest suite (no LLM required)
```

## Testing

OpenGrug has two separate test pipelines:

| Pipeline | Location | Speed | What it tests |
|---|---|---|---|
| **Unit/Integration Tests** | `tests/` | Fast (no LLM) | Code logic: DB ops, routing dispatch, schema validation |
| **LLM Evals** | `evals/` | Slow (live Ollama) | Model reasoning: tool selection, argument extraction, injection resistance |

```bash
# Run deterministic tests (no Ollama required)
pytest tests/

# Run LLM reasoning evals against local Ollama
export OLLAMA_HOST="http://localhost:11434"
python3 evals/run_evals.py

# Filter evals by category or save results
python3 evals/run_evals.py --category ADVERSARIAL
python3 evals/run_evals.py --output evals/results.json
```

See `evals/README.md` for full details on the dataset format and adding new test cases.

## Quick Start

```bash
./setup.sh
```

The setup wizard prompts for API keys, creates the `/brain` directory, and starts Docker.

## Deployment

| Profile          | Hardware            | Notes                                                              |
| ---------------- | ------------------- | ------------------------------------------------------------------ |
| **Local models** | 8+ GB RAM, 4+ cores | Ollama runs gemma or mistral locally. No API costs, works offline. |

## Configuration

Settings live in `grug_config.json`:

| Section     | Controls                                                              |
| ----------- | --------------------------------------------------------------------- |
| `llm`       | Model, Ollama host, context window, temperature, confidence threshold |
| `memory`    | Summary limits, tail lines, idle timeout, RAG settings                |
| `storage`   | Base directory, session TTL, subprocess timeout                       |
| `shortcuts` | Prefix/alias mappings (e.g. `/note` → `add_note`)                     |
| `scheduler` | Poll interval, database path                                          |
| `queue`     | Worker count (scale with Ollama instances)                            |

Environment overrides: `OLLAMA_HOST`, `DOCKER`, `SLACK_BOT_TOKEN`, `SLACK_APP_TOKEN`.

---

> *Why use big brain in sky when smol local brain work just fine?* - grug

