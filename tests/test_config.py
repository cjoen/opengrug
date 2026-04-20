"""Tests for GrugConfig: defaults and file overrides."""

import os
import tempfile
from core.config import GrugConfig


def test_config_loader_defaults():
    cfg = GrugConfig(config_path="/nonexistent/path.json")
    assert cfg.llm.model_name == "gemma:e4b"
    assert cfg.memory.thread_idle_timeout_hours == 4
    assert cfg.memory.capped_tail_lines == 100
    assert cfg.storage.session_ttl_days == 30
    assert cfg.scheduler.poll_interval_seconds == 60


def test_config_loader_file():
    tmp = tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False)
    tmp.write('{"llm": {"model_name": "llama:7b"}, "memory": {"capped_tail_lines": 50}}')
    tmp.close()
    cfg = GrugConfig(config_path=tmp.name)
    assert cfg.llm.model_name == "llama:7b"
    assert cfg.memory.capped_tail_lines == 50
    assert cfg.llm.max_context_tokens == 8192
    assert cfg.memory.summary_days_limit == 7
    os.unlink(tmp.name)
