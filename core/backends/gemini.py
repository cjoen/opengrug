"""Gemini backend worker for Grug.

Stub for future cloud failover. The interface exists so WorkerFactory
can instantiate it from config, but all methods raise NotImplementedError
until the Gemini API integration is built.
"""

from core.interfaces import ChatWorker, LLMResponse
from workers.health import WorkerHealth


class GeminiChatWorker(ChatWorker):
    """Placeholder chat worker for Google Gemini API."""

    def __init__(self, model: str, concurrency: int = 4):
        super().__init__(concurrency=concurrency)
        self.model = model

    @property
    def model_name(self) -> str:
        return self.model

    @property
    def backend_name(self) -> str:
        return "gemini"

    def _chat_impl(self, system_prompt: str, messages: list, tools: list = None) -> LLMResponse:
        raise NotImplementedError("GeminiChatWorker is not yet implemented")

    def generate(self, prompt: str) -> str:
        raise NotImplementedError("GeminiChatWorker is not yet implemented")

    def _probe(self) -> WorkerHealth:
        return WorkerHealth(False, "Gemini: not yet implemented")
