from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import List, Dict, Optional


@dataclass
class LLMResponse:
    content: str
    tool_calls: List[Dict]


class LLMClient(ABC):
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
