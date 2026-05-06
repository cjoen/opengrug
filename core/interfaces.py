"""Worker interfaces for Grug.

Defines the abstract contracts for chat and embedding workers.
All consumers depend on these ABCs, never on concrete backends.
"""

import threading
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import List, Dict, Optional, Union

from workers.health import WorkerHealth


@dataclass
class LLMResponse:
    content: str
    tool_calls: List[Dict]


class ChatWorker(ABC):
    """Abstract chat/generation worker with concurrency control.

    ``chat()`` is a template method that wraps ``_chat_impl()`` with
    consecutive-failure tracking. After ``failure_threshold`` raises in a row,
    ``health_check()`` reports the breaker as open until the next success.
    Subclasses implement ``_chat_impl`` and ``_probe`` instead of overriding
    ``chat`` / ``health_check`` directly.
    """

    failure_threshold: int = 3

    def __init__(self, concurrency: int = 1):
        self._semaphore = threading.Semaphore(concurrency)
        self._consecutive_failures = 0

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
    def _chat_impl(self, system_prompt: str, messages: List[Dict],
                   tools: Optional[List[Dict]] = None) -> LLMResponse:
        """Backend-specific chat call. Raise on transient/connection errors so
        the breaker can count them; return an LLMResponse on success."""

    def chat(self, system_prompt: str, messages: List[Dict],
             tools: Optional[List[Dict]] = None) -> LLMResponse:
        try:
            result = self._chat_impl(system_prompt, messages, tools)
        except Exception:
            self._consecutive_failures += 1
            raise
        self._consecutive_failures = 0
        return result

    @abstractmethod
    def generate(self, prompt: str) -> str:
        """Plain-text generation. Returns generated text or '' on error."""

    def _probe(self) -> Union[WorkerHealth, str]:
        """Backend-specific health probe. Default: a generic OK string."""
        return f"{self.backend_name}: {self.model_name} (no detailed health available)"

    def health_check(self) -> WorkerHealth:
        if self._consecutive_failures >= self.failure_threshold:
            return WorkerHealth(
                healthy=False,
                status=f"circuit open after {self._consecutive_failures} consecutive failures",
            )
        result = self._probe()
        if isinstance(result, WorkerHealth):
            return result
        # Subclass returned a legacy string. Apply the same heuristic the
        # monitor uses so we keep a single source of truth for "bad".
        msg = str(result or "")
        bad = any(s in msg.lower() for s in ("unreachable", "not found", "timed out", "timeout", "error"))
        return WorkerHealth(healthy=not bad, status=msg)


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
