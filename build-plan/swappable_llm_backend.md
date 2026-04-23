# Swappable LLM Backend — Implementation Plan

## Context

The system currently hardcodes `OllamaClient` as the only LLM backend. An `LLMClient` ABC already exists in `core/interfaces.py` with a `chat()` method, and `OllamaClient` implements it. However, `generate()` (used by Summarizer, Router, and notes) is not on the interface, and `health.py` reaches directly into Ollama-specific attributes (`llm_client.host`, `llm_client.model`). The goal is to make the backend config-driven so we can swap between Ollama, Anthropic, Google, etc. without touching any consumer code.

## Design Principles

- **Interface Segregation**: All consumers depend on `LLMClient`, never a concrete class
- **Single Responsibility**: Each backend file owns only its vendor's HTTP details
- **Open/Closed**: Adding a new backend = one new file + one config value. Zero changes to consumers.
- **Factory pattern**: A single factory function reads config and returns the right `LLMClient`

## Changes

### 1. Expand `LLMClient` interface — `core/interfaces.py`

Add `generate()` as an abstract method alongside `chat()`. Also add `model_name` and `backend_name` as abstract properties for health reporting (replaces direct `.host`/`.model` access).

```python
class LLMClient(ABC):
    @property
    @abstractmethod
    def model_name(self) -> str:
        """Human-readable model identifier (e.g. 'gemma4:grug')."""

    @property
    @abstractmethod
    def backend_name(self) -> str:
        """Backend identifier for health/logging (e.g. 'ollama @ http://localhost:11434')."""

    @abstractmethod
    def chat(self, system_prompt, messages, tools=None) -> LLMResponse: ...

    @abstractmethod
    def generate(self, prompt: str) -> str: ...

    def health_check(self) -> str:
        """Optional override for backend-specific health info. Default: basic status."""
        return f"{self.backend_name}: {self.model_name} (no detailed health available)"
```

### 2. Refactor `OllamaClient` — `core/llm.py` -> `core/backends/ollama.py`

Move the existing `OllamaClient` into a `core/backends/` package. Add the new properties and `health_check()`. No behavior changes — just reorganization.

- Add `model_name` property -> returns `self.model`
- Add `backend_name` property -> returns `f"ollama @ {self.host}"`
- Add `health_check()` -> moves the Ollama-specific connectivity check from `tools/health.py` into the client where it belongs

Files:
- Create `core/backends/__init__.py` (empty)
- Create `core/backends/ollama.py` (move from `core/llm.py`)
- Keep `core/llm.py` as a re-export shim for backwards compatibility: `from core.backends.ollama import OllamaClient`

### 3. Add factory function — `core/backends/factory.py`

```python
def create_llm_client(config) -> LLMClient:
    backend = getattr(config.llm, "backend", "ollama")
    if backend == "ollama":
        from core.backends.ollama import OllamaClient
        return OllamaClient(
            host=config.llm.ollama_host,
            model=config.llm.model_name,
            timeout=config.llm.ollama_timeout,
            num_keep=getattr(config.llm, "num_keep", 1024),
        )
    raise ValueError(f"Unknown LLM backend: {backend}")
```

New backends get added here as `elif` branches — one import + one constructor call each.

### 4. Update config — `core/config.py`

Add `"backend": "ollama"` to the `_DEFAULTS["llm"]` dict. That's it — existing configs without the key get the default.

### 5. Update `app.py` — use factory

Replace:
```python
from core.llm import OllamaClient
llm_client = OllamaClient(host=..., model=..., ...)
```
With:
```python
from core.backends.factory import create_llm_client
llm_client = create_llm_client(config)
```

### 6. Update `evals/run_evals.py` — use factory

Same pattern as app.py. The eval harness should test whichever backend is configured.

### 7. Fix `tools/health.py` — use interface properties

Replace direct attribute access:
```python
# Before
lines.append(f"LLM: {llm_client.model} @ {llm_client.host}")
resp = requests.get(f"{llm_client.host}/api/tags", ...)

# After  
lines.append(f"LLM: {llm_client.model_name} ({llm_client.backend_name})")
# system_health delegates to:
lines.append(llm_client.health_check())
```

This removes all Ollama-specific logic from health.py and pushes it into `OllamaClient.health_check()`.

## Files to modify (in order)

| # | File | Change |
|---|------|--------|
| 1 | `core/interfaces.py` | Add `generate()`, `model_name`, `backend_name`, `health_check()` to ABC |
| 2 | `core/backends/__init__.py` | Create (empty) |
| 3 | `core/backends/ollama.py` | Move `OllamaClient` here, add properties + `health_check()` |
| 4 | `core/llm.py` | Replace with re-export shim |
| 5 | `core/backends/factory.py` | Create factory function |
| 6 | `core/config.py` | Add `"backend": "ollama"` default |
| 7 | `app.py` | Use factory instead of direct `OllamaClient` |
| 8 | `evals/run_evals.py` | Use factory instead of direct `OllamaClient` |
| 9 | `tools/health.py` | Use interface properties, delegate to `health_check()` |

## What we are NOT doing

- Not adding Anthropic/Google backends yet — that's a separate PR per backend
- Not changing the tool schema format (already OpenAI-compatible)
- Not changing `LLMResponse` or `ToolExecutionResult` data classes
- Not touching router, orchestrator, summarizer, or any tool files (except health)

## Verification

1. `python3 -m pytest tests/` — existing tests pass
2. `python3 evals/run_evals.py` — evals still run against Ollama
3. Config without `"backend"` key works (defaults to `"ollama"`)
4. Config with `"backend": "ollama"` works identically
5. Config with `"backend": "invalid"` raises clear `ValueError`
6. Health tools report via interface properties, no Ollama-specific coupling
