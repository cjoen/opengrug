"""Tests for the worker system: WorkerFactory, ABCs, semaphores."""

import threading
import tempfile
import os
import pytest
from types import SimpleNamespace
from core.interfaces import ChatWorker, EmbeddingWorker, LLMResponse
from core.backends.factory import WorkerFactory


# ---------------------------------------------------------------------------
# WorkerFactory
# ---------------------------------------------------------------------------

def _make_config(workers_dict, dispatcher_tier="local-fast"):
    """Build a minimal config namespace from a plain dict."""
    workers_ns = SimpleNamespace()
    for name, attrs in workers_dict.items():
        setattr(workers_ns, name, SimpleNamespace(**attrs))
    return SimpleNamespace(
        workers=workers_ns,
        dispatcher=SimpleNamespace(worker_tier=dispatcher_tier),
    )


def test_factory_creates_chat_worker():
    cfg = _make_config({
        "local-fast": {
            "provider": "ollama",
            "model": "gemma:test",
            "type": "chat",
            "ollama_host": "http://localhost:11434",
            "ollama_timeout": 30,
            "num_keep": 512,
            "concurrency": 1,
        }
    })
    pool = WorkerFactory.create_all(cfg)
    assert "local-fast" in pool
    worker = pool["local-fast"]
    assert isinstance(worker, ChatWorker)
    assert worker.model_name == "gemma:test"


def test_factory_creates_embedding_worker():
    cfg = _make_config({
        "local-fast": {
            "provider": "ollama", "model": "gemma:test", "type": "chat",
            "ollama_host": "http://localhost:11434", "ollama_timeout": 30,
            "concurrency": 1,
        },
        "embedder": {
            "provider": "ollama",
            "model": "nomic-embed-text",
            "type": "embedding",
            "ollama_host": "http://localhost:11434",
            "ollama_timeout": 30,
            "concurrency": 4,
        },
    })
    pool = WorkerFactory.create_all(cfg)
    assert "embedder" in pool
    worker = pool["embedder"]
    assert isinstance(worker, EmbeddingWorker)
    assert worker.model_name == "nomic-embed-text"


def test_factory_rejects_unknown_type():
    cfg = _make_config({
        "bad": {"provider": "ollama", "model": "x", "type": "unknown"},
    })
    with pytest.raises(ValueError, match="Unknown worker type"):
        WorkerFactory.create_all(cfg)


def test_factory_rejects_unknown_provider():
    cfg = _make_config({
        "bad": {"provider": "nonexistent", "model": "x", "type": "chat"},
    })
    with pytest.raises(ValueError, match="Unknown chat worker provider"):
        WorkerFactory.create_all(cfg)


# ---------------------------------------------------------------------------
# Semaphore behavior
# ---------------------------------------------------------------------------

def test_chat_worker_semaphore_limits_concurrency():
    """Verify the semaphore has the right initial value."""
    from core.backends.ollama import OllamaChatWorker
    worker = OllamaChatWorker(
        host="http://localhost:11434",
        model="test",
        timeout=30,
        concurrency=2,
    )
    # Semaphore starts with value 2
    assert worker.semaphore.acquire(blocking=False)
    assert worker.semaphore.acquire(blocking=False)
    # Third acquire should fail (non-blocking)
    assert not worker.semaphore.acquire(blocking=False)
    worker.semaphore.release()
    worker.semaphore.release()


def test_embedding_worker_semaphore_limits_concurrency():
    from core.backends.ollama import OllamaEmbeddingWorker
    worker = OllamaEmbeddingWorker(
        host="http://localhost:11434",
        model="test",
        timeout=30,
        concurrency=3,
    )
    for _ in range(3):
        assert worker.semaphore.acquire(blocking=False)
    assert not worker.semaphore.acquire(blocking=False)
    for _ in range(3):
        worker.semaphore.release()


# ---------------------------------------------------------------------------
# Health check (basic — doesn't require a running Ollama)
# ---------------------------------------------------------------------------

def test_chat_worker_health_check_unreachable():
    from core.backends.ollama import OllamaChatWorker
    worker = OllamaChatWorker(
        host="http://localhost:99999",
        model="test",
        timeout=1,
        concurrency=1,
    )
    result = worker.health_check()
    assert "unreachable" in result.lower() or "error" in result.lower()


def test_gemini_stub_health_check():
    from core.backends.gemini import GeminiChatWorker
    worker = GeminiChatWorker(model="gemini-1.5-pro")
    assert "not yet implemented" in worker.health_check().lower()


def test_gemini_stub_raises():
    from core.backends.gemini import GeminiChatWorker
    worker = GeminiChatWorker(model="gemini-1.5-pro")
    with pytest.raises(NotImplementedError):
        worker.chat("system", [])
    with pytest.raises(NotImplementedError):
        worker.generate("prompt")
