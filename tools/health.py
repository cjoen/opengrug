"""Health check tools for Grug."""

import os
import shutil
import time
import requests
from functools import partial


def register_tools(registry, vector_memory, session_store, message_queue, schedule_store, llm_client, brain_dir):
    """Register all SYSTEM health tools with the given registry."""
    registry.register_python_tool(
        name="grug_health",
        schema={
            "description": "[SYSTEM] Show Grug's internal health: LLM config, vector memory, sessions, schedules, queue, and routing trace stats.",
            "type": "object",
            "properties": {}
        },
        func=partial(grug_health, vector_memory, session_store, message_queue, schedule_store, llm_client, brain_dir),
        category="SYSTEM",
        friendly_name="Grug health check"
    )
    registry.register_python_tool(
        name="system_health",
        schema={
            "description": "[SYSTEM] Show host system health: disk usage and Ollama connectivity.",
            "type": "object",
            "properties": {}
        },
        func=partial(system_health, llm_client),
        category="SYSTEM",
        friendly_name="System health check"
    )


def _fmt_bytes(n):
    """Format byte count as human-readable string."""
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} TB"


def _count_lines(path):
    """Count lines in a file without reading it all into memory."""
    try:
        with open(path, "rb") as f:
            return sum(1 for _ in f)
    except OSError:
        return 0


def grug_health(vector_memory, session_store, message_queue, schedule_store, llm_client, brain_dir, **_kwargs):
    """Report on Grug's internal state."""
    lines = []

    lines.append(f"LLM: {llm_client.model} @ {llm_client.host}")

    try:
        vstats = vector_memory.stats()
        if vstats["enabled"]:
            lines.append(f"Vectors: enabled, {vstats['block_count']} blocks indexed, DB {_fmt_bytes(vstats['db_size'])}")
        else:
            lines.append("Vectors: disabled")
    except Exception as e:
        lines.append(f"Vectors: error reading stats ({e})")

    try:
        lines.append(f"Sessions: {session_store.session_count()} active")
    except Exception as e:
        lines.append(f"Sessions: error ({e})")

    lines.append(f"Queue workers: {message_queue.worker_count}")

    try:
        schedules = schedule_store.list_schedules()
        recurring = sum(1 for s in schedules if s.get("is_recurring"))
        one_shot = len(schedules) - recurring
        lines.append(f"Schedules: {len(schedules)} active ({recurring} recurring, {one_shot} one-shot)")
    except Exception as e:
        lines.append(f"Schedules: error ({e})")

    trace_path = os.path.join(brain_dir, "routing_trace.jsonl")
    if os.path.exists(trace_path):
        size = _fmt_bytes(os.path.getsize(trace_path))
        count = _count_lines(trace_path)
        lines.append(f"Routing trace: {count} entries, {size}")
    else:
        lines.append("Routing trace: empty")

    return "\n".join(lines)


def system_health(llm_client, **_kwargs):
    """Report on host system and infrastructure health."""
    lines = []

    # Disk usage
    try:
        usage = shutil.disk_usage("/")
        pct = usage.used / usage.total * 100
        lines.append(f"Disk: {_fmt_bytes(usage.total)} total, {_fmt_bytes(usage.used)} used, {_fmt_bytes(usage.free)} free ({pct:.0f}%)")
    except Exception as e:
        lines.append(f"Disk: error ({e})")

    # Ollama health
    try:
        start = time.time()
        resp = requests.get(f"{llm_client.host}/api/tags", timeout=5)
        elapsed_ms = int((time.time() - start) * 1000)
        resp.raise_for_status()
        models = resp.json().get("models", [])
        model_names = [m.get("name", "") for m in models]
        if any(llm_client.model in name for name in model_names):
            lines.append(f"Ollama: reachable ({elapsed_ms}ms), {llm_client.model} loaded")
        else:
            lines.append(f"Ollama: reachable ({elapsed_ms}ms), {llm_client.model} NOT found. Available: {', '.join(model_names)}")
    except requests.exceptions.ConnectionError:
        lines.append(f"Ollama: unreachable at {llm_client.host}")
    except requests.exceptions.Timeout:
        lines.append(f"Ollama: timeout at {llm_client.host}")
    except Exception as e:
        lines.append(f"Ollama: error ({e})")

    return "\n".join(lines)
