"""Ollama backend workers for Grug.

Provides OllamaChatWorker and OllamaEmbeddingWorker — the concrete
implementations of the ChatWorker and EmbeddingWorker ABCs for Ollama.
"""

import time
import requests
import re
from core.interfaces import ChatWorker, EmbeddingWorker, LLMResponse
from workers.health import WorkerHealth


class OllamaChatWorker(ChatWorker):
    """Chat and generation worker backed by Ollama's HTTP API."""

    def __init__(self, host: str, model: str, timeout: int,
                 num_keep: int = 1024, concurrency: int = 1):
        super().__init__(concurrency=concurrency)
        self.host = host.rstrip("/")
        self.model = model
        self.timeout = timeout
        self.num_keep = num_keep

    @property
    def model_name(self) -> str:
        return self.model

    @property
    def backend_name(self) -> str:
        return f"ollama @ {self.host}"

    def _chat_impl(self, system_prompt: str, messages: list, tools: list = None) -> LLMResponse:
        """Multi-turn chat via /api/chat. Returns LLMResponse."""
        url = f"{self.host}/api/chat"
        chat_messages = [{"role": "system", "content": system_prompt}] + messages
        payload = {
            "model": self.model,
            "messages": chat_messages,
            "stream": False,
            "options": {"num_keep": self.num_keep},
        }
        if tools:
            payload["tools"] = tools

        def _error_response(msg: str) -> LLMResponse:
            return LLMResponse(
                content="",
                tool_calls=[{"tool": "reply_to_user", "arguments": {"message": msg}}]
            )

        try:
            response = requests.post(url, json=payload, timeout=self.timeout)
            response.raise_for_status()
            data = response.json()
            message = data.get("message", {})

            content = message.get("content", "")
            content = re.sub(r"<\|channel>.*?<channel\|>", "", content, flags=re.DOTALL).strip()

            parsed_calls = []
            for tc in message.get("tool_calls", []):
                fn = tc.get("function", {})
                if fn.get("name"):
                    parsed_calls.append({
                        "tool": fn.get("name"),
                        "arguments": fn.get("arguments", {})
                    })

            if not parsed_calls and content:
                parsed_calls.append({
                    "tool": "reply_to_user",
                    "arguments": {"message": content}
                })

            return LLMResponse(content=content, tool_calls=parsed_calls)

        except requests.exceptions.Timeout:
            return _error_response("Grug brain slow today. LLM took too long to think — try again in a moment.")
        except requests.exceptions.ConnectionError:
            return _error_response("Grug can't reach brain. LLM server appears to be offline.")
        except Exception:
            return _error_response("Grug brain foggy. Something went wrong — try again.")

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
            print(f"[worker] generate failed: {e}")
            return ""

    def _probe(self) -> WorkerHealth:
        """Ollama-specific connectivity and model availability check."""
        try:
            start = time.time()
            resp = requests.get(f"{self.host}/api/tags", timeout=5)
            elapsed_ms = int((time.time() - start) * 1000)
            resp.raise_for_status()
            models = resp.json().get("models", [])
            model_names = [m.get("name", "") for m in models]
            if any(self.model in name for name in model_names):
                return WorkerHealth(True, f"Ollama: reachable ({elapsed_ms}ms), {self.model} loaded")
            return WorkerHealth(
                False,
                f"Ollama: reachable ({elapsed_ms}ms), {self.model} NOT found. "
                f"Available: {', '.join(model_names)}",
            )
        except requests.exceptions.ConnectionError:
            return WorkerHealth(False, f"Ollama: unreachable at {self.host}")
        except requests.exceptions.Timeout:
            return WorkerHealth(False, f"Ollama: timeout at {self.host}")
        except Exception as e:
            return WorkerHealth(False, f"Ollama: error ({e})")


class OllamaEmbeddingWorker(EmbeddingWorker):
    """Embedding worker backed by Ollama's HTTP API."""

    def __init__(self, host: str, model: str, timeout: int, concurrency: int = 4):
        super().__init__(concurrency=concurrency)
        self.host = host.rstrip("/")
        self.model = model
        self.timeout = timeout

    @property
    def model_name(self) -> str:
        return self.model

    @property
    def backend_name(self) -> str:
        return f"ollama @ {self.host}"

    def embed(self, text: str) -> list[float]:
        """Return an embedding vector via Ollama. Tries /api/embed first, falls back to /api/embeddings."""
        try:
            response = requests.post(
                f"{self.host}/api/embed",
                json={"model": self.model, "input": text},
                timeout=self.timeout,
            )
            response.raise_for_status()
            data = response.json()
            embeddings = data.get("embeddings")
            if embeddings and len(embeddings) > 0:
                return embeddings[0]
        except Exception:
            pass

        # Fallback to legacy endpoint
        try:
            response = requests.post(
                f"{self.host}/api/embeddings",
                json={"model": self.model, "prompt": text},
                timeout=self.timeout,
            )
            response.raise_for_status()
            return response.json().get("embedding", [])
        except Exception as e:
            print(f"[worker] embed failed: {e}")
            return []

    def health_check(self) -> str:
        """Ollama embedding health check."""
        try:
            start = time.time()
            resp = requests.get(f"{self.host}/api/tags", timeout=5)
            elapsed_ms = int((time.time() - start) * 1000)
            resp.raise_for_status()
            models = resp.json().get("models", [])
            model_names = [m.get("name", "") for m in models]
            if any(self.model in name for name in model_names):
                return f"Ollama embedder: reachable ({elapsed_ms}ms), {self.model} loaded"
            else:
                return f"Ollama embedder: reachable ({elapsed_ms}ms), {self.model} NOT found. Available: {', '.join(model_names)}"
        except requests.exceptions.ConnectionError:
            return f"Ollama embedder: unreachable at {self.host}"
        except requests.exceptions.Timeout:
            return f"Ollama embedder: timeout at {self.host}"
        except Exception as e:
            return f"Ollama embedder: error ({e})"
