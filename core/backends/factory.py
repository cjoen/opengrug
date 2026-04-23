"""Factory for creating LLM clients from config."""

from core.interfaces import LLMClient


def create_llm_client(config) -> LLMClient:
    """Return the configured LLMClient backend.

    Reads config.llm.backend (default: 'ollama') and constructs the
    appropriate client. New backends are added as elif branches.
    """
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
