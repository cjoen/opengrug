"""Worker interfaces for Grug.

Defines the abstract contracts for chat and embedding workers.
All consumers depend on these ABCs, never on concrete backends.
"""

import threading
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import List, Dict, Optional


@dataclass
class LLMResponse:
    content: str
    tool_calls: List[Dict]


class ChatWorker(ABC):
    """Abstract chat/generation worker with concurrency control."""

    def __init__(self, concurrency: int = 1):
        self._semaphore = threading.Semaphore(concurrency)

    @property
    def semaphore(self) -> threading.Semaphore:
        return self._semaphore

    @property
    @abstractmethod
    def model_name(self) -> str:
        """Human-readable model identifier (e.g. 'gemma4:grug')."""

    @property
    @abstractmethod
    def backend_name(self) -> str:
        """Backend identifier for health/logging (e.g. 'ollama @ http://localhost:11434')."""

    @abstractmethod
    def chat(self, system_prompt: str, messages: List[Dict],
             tools: Optional[List[Dict]] = None) -> LLMResponse:
        """Send a chat completion request.

        Args:
            system_prompt: High-level instructions for the agent.
            messages: Array of conversation turns (role/content).
            tools: Optional array of JSON schemas defining available tools.

        Returns:
            An LLMResponse containing the raw text response and any invoked tools.
        """

    @abstractmethod
    def generate(self, prompt: str) -> str:
        """Plain-text generation. Returns generated text or '' on error."""

    def health_check(self) -> str:
        """Optional override for backend-specific health info."""
        return f"{self.backend_name}: {self.model_name} (no detailed health available)"


class EmbeddingWorker(ABC):
    """Abstract embedding worker with concurrency control."""

    def __init__(self, concurrency: int = 1):
        self._semaphore = threading.Semaphore(concurrency)

    @property
    def semaphore(self) -> threading.Semaphore:
        return self._semaphore

    @property
    @abstractmethod
    def model_name(self) -> str:
        """Human-readable model identifier (e.g. 'nomic-embed-text')."""

    @property
    @abstractmethod
    def backend_name(self) -> str:
        """Backend identifier for health/logging."""

    @abstractmethod
    def embed(self, text: str) -> List[float]:
        """Return an embedding vector for the given text."""

    def health_check(self) -> str:
        """Optional override for backend-specific health info."""
        return f"{self.backend_name}: {self.model_name} (no detailed health available)"
