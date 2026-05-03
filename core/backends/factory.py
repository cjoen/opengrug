"""Worker factory for Grug.

Creates typed workers from config.workers entries.
"""

from __future__ import annotations

from typing import Dict, Union
from core.interfaces import ChatWorker, EmbeddingWorker


class WorkerFactory:
    """Creates concrete workers from the config.workers dict."""

    @staticmethod
    def create_all(config) -> Dict[str, Union[ChatWorker, EmbeddingWorker]]:
        """Instantiate all workers declared in config.workers.

        Returns a dict keyed by tier name (e.g. 'local-fast', 'embedder').
        """
        pool = {}
        workers_ns = config.workers

        for tier_name in vars(workers_ns):
            worker_cfg = getattr(workers_ns, tier_name)
            provider = getattr(worker_cfg, "provider", "ollama")
            worker_type = getattr(worker_cfg, "type", "chat")

            if worker_type == "chat":
                pool[tier_name] = WorkerFactory._create_chat_worker(provider, worker_cfg)
            elif worker_type == "embedding":
                pool[tier_name] = WorkerFactory._create_embedding_worker(provider, worker_cfg)
            else:
                raise ValueError(f"Unknown worker type '{worker_type}' for tier '{tier_name}'")

        return pool

    @staticmethod
    def _create_chat_worker(provider: str, cfg) -> ChatWorker:
        if provider == "ollama":
            from core.backends.ollama import OllamaChatWorker
            return OllamaChatWorker(
                host=getattr(cfg, "ollama_host", "http://localhost:11434"),
                model=cfg.model,
                timeout=getattr(cfg, "ollama_timeout", 120),
                num_keep=getattr(cfg, "num_keep", 1024),
                concurrency=getattr(cfg, "concurrency", 1),
            )
        elif provider == "gemini":
            from core.backends.gemini import GeminiChatWorker
            return GeminiChatWorker(
                model=cfg.model,
                concurrency=getattr(cfg, "concurrency", 4),
            )
        raise ValueError(f"Unknown chat worker provider: {provider}")

    @staticmethod
    def _create_embedding_worker(provider: str, cfg) -> EmbeddingWorker:
        if provider == "ollama":
            from core.backends.ollama import OllamaEmbeddingWorker
            return OllamaEmbeddingWorker(
                host=getattr(cfg, "ollama_host", "http://localhost:11434"),
                model=cfg.model,
                timeout=getattr(cfg, "ollama_timeout", 120),
                concurrency=getattr(cfg, "concurrency", 4),
            )
        raise ValueError(f"Unknown embedding worker provider: {provider}")
