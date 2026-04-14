"""Ollama LLM client for Grug.

Single place that knows the Ollama HTTP API.
All modules that need LLM calls receive an OllamaClient instance.
"""

import json
import requests


class OllamaClient:

    def __init__(self, host: str, model: str, timeout: int, num_keep: int = 1024):
        self.host = host.rstrip("/")
        self.model = model
        self.timeout = timeout
        self.num_keep = num_keep

    def chat(self, system_prompt: str, messages: list) -> str:
        """Multi-turn chat via /api/chat. Returns content string.

        Falls back to an ask_for_clarification JSON string on error.
        """
        url = f"{self.host}/api/chat"
        chat_messages = [{"role": "system", "content": system_prompt}] + messages
        payload = {
            "model": self.model,
            "messages": chat_messages,
            "format": "json",
            "stream": False,
            "options": {"num_keep": self.num_keep},
        }
        try:
            response = requests.post(url, json=payload, timeout=self.timeout)
            response.raise_for_status()
            return response.json().get("message", {}).get("content", "")
        except Exception as e:
            return json.dumps({
                "tool": "ask_for_clarification",
                "arguments": {"reason_for_confusion": f"Grug brain foggy. Ollama not responding: {e}"},
                "confidence_score": 0
            })

    def generate(self, prompt: str) -> str:
        """Plain-text generation via /api/generate. Returns text or '' on error."""
        url = f"{self.host}/api/generate"
        payload = {
            "model": self.model,
            "prompt": prompt,
            "stream": False,
        }
        try:
            response = requests.post(url, json=payload, timeout=self.timeout)
            response.raise_for_status()
            return response.json().get("response", "").strip()
        except Exception as e:
            print(f"[llm] generate failed: {e}")
            return ""
