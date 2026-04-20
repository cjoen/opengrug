# RAG Architecture Plan

**Status:** Phase 1 complete, future phases on hold
**Created:** 2026-04-17
**Updated:** 2026-04-17

## Problem (Resolved)

RAG was LLM-gated — the model had to choose to call `query_memory`, which it frequently didn't. Vector search was gated behind an env flag with a known crash bug.

**Phase 1 fixed this.** Pre-flight RAG injection now runs automatically on every message. sqlite-vss was replaced with sqlite-vec. See [phase1_preflight_rag.md](phase1_preflight_rag.md) for details.

## Current Architecture

Everything runs on Ubuntu as a single monolith. Ollama runs on a Mac Mini M2 and is called over HTTP for inference only.

```
Ubuntu Server (OpenGrug — monolith)          Mac Mini M2
┌──────────────────────────────┐            ┌────────────┐
│ Slack bot + queue            │            │            │
│ brain/ (markdown truth)      │            │            │
│ sqlite-vec (vector search)   │            │            │
│ SentenceTransformers (embed) │            │            │
│ RAG pre-flight               │            │            │
│ Context assembly + routing   │  ─ HTTP ─> │ Ollama     │
│ Sessions + scheduler         │            │            │
│ Background workers           │            │            │
└──────────────────────────────┘            └────────────┘
```

## Why not split into services

The original plan proposed moving brain/, vectors, and routing to the M2 as a "hub" to co-locate data with inference. But this creates a problem:

- **If we switch to a cloud LLM (Claude, Gemini)**, the M2 is no longer the center of gravity. Notes would need to move back to Ubuntu, or to a third location. The split would have to be undone or restructured.
- **If we stay on Ollama**, the split saves one network hop per query but adds operational complexity (two deployments, data migration, API surface to maintain).
- **The monolith is already flexible.** Swapping `OllamaClient` for an Anthropic or Google client is a one-file change. Notes stay local on Ubuntu regardless of which LLM backend is used.

The pre-flight RAG injection (Phase 1) already delivers the main quality win. The service split was primarily an optimization for co-locating data with local inference — a commitment to a specific LLM strategy we're not ready to make.

## Decision

Keep the monolith. Keep notes and vectors on Ubuntu. Keep LLM choice flexible. Revisit service split only if a clear operational need arises (e.g., multiple clients need to share the knowledge base).

## Future improvements (no service split required)

### Ollama embeddings
Ollama exposes `/api/embeddings`. Could replace SentenceTransformers entirely:
- Drop ~400MB dependency from Ubuntu
- Run embeddings on M2 Apple Silicon (faster)
- One HTTP call per embed (same pattern as inference)
- Tradeoff: network round-trip per embedding vs local CPU encode

This is a good next step if SentenceTransformers becomes a bottleneck or maintenance burden. It's a change inside `core/vectors.py` only — no architecture change.

### RAG quality tuning
- **Chunk granularity**: Currently indexes individual `- ` bullet lines. May be too granular (loses context) or not granular enough (misses multi-line entries). Experiment with paragraph-level chunks.
- **Distance filtering**: Skip hits above a distance threshold to avoid injecting irrelevant context.
- **Result limit**: `grug_config.json` → `memory.rag_result_limit` (currently 3). Tune based on observation.

### Swappable LLM backend
The current `OllamaClient` could be wrapped behind an interface so swapping to Claude/Gemini is config-driven rather than a code change:

```python
# grug_config.json
{"llm": {"backend": "ollama"}}   # or "anthropic" or "google"
```

This keeps the monolith but makes the LLM backend a configuration choice. Worth doing if/when you actually want to try a cloud API.

## Archived: original hub-and-spoke design

The original service-split plan is preserved below for reference. It remains a valid architecture if the decision is made to commit to local Ollama long-term and/or serve multiple clients.

<details>
<summary>Original hub-and-spoke proposal</summary>

### Architecture

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

### Hub API surface

```
POST /ask              question + history in, structured result out
POST /notes            add_note (write to brain/daily_notes)
GET  /notes/recent     get_recent_notes
GET  /notes/search     keyword + vector search combined
POST /tasks            add/edit task
GET  /tasks            list tasks
GET  /tasks/summary    summarize board
```

### What would move to M2
- `core/llm.py`, `core/vectors.py`, `core/context.py`, `core/router.py`, `core/storage.py`, `core/summarizer.py`
- `prompts/`, `brain/`, `workers/background.py`

### What would stay on Ubuntu
- `app.py`, `core/queue.py`, `core/sessions.py`, `core/scheduler.py`, `core/registry.py`
- `tools/scheduler_tools.py`, `tools/system.py`

### When this makes sense
- Committed to local Ollama as the sole LLM backend
- Multiple clients need shared access to the knowledge base (CLI, web UI, multiple Slack workspaces)
- Ubuntu server is resource-constrained and needs to offload embedding + inference work

</details>
