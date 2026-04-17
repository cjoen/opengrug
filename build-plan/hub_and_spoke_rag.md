# Hub-and-Spoke RAG Architecture

**Status:** Research / Planning
**Created:** 2026-04-17

## Problem

RAG is currently underutilized. The LLM must *choose* to call `query_memory` as a tool — a small edge model frequently doesn't. The system prompt has all-caps `CRITICAL` nudges begging the model to search, which is a workaround for a structural problem. Vector search is gated behind an env flag and has a known crash bug when disabled.

Meanwhile, the physical topology is already two machines (Ubuntu server running OpenGrug, Mac Mini M2 running Ollama) but the M2 is only used as a dumb inference endpoint. Embeddings run on Ubuntu CPU, and there's a network hop between every vector search and LLM call.

## Goal

1. Make RAG automatic (pre-flight injection) instead of LLM-gated.
2. Formalize the two-machine topology into a Hub (M2) and Runner (Ubuntu).
3. Move data + brains to the M2 where they belong. Lighten the Ubuntu runner.

## Architecture

```
Ubuntu (Runner)                    Mac Mini M2 (Hub)
┌─────────────┐                   ┌──────────────────────┐
│ Slack bot    │  ── question ──> │ FastAPI wrapper       │
│ HITL/queue   │                  │  ├─ Ollama (LLM)     │
│ Scheduler    │  <── answer ───  │  ├─ sqlite-vec        │
│ Session mgmt │                  │  ├─ SentenceTransform │
└─────────────┘                   │  ├─ brain/ (markdown) │
                                  │  ├─ prompts/          │
                                  │  └─ RAG pre-flight    │
                                  └──────────────────────┘
```

## Hub API Surface

```
POST /ask              question + history in, structured result out
POST /notes            add_note (write to brain/daily_notes)
GET  /notes/recent     get_recent_notes
GET  /notes/search     keyword + vector search combined
POST /tasks            add/edit task
GET  /tasks            list tasks
GET  /tasks/summary    summarize board
```

`/ask` is the core endpoint. It:
1. Embeds the query via SentenceTransformers (on M2 Metal/ANE)
2. Searches sqlite-vec for relevant context (pre-flight RAG)
3. Assembles system prompt (base + summaries + capped tail + RAG hits)
4. Calls Ollama (localhost — no network hop)
5. Parses think-then-act JSON, executes tool logic
6. Returns structured result to the runner

## What moves where

### To Hub (Mac Mini M2)
- `core/llm.py` — OllamaClient (ollama_host becomes localhost)
- `core/vectors.py` — VectorMemory (always-on, switch sqlite-vss to sqlite-vec)
- `core/context.py` — prompt assembly
- `core/storage.py` — markdown truth layer
- `core/summarizer.py` — needs LLM access
- `core/router.py` — routing engine lives with the LLM
- `prompts/` — system prompt files
- `brain/` — daily_notes, summaries, tasks.md
- `workers/background.py` — indexer + nightly summarize

### Stays on Runner (Ubuntu)
- `app.py` — Slack bot, simplified process_message
- `core/queue.py` — message queue
- `core/sessions.py` — session store (ephemeral, per-conversation)
- `core/scheduler.py` — schedule store
- `core/registry.py` — simplified, only local tool dispatch
- `tools/scheduler_tools.py` — schedule CRUD
- `tools/system.py` — reply_to_user, clarification (Slack-facing)

### New files
- `hub/api.py` — FastAPI app (~100-150 lines)
- `runner/hub_client.py` — httpx client (~50 lines, single `HUB_URL` config)

### Deleted from Runner
- `core/router.py` — routing moves to hub
- `core/vectors.py` — no local vector search
- `core/summarizer.py` — summarization moves to hub
- `sentence-transformers` dependency (~400MB)
- `sqlite-vss` dependency

## Migration Steps

### Phase 1: Pre-flight RAG on existing architecture
Before splitting services, prove out pre-flight RAG injection in the current monolith. This validates the approach with minimal risk.

- [ ] Fix `query_memory` crash when `self.model is None` (known bug)
- [ ] In `process_message`, add vector search before `router.route_message()`
- [ ] Inject RAG hits into system prompt as `## Relevant Memory` section
- [ ] Evaluate: does pre-flight context improve response quality vs LLM-gated `query_memory`?
- [ ] Consider switching `sqlite-vss` to `sqlite-vec` (pip-installable, maintained)

### Phase 2: Build the Hub API
Stand up FastAPI on the M2 alongside Ollama. Hub serves the `/ask` endpoint.

- [ ] Create `hub/api.py` with `/ask` endpoint wrapping router + pre-flight RAG
- [ ] Move `core/llm.py`, `core/vectors.py`, `core/context.py`, `core/router.py`, `core/storage.py`, `core/summarizer.py` into hub
- [ ] Move `brain/` and `prompts/` to M2
- [ ] Add `/notes`, `/tasks`, `/notes/search` endpoints
- [ ] Test hub standalone — send questions via curl, verify RAG context + LLM response

### Phase 3: Wire the Runner to the Hub
Point OpenGrug at the hub API instead of calling Ollama directly.

- [ ] Create `runner/hub_client.py` (httpx, `HUB_URL` env var)
- [ ] Rewrite `process_message` to call `hub_client.ask()` instead of local routing
- [ ] Rewrite `tools/notes.py`, `tools/tasks.py`, `tools/search.py` as thin hub API wrappers
- [ ] Keep old code path as fallback behind a feature flag during transition
- [ ] Test end-to-end: Slack message → Ubuntu queue → M2 hub → response posted

### Phase 4: Strip the Runner
Remove everything the runner no longer needs.

- [ ] Remove `core/router.py`, `core/vectors.py`, `core/summarizer.py`, `core/context.py` from runner
- [ ] Remove `sentence-transformers`, `sqlite-vss` from runner requirements
- [ ] Remove `brain/`, `prompts/` from Ubuntu
- [ ] Remove background indexer and nightly summarize threads from runner
- [ ] Update `grug_config.json` — split into hub config and runner config
- [ ] Update Docker setup if applicable

## Research TODOs

Before committing to this plan, investigate:

- [ ] **sqlite-vec vs sqlite-vss**: sqlite-vec is the maintained successor, pip-installable, works on macOS without extension loading hacks. Confirm it covers current usage.
- [ ] **Embedding model on M2**: Benchmark `all-MiniLM-L6-v2` on Apple Silicon vs Ubuntu CPU. Confirm Metal/CoreML acceleration works with SentenceTransformers.
- [ ] **RAG quality**: What chunk size / overlap works best for the bullet-point markdown format? Current approach indexes individual `- ` lines — is that granular enough or too granular?
- [ ] **Ollama embeddings**: Ollama can generate embeddings natively (`/api/embeddings`). Could replace SentenceTransformers entirely, using the same model or a dedicated embedding model. Fewer dependencies on the hub.
- [ ] **FastAPI deployment**: uvicorn behind what? Bare process, systemd service, or container on the M2?
- [ ] **Network reliability**: What happens when the hub is unreachable? Runner needs a graceful degradation path (queue backpressure, retry, or cached fallback).
- [ ] **brain/ migration**: One-time rsync, or set up the hub to own writes from day one? Need to handle the transition period where both machines might write.

## Open Questions

- Should the hub also own session state, or keep that on the runner (closer to Slack)?
- Should scheduled tool executions (scheduler_poll_loop) call through the hub API, or does the hub expose a `/execute_tool` endpoint?
- Is there value in the hub serving multiple runners eventually (CLI tool, web UI, different Slack workspaces)?
