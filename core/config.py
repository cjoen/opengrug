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
        "model_name": "gemma:e4b",
        "ollama_host": "http://localhost:11434",
        "max_context_tokens": 8192,
        "target_context_tokens": 2048,
        "temperature": 0.1,
        "default_compression": "FULL",
        "ollama_timeout": 120,
        "low_confidence_threshold": 4,
        "thinking_mode": False,
        "num_keep": 1024,
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
        "notes_display_limit": 10,
        "search_result_limit": 5,
    },
    "storage": {
        "base_dir": "./brain",
        "session_ttl_days": 30,
        "subprocess_timeout": 30,
    },
    "shortcuts": {
        "prefix": "/",
        "aliases": {
            "note": "add_note",
            "task": "add_task",
        },
    },
    "scheduler": {
        "poll_interval_seconds": 60,
        "db_file": "schedules.db",
        "timezone": "UTC",
    },
    "queue": {
        "worker_count": 1,
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
    """Loads grug_config.json with defaults for every key."""

    def __init__(self, config_path: Optional[str] = None):
        raw = _DEFAULTS.copy()

        if config_path is None:
            for candidate in ("./grug_config.json", "/app/grug_config.json"):
                if os.path.isfile(candidate):
                    config_path = candidate
                    break

        if config_path and os.path.isfile(config_path):
            try:
                with open(config_path, "r", encoding="utf-8") as f:
                    file_data = json.load(f)
                raw = _deep_merge(raw, file_data)
            except (json.JSONDecodeError, OSError):
                pass

        # Docker overrides
        if os.environ.get("DOCKER"):
            raw.setdefault("storage", {})["base_dir"] = "/app/brain"
        if os.environ.get("OLLAMA_HOST"):
            raw.setdefault("llm", {})["ollama_host"] = os.environ["OLLAMA_HOST"]

        ns = _dict_to_namespace(raw)
        self.llm = ns.llm
        self.memory = ns.memory
        self.storage = ns.storage
        self.shortcuts = ns.shortcuts
        self.scheduler = ns.scheduler
        self.queue = ns.queue


config = GrugConfig()
