"""Configuration loader for Grug.

Reads grug_config.json and exposes settings via dot notation.
Falls back to built-in defaults if the file is missing.
"""

import os
import json
from typing import Optional
from types import SimpleNamespace


_DEFAULTS = {
    "llm": {
        "model_name": "gemma:2b",
        "max_context_tokens": 8192,
        "target_context_tokens": 2048,
        "temperature": 0.1,
        "default_compression": "FULL",
        "ollama_timeout": 120,
    },
    "memory": {
        "summary_days_limit": 7,
        "summary_token_budget": 300,
        "summarization_threshold_bytes": 100,
        "thread_history_limit": 10,
        "thread_idle_timeout_hours": 4,
        "idle_sweep_interval_minutes": 15,
        "capped_tail_lines": 100,
        "rag_result_limit": 3,
    },
    "storage": {
        "base_dir": "./brain",
        "session_ttl_days": 30,
    },
}


def _deep_merge(defaults: dict, overrides: dict) -> dict:
    """Recursively merge overrides into defaults."""
    merged = defaults.copy()
    for key, value in overrides.items():
        if key in merged and isinstance(merged[key], dict) and isinstance(value, dict):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def _dict_to_namespace(d: dict) -> SimpleNamespace:
    """Convert a nested dict to nested SimpleNamespace for dot-notation access."""
    ns = SimpleNamespace()
    for key, value in d.items():
        if isinstance(value, dict):
            setattr(ns, key, _dict_to_namespace(value))
        else:
            setattr(ns, key, value)
    return ns


class GrugConfig:
    """Loads grug_config.json with defaults for every key.

    - Constructor accepts an optional ``config_path`` argument (default: auto-detect).
    - Auto-detection: check ``./grug_config.json``, then ``/app/grug_config.json`` (Docker).
    - If the file doesn't exist, use built-in defaults silently (no crash).
    - Expose attributes via dot notation: ``config.llm.model_name``, etc.
    - The ``storage.base_dir`` respects the ``DOCKER`` env var: if set, override to ``/app/brain``.
    """

    def __init__(self, config_path: Optional[str] = None):
        raw = _DEFAULTS.copy()

        # Resolve config file path
        if config_path is None:
            for candidate in ("./grug_config.json", "/app/grug_config.json"):
                if os.path.isfile(candidate):
                    config_path = candidate
                    break

        # Load and merge overrides from file
        if config_path and os.path.isfile(config_path):
            try:
                with open(config_path, "r", encoding="utf-8") as f:
                    file_data = json.load(f)
                raw = _deep_merge(raw, file_data)
            except (json.JSONDecodeError, OSError):
                pass  # Malformed or unreadable file — use defaults silently

        # Docker override: force base_dir to /app/brain when running in container
        if os.environ.get("DOCKER"):
            raw.setdefault("storage", {})["base_dir"] = "/app/brain"

        # Build nested namespace for dot-notation access
        ns = _dict_to_namespace(raw)
        self.llm = ns.llm
        self.memory = ns.memory
        self.storage = ns.storage


# Module-level singleton — import as: from core.config import config
config = GrugConfig()
