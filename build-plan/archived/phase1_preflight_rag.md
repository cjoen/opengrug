# Phase 1: Pre-flight RAG Injection

**Status:** Implemented
**Created:** 2026-04-17
**Parent plan:** [hub_and_spoke_rag.md](hub_and_spoke_rag.md)

## Objective

Make RAG automatic instead of LLM-gated. Previously, the LLM had to *choose* to call `query_memory` as a tool — a small edge model frequently didn't, even with all-caps system prompt nudges. Phase 1 injects vector search results into the system prompt *before* the LLM reasons, so every query gets relevant context automatically.

No architecture changes. No service splits. Everything stays on Ubuntu.

## What was done

### 0. Migrated sqlite-vss to sqlite-vec
**File:** `core/vectors.py`, `requirements.txt`

Replaced sqlite-vss (unmaintained, required extension-loading hacks and `VECTORS_LOAD_EXTENSION` env flag) with sqlite-vec (pip-installable, maintained successor by same author).

Changes:
- Replaced `import sqlite_vss` with `import sqlite_vec`
- Removed `HAS_VSS` module-level flag and `VECTORS_LOAD_EXTENSION` env gate
- Added single `self._enabled` flag that covers all failure modes (model load failure, DB init failure)
- Changed virtual table from `vss0(embedding(N))` to `vec0(embedding float[N])`
- Changed query from `vss_search(embedding, ?)` to `embedding MATCH ? AND k = ?`
- Changed embedding serialization from JSON float arrays to binary blobs via `struct.pack`
- Added `_serialize_embedding()` helper for float32 binary serialization

### 1. Fixed query_memory crash
**File:** `core/vectors.py`

The `self._enabled` flag replaces the old `HAS_VSS` / `self.model is None` guards. All public methods (`index_markdown_directory`, `start_background_indexer`, `query_memory`) check `self._enabled` and degrade gracefully — returning early or returning the offline fallback list.

### 2. Added pre-flight RAG injection in process_message
**File:** `app.py`

Before calling `router.route_message()`, the worker now:
1. Calls `vector_memory.query_memory(text, limit=config.memory.rag_result_limit)`
2. If results are available (not offline), joins them into a string
3. Passes `rag_context` to `build_system_prompt()`

Wrapped in try/except so vector failures never break message processing.

### 3. Updated build_system_prompt to accept RAG context
**File:** `core/context.py`

Added optional `rag_context=""` parameter. When non-empty, inserts a `## Relevant Memory` section between summaries and today's notes:

```
## Recent Summaries (last 7 days)
...
## Relevant Memory          <-- NEW: semantically relevant hits
...
## Today's Notes
...
```

### 4. Added RAG to HITL re-infer path
**File:** `app.py`, `_re_infer()` function

After a HITL approval triggers re-inference, the same pre-flight RAG pattern is applied using the last user message from the thread history as the query.

### 5. Updated system prompt
**File:** `prompts/system.md`

- Replaced two `CRITICAL` directives that begged the model to manually call `query_memory`
- New wording tells the model that `## Relevant Memory` is automatically populated
- `query_memory` tool remains available for explicit deeper searches

### 6. Cleaned up sqlite-vss remnants
Updated references across non-archived files:
- `requirements.txt`: `sqlite-vss==0.1.2` → `sqlite-vec==0.1.6`
- `Dockerfile`: comment updated
- `README.md`: vectors.py description updated
- `ai-context.md`: system overview and vectors.py description updated
- `build-plan/project_ideas.md`: marked 5.1 as resolved

Archived build plans left unchanged (historical record).

## Files changed

| File | Change |
|---|---|
| `core/vectors.py` | Full rewrite: sqlite-vec, binary embeddings, `_enabled` flag |
| `core/context.py` | Added `rag_context` param to `build_system_prompt()` |
| `app.py` | Pre-flight RAG in `process_message()` and `_re_infer()` |
| `prompts/system.md` | Softened manual query_memory directives |
| `requirements.txt` | sqlite-vss → sqlite-vec |
| `Dockerfile` | Comment update |
| `README.md` | sqlite-vss → sqlite-vec reference |
| `ai-context.md` | sqlite-vss → sqlite-vec references, removed env flag mention |
| `build-plan/project_ideas.md` | Marked 5.1 as resolved |

## How to verify

1. **Vectors unavailable**: Remove `sqlite-vec` from env. Start the bot. Confirm no crash, bot responds normally, `## Relevant Memory` section is absent from prompt.
2. **Vectors enabled**: Ensure `brain/daily_notes/` has content. Start the bot. Wait 30s for indexer. Send a message referencing an older note. Check `brain/routing_trace.jsonl` — the system prompt should contain a `## Relevant Memory` section.
3. **Quality check**: Send "what did I say about deploys last week?" — the LLM should answer from injected context without calling `query_memory` as a tool.
4. **Config knob**: `grug_config.json` → `memory.rag_result_limit` (default 3) controls how many vector hits are injected.

## Important: existing memory.db is incompatible

The old `memory.db` (sqlite-vss format) uses different table schemas and embedding serialization. On first run with sqlite-vec, the existing `memory.db` will need to be deleted so the new tables can be created. The background indexer will re-index all markdown files within 30 seconds.

```bash
rm brain/memory.db  # safe — it's a cache, not truth
```

## What's next

- **Evaluate RAG quality**: Are 3 bullet-point-sized hits enough context? Is the distance threshold useful for filtering irrelevant results?
- **Phase 2 (Hub-and-Spoke)**: If RAG proves valuable, consider moving embeddings + vector search to the M2 via Ollama's `/api/embeddings` endpoint. See [hub_and_spoke_rag.md](hub_and_spoke_rag.md).
