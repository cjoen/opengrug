from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import List, Dict, Optional


@dataclass
class LLMResponse:
    content: str
    tool_calls: List[Dict]


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
    def chat(self, system_prompt: str, messages: List[Dict], tools: Optional[List[Dict]] = None) -> LLMResponse:
        """
        Send a chat completion request to the LLM.

        Args:
            system_prompt: High-level instructions for the agent.
            messages: Array of conversation turns (role/content).
            tools: Optional array of JSON schemas defining available tools.

        Returns:
            An LLMResponse containing the raw text response and any invoked tools.
        """
        pass

    @abstractmethod
    def generate(self, prompt: str) -> str:
        """Plain-text generation. Returns generated text or '' on error."""
        pass

    def get_embedding(self, text: str, model: str) -> List[float]:
        """Return an embedding vector for the given text. Raises NotImplementedError if unsupported."""
        raise NotImplementedError(f"{self.__class__.__name__} does not support embeddings")

    def health_check(self) -> str:
        """Optional override for backend-specific health info."""
        return f"{self.backend_name}: {self.model_name} (no detailed health available)"
