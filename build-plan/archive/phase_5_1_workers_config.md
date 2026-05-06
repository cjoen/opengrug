# Phase 5.1: Workers & Config Foundation ✅ Complete (2026-05-03)

## Objective
Replace the monolithic `config.llm` / single `LLMClient` architecture with a multi-worker system where each compute resource (chat LLM, cloud LLM, embedding model) is declared, typed, and independently managed.

## Design Patterns
- **Abstract Factory** — `WorkerFactory` creates concrete workers from config entries.
- **Dependency Inversion (DIP)** — All consumers depend on the `Worker` ABC, never on `OllamaClient` directly.
- **Strategy Pattern** — Worker `type` (`chat` vs `embedding`) determines the interface contract.
- **Single Responsibility (SRP)** — Config parsing, validation, and worker construction are separate concerns.

## Changes

### [MODIFY] `grug_config.json`
Replace the top-level `"llm"` block with `"workers"` dictionary. Each entry has: `provider`, `model`, `type` (`chat`|`embedding`), `context_window` (chat only), `concurrency`. Add `"dispatcher"` block with `worker_tier` reference.

### [MODIFY] `core/interfaces.py`
Split `LLMClient` into two ABCs:
- `ChatWorker(ABC)` — `chat()`, `generate()`, `health_check()`. Adds a `concurrency` semaphore property.
- `EmbeddingWorker(ABC)` — `embed(text) -> list[float]`, `health_check()`. Own concurrency semaphore.

Keep `LLMResponse` dataclass unchanged.

### [MODIFY] `core/backends/ollama.py`
- `OllamaClient` → `OllamaChatWorker(ChatWorker)`. Wraps existing `chat()`, `generate()`, `health_check()`. Adds `threading.Semaphore` initialized from config `concurrency`.
- `OllamaEmbeddingWorker(EmbeddingWorker)` — new class. Moves `get_embedding()` logic here, exposes as `embed()`. Own semaphore.

### [NEW] `core/backends/gemini.py`
Stub `GeminiChatWorker(ChatWorker)` — placeholder for cloud failover. `chat()` and `generate()` call the Gemini API. `health_check()` validates API key. Not required for MVP launch but the interface must exist.

### [MODIFY] `core/backends/factory.py`
Replace `create_llm_client(config)` with `WorkerFactory`:
```python
class WorkerFactory:
    @staticmethod
    def create_all(config) -> dict[str, ChatWorker | EmbeddingWorker]:
        """Instantiate all workers from config.workers dict."""
```
Returns a `worker_pool: dict[str, Worker]` keyed by tier name.

### [MODIFY] `core/config.py`
- Remove `_DEFAULTS["llm"]` block entirely.
- Add `_DEFAULTS["workers"]` with a single `local-fast` Ollama entry and an `embedder` entry.
- Add `_DEFAULTS["dispatcher"]` with `worker_tier: "local-fast"`.
- Validation: fail fast if an agent references a `worker_tier` not in `workers`, or a `rag_source` references an `embedding_worker` not in `workers`.

### [MODIFY] `app.py`
- Replace `llm_client = create_llm_client(config)` with `worker_pool = WorkerFactory.create_all(config)`.
- Replace all `llm_client` references with the appropriate worker from the pool.
- `VectorMemory` receives an `EmbeddingWorker` instead of `llm_client` + `embedding_model`.
- `Summarizer` receives a `ChatWorker` (for semaphore coordination — §8 of PRD).

### [MODIFY] `core/vectors.py`
- Constructor changes: `VectorMemory(embedding_worker, db_path)` instead of `VectorMemory(llm_client, embedding_model, db_path)`.
- Replace `self.llm_client.get_embedding(text, self.embedding_model)` → `self.embedding_worker.embed(text)`.

### [MODIFY] `core/summarizer.py`
- Constructor changes: receives `ChatWorker` instead of raw `llm_client`.
- LLM calls acquire the worker's semaphore (ensuring summarization threads don't contend with chat inference on single-GPU setups).

## Verification
1. `pytest tests/` — all existing tests pass with updated mocks.
2. New `tests/test_workers.py` — verify `WorkerFactory` creates correct types, semaphore limits concurrent calls, health_check returns status strings.
3. Manual: boot the app, send a Slack message, confirm chat + embedding still work.
