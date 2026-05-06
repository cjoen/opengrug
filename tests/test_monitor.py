"""Tests for health monitor: report generation, dashboard writing, alerting."""

import os
from types import SimpleNamespace

from core.dlq import DeadLetterQueue
from core.task import Task, TaskPriority
from core.task_queue import TaskQueue
from workers.health import WorkerHealth
from workers.monitor import collect_report, write_dashboard, _maybe_alert


class _FakeWorker:
    def __init__(self, model_name, health_msg):
        self.model_name = model_name
        self._msg = health_msg

    def health_check(self):
        return self._msg


def _make_queue():
    return TaskQueue(process_fn=lambda b: None, worker_count=0)


def test_collect_report_healthy(tmp_path):
    pool = {
        "fast": _FakeWorker("llama3.2", "Ollama: reachable (12ms), llama3.2 loaded"),
        "embedder": _FakeWorker("nomic", "Ollama: reachable (3ms), nomic loaded"),
    }
    dlq = DeadLetterQueue(str(tmp_path / "f.md"))
    q = _make_queue()
    rpt = collect_report(pool, q, dlq)
    assert all(w["healthy"] for w in rpt["workers"].values())
    assert rpt["queue_depth"] == 0
    assert rpt["dlq_size"] == 0


def test_collect_report_detects_degraded(tmp_path):
    pool = {"fast": _FakeWorker("llama3.2", "Ollama: unreachable at http://x")}
    dlq = DeadLetterQueue(str(tmp_path / "f.md"))
    rpt = collect_report(pool, _make_queue(), dlq)
    assert rpt["workers"]["fast"]["healthy"] is False


def test_collect_report_includes_dlq_and_queue_size(tmp_path):
    dlq = DeadLetterQueue(str(tmp_path / "f.md"))
    t = Task(session_id="s", user_id="u", agent_name="x", context="c",
             priority=TaskPriority.URGENT)
    dlq.add(t, error="x")

    q = _make_queue()
    q.enqueue(Task(session_id="s2", user_id="u", agent_name="x", context="c",
                   priority=TaskPriority.URGENT))
    rpt = collect_report({}, q, dlq)
    assert rpt["dlq_size"] == 1
    assert rpt["queue_depth"] == 1


def test_write_dashboard_includes_marker(tmp_path):
    path = str(tmp_path / "system_health.md")
    rpt = {
        "timestamp": "2026-05-06T10:00:00",
        "workers": {
            "fast": {"model": "llama", "status": "reachable", "healthy": True},
            "slow": {"model": "qwen", "status": "unreachable", "healthy": False},
        },
        "queue_depth": 3,
        "dlq_size": 2,
    }
    write_dashboard(path, rpt)
    text = open(path).read()
    assert "OK" in text
    assert "DEGRADED" in text
    assert "Pending tasks: 3" in text
    assert "Dead-letter queue: 2" in text


def test_maybe_alert_only_on_transitions():
    alerts: list[str] = []
    last: dict[str, str] = {}
    rpt_ok = {
        "workers": {"fast": {"status": "ok", "healthy": True}},
        "dlq_size": 0,
    }
    rpt_bad = {
        "workers": {"fast": {"status": "unreachable", "healthy": False}},
        "dlq_size": 0,
    }
    # First call: no previous state, no alerts
    _maybe_alert(rpt_ok, last, alerts.append, dlq_threshold=5)
    assert alerts == []
    # Transition to degraded fires an alert
    _maybe_alert(rpt_bad, last, alerts.append, dlq_threshold=5)
    assert any("degraded" in a for a in alerts)
    # Staying degraded: no new alert
    n = len(alerts)
    _maybe_alert(rpt_bad, last, alerts.append, dlq_threshold=5)
    assert len(alerts) == n
    # Recovery alert
    _maybe_alert(rpt_ok, last, alerts.append, dlq_threshold=5)
    assert any("recovered" in a for a in alerts)


class _StructuredWorker:
    def __init__(self, model_name, health):
        self.model_name = model_name
        self._h = health

    def health_check(self):
        return self._h


def test_collect_report_uses_structured_health(tmp_path):
    """A worker returning WorkerHealth(False, "anything") is DEGRADED regardless
    of whether the message string contains a heuristic keyword."""
    pool = {"fast": _StructuredWorker(
        "llama", WorkerHealth(healthy=False, status="circuit open"))}
    dlq = DeadLetterQueue(str(tmp_path / "f.md"))
    rpt = collect_report(pool, _make_queue(), dlq)
    assert rpt["workers"]["fast"]["healthy"] is False
    assert rpt["workers"]["fast"]["status"] == "circuit open"


def test_collect_report_legacy_string_still_works(tmp_path):
    pool = {"fast": _FakeWorker("llama", "Ollama reachable, model ok")}
    dlq = DeadLetterQueue(str(tmp_path / "f.md"))
    rpt = collect_report(pool, _make_queue(), dlq)
    assert rpt["workers"]["fast"]["healthy"] is True


def test_maybe_alert_dlq_threshold():
    alerts: list[str] = []
    last: dict[str, str] = {}
    base = {"workers": {}, "dlq_size": 0}
    over = {"workers": {}, "dlq_size": 10}
    _maybe_alert(base, last, alerts.append, dlq_threshold=5)
    _maybe_alert(over, last, alerts.append, dlq_threshold=5)
    assert any("exceeds threshold" in a for a in alerts)
