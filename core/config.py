"""Configuration loader for Grug.

Reads grug_config.json and exposes settings via dot notation.
Falls back to built-in defaults if the file is missing.
"""

import os
import json
from typing import Optional
from types import SimpleNamespace


_DEFAULTS = {
    "workers": {
        "local-fast": {
            "provider": "ollama",
            "model": "gemma:e4b",
            "type": "chat",
            "context_window": 8192,
            "target_context_tokens": 2048,
            "temperature": 0.1,
            "ollama_host": "http://localhost:11434",
            "ollama_timeout": 120,
            "thinking_mode": False,
            "num_keep": 1024,
            "concurrency": 1,
        },
        "embedder": {
            "provider": "ollama",
            "model": "nomic-embed-text",
            "type": "embedding",
            "ollama_host": "http://localhost:11434",
            "ollama_timeout": 120,
            "concurrency": 4,
        },
    },
    "dispatcher": {
        "worker_tier": "local-fast",
    },
    "agents": {
        "chat_agent": {
            "required_worker_tier": "local-fast",
            "prompt": "prompts/agents/chat_agent.md",
            "tools": "all",
            "rag_sources": ["core_memory"],
        },
    },
    "rag_sources": {
        "core_memory": {
            "db_path": "memory.db",
            "embedding_worker": "embedder",
        },
    },
    "memory": {
        "summary_days_limit": 7,
        "summary_token_budget": 300,
        "summarization_threshold_bytes": 100,
        "thread_history_limit": 10,
        "thread_idle_timeout_hours": 168,
        "instructions_max_chars": 1500,
        "idle_sweep_interval_minutes": 15,
        "capped_tail_lines": 100,
        "rag_result_limit": 3,
        "notes_display_limit": 10,
        "search_result_limit": 5,
    },
    "storage": {
        "base_dir": "./brain",
        "knowledge_dir": "knowledge",
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
        # Off-hours window for BACKGROUND tasks. If the queue has only one
        # chat worker tier we hold BG work until the local hour falls in
        # [start_hour, end_hour) (wraps over midnight when start > end).
        # Set to null to run BG tasks anytime.
        "background_window": {
            "start_hour": 22,
            "end_hour": 6,
        },
    },
    "grug_tasks": {
        "file": "agent_tasks.md",
        "nightly_limit": 5,
        "results_channel": None,
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
            ollama_host = os.environ["OLLAMA_HOST"]
            for worker in raw.get("workers", {}).values():
                if worker.get("provider") == "ollama":
                    worker["ollama_host"] = ollama_host

        # Validate: dispatcher must reference a known worker tier
        workers_raw = raw.get("workers", {})
        dispatcher_tier = raw.get("dispatcher", {}).get("worker_tier")
        if dispatcher_tier and dispatcher_tier not in workers_raw:
            raise ValueError(
                f"dispatcher.worker_tier '{dispatcher_tier}' not found in workers: "
                f"{list(workers_raw.keys())}"
            )

        # Validate: rag_sources reference known embedding workers
        rag_raw = raw.get("rag_sources", {})
        for rs_name, rs_cfg in rag_raw.items():
            emb_name = rs_cfg.get("embedding_worker")
            emb_cfg = workers_raw.get(emb_name)
            if emb_cfg is None:
                raise ValueError(
                    f"rag_source '{rs_name}': embedding_worker '{emb_name}' not in workers"
                )
            if emb_cfg.get("type") != "embedding":
                raise ValueError(
                    f"rag_source '{rs_name}': worker '{emb_name}' is type "
                    f"'{emb_cfg.get('type')}', expected 'embedding'"
                )

        # Validate: each agent references a known chat worker tier and known rag sources
        for agent_name, agent_cfg in raw.get("agents", {}).items():
            tier = agent_cfg.get("required_worker_tier")
            tier_cfg = workers_raw.get(tier)
            if tier_cfg is None:
                raise ValueError(
                    f"agent '{agent_name}': required_worker_tier '{tier}' not in workers"
                )
            if tier_cfg.get("type") != "chat":
                raise ValueError(
                    f"agent '{agent_name}': worker '{tier}' is type "
                    f"'{tier_cfg.get('type')}', expected 'chat'"
                )
            for rs_name in agent_cfg.get("rag_sources", []):
                if rs_name not in rag_raw:
                    raise ValueError(
                        f"agent '{agent_name}': rag_source '{rs_name}' not declared"
                    )

        ns = _dict_to_namespace(raw)
        self.workers = ns.workers
        self.dispatcher = ns.dispatcher
        self.agents = ns.agents
        self.rag_sources = ns.rag_sources
        self.memory = ns.memory
        self.storage = ns.storage
        self.shortcuts = ns.shortcuts
        self.scheduler = ns.scheduler
        self.queue = ns.queue
        self.grug_tasks = ns.grug_tasks


config = GrugConfig()
