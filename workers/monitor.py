"""Health monitor — observes worker_pool, queue, and DLQ state.

A plain Python thread (not an LLM Agent). Polls each worker's health_check(),
queue depth, and DLQ size; writes an Obsidian-friendly dashboard to
``brain/system_health.md``; calls alert_callback when state degrades.

The monitor never imports Slack — alerting is delegated to a callback so the
adapter wires it to the appropriate channel.
"""

from __future__ import annotations

import os
import time
from datetime import datetime
from typing import Callable, Optional

from workers.health import WorkerHealth


def health_monitor_loop(worker_pool, task_queue, dlq, alert_callback,
                        config, dashboard_path: Optional[str] = None,
                        poll_interval_seconds: float = 60.0,
                        dlq_alert_threshold: int = 5):
    """Periodic health check loop. Run as a daemon thread."""
    base_dir = config.storage.base_dir
    if dashboard_path is None:
        dashboard_path = os.path.join(base_dir, "system_health.md")

    last_alerts: dict[str, str] = {}

    while True:
        try:
            report = collect_report(worker_pool, task_queue, dlq)
            write_dashboard(dashboard_path, report)
            _maybe_alert(report, last_alerts, alert_callback, dlq_alert_threshold)
        except Exception as e:
            print(f"[monitor] error during health check: {e}")
        time.sleep(poll_interval_seconds)


def collect_report(worker_pool, task_queue, dlq) -> dict:
    """Snapshot worker / queue / DLQ state. Pure function, no I/O."""
    workers: dict[str, dict] = {}
    for name, worker in (worker_pool or {}).items():
        status, healthy = _check_worker(worker)
        workers[name] = {
            "model": getattr(worker, "model_name", "?"),
            "status": status,
            "healthy": healthy,
        }
    return {
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "workers": workers,
        "queue_depth": task_queue.pending_count() if task_queue else 0,
        "dlq_size": dlq.size() if dlq else 0,
    }


def _check_worker(worker) -> tuple[str, bool]:
    fn = getattr(worker, "health_check", None)
    if fn is None:
        return "no health_check available", True
    try:
        result = fn()
        if isinstance(result, WorkerHealth):
            return result.status, result.healthy
        # Legacy string fallback for backends that haven't migrated yet.
        msg = str(result or "")
        bad = any(s in msg.lower() for s in ("unreachable", "not found", "timed out", "timeout", "error"))
        return msg, not bad
    except Exception as e:
        return f"health_check raised: {e}", False


def write_dashboard(path: str, report: dict) -> None:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    lines = [
        "# Grug System Health",
        "",
        f"_Last updated: {report['timestamp']}_",
        "",
        "## Workers",
        "",
        "| Worker | Model | Status |",
        "|:---|:---|:---|",
    ]
    for name, w in report["workers"].items():
        marker = "OK" if w["healthy"] else "DEGRADED"
        lines.append(f"| {name} | {w['model']} | {marker} — {w['status']} |")
    lines += [
        "",
        "## Queue",
        "",
        f"- Pending tasks: {report['queue_depth']}",
        f"- Dead-letter queue: {report['dlq_size']}",
        "",
    ]
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))


def _maybe_alert(report: dict, last_alerts: dict, callback: Optional[Callable[[str], None]],
                 dlq_threshold: int) -> None:
    """Fire the callback only on transitions, so we don't spam every poll."""
    if callback is None:
        return
    for name, w in report["workers"].items():
        prev = last_alerts.get(f"worker:{name}")
        cur = "ok" if w["healthy"] else "degraded"
        if prev is None:
            last_alerts[f"worker:{name}"] = cur
            continue
        if cur != prev:
            last_alerts[f"worker:{name}"] = cur
            if cur == "degraded":
                _safe_call(callback, f"[health] worker '{name}' degraded: {w['status']}")
            else:
                _safe_call(callback, f"[health] worker '{name}' recovered.")

    prev_dlq = last_alerts.get("dlq:over_threshold")
    over = report["dlq_size"] >= dlq_threshold
    cur_dlq = "over" if over else "under"
    if prev_dlq is None:
        last_alerts["dlq:over_threshold"] = cur_dlq
        return
    if cur_dlq != prev_dlq:
        last_alerts["dlq:over_threshold"] = cur_dlq
        if over:
            _safe_call(callback, f"[health] DLQ size ({report['dlq_size']}) exceeds threshold {dlq_threshold}.")


def _safe_call(callback: Callable[[str], None], msg: str) -> None:
    try:
        callback(msg)
    except Exception as e:
        print(f"[monitor] alert callback raised: {e}")
