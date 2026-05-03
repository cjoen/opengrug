"""Tests for GrugConfig: defaults and file overrides."""

import os
import tempfile
from core.config import GrugConfig


def test_config_loader_defaults():
    cfg = GrugConfig(config_path="/nonexistent/path.json")
    # Workers defaults
    local_fast = getattr(cfg.workers, "local-fast")
    assert local_fast.model == "gemma:e4b"
    assert local_fast.type == "chat"
    assert local_fast.concurrency == 1
    embedder = cfg.workers.embedder
    assert embedder.model == "nomic-embed-text"
    assert embedder.type == "embedding"
    # Dispatcher default
    assert cfg.dispatcher.worker_tier == "local-fast"
    # Other defaults unchanged
    assert cfg.memory.thread_idle_timeout_hours == 168
    assert cfg.memory.capped_tail_lines == 100
    assert cfg.storage.session_ttl_days == 30
    assert cfg.scheduler.poll_interval_seconds == 60


def test_config_loader_file():
    tmp = tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False)
    tmp.write('{"workers": {"local-fast": {"model": "llama:7b"}}, "memory": {"capped_tail_lines": 50}}')
    tmp.close()
    cfg = GrugConfig(config_path=tmp.name)
    local_fast = getattr(cfg.workers, "local-fast")
    assert local_fast.model == "llama:7b"
    assert cfg.memory.capped_tail_lines == 50
    # Defaults still intact for unset fields
    assert local_fast.context_window == 8192
    assert cfg.memory.summary_days_limit == 7
    os.unlink(tmp.name)


def test_config_dispatcher_validation():
    """Dispatcher referencing a nonexistent worker tier should raise."""
    import pytest
    tmp = tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False)
    tmp.write('{"dispatcher": {"worker_tier": "nonexistent-tier"}}')
    tmp.close()
    with pytest.raises(ValueError, match="nonexistent-tier"):
        GrugConfig(config_path=tmp.name)
    os.unlink(tmp.name)
