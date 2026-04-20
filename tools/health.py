"""Health check tools for Grug."""

import os
import glob
import shutil
import time
import requests


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

    # LLM config
    lines.append(f"LLM: {llm_client.model} @ {llm_client.host}")

    # Vector memory
    try:
        vstats = vector_memory.stats()
        if vstats["enabled"]:
            lines.append(f"Vectors: enabled, {vstats['block_count']} blocks indexed, DB {_fmt_bytes(vstats['db_size'])}")
        else:
            lines.append("Vectors: disabled")
    except Exception as e:
        lines.append(f"Vectors: error reading stats ({e})")

    # Sessions
    try:
        lines.append(f"Sessions: {session_store.session_count()} active")
    except Exception as e:
        lines.append(f"Sessions: error ({e})")

    # Queue
    lines.append(f"Queue workers: {message_queue.worker_count}")

    # Schedules
    try:
        schedules = schedule_store.list_schedules()
        recurring = sum(1 for s in schedules if s.get("is_recurring"))
        one_shot = len(schedules) - recurring
        lines.append(f"Schedules: {len(schedules)} active ({recurring} recurring, {one_shot} one-shot)")
    except Exception as e:
        lines.append(f"Schedules: error ({e})")

    # Routing trace
    trace_path = os.path.join(brain_dir, "routing_trace.jsonl")
    if os.path.exists(trace_path):
        size = _fmt_bytes(os.path.getsize(trace_path))
        count = _count_lines(trace_path)
        lines.append(f"Routing trace: {count} entries, {size}")
    else:
        lines.append("Routing trace: empty")

    return "\n".join(lines)


def system_health(llm_client, brain_dir, **_kwargs):
    """Report on host system and infrastructure health."""
    lines = []

    # Disk usage
    try:
        usage = shutil.disk_usage("/")
        pct = usage.used / usage.total * 100
        lines.append(f"Disk: {_fmt_bytes(usage.total)} total, {_fmt_bytes(usage.used)} used, {_fmt_bytes(usage.free)} free ({pct:.0f}%)")
    except Exception as e:
        lines.append(f"Disk: error ({e})")

    # Memory
    try:
        with open("/proc/meminfo", "r") as f:
            meminfo = {}
            for line in f:
                parts = line.split()
                if parts[0] in ("MemTotal:", "MemAvailable:"):
                    meminfo[parts[0]] = int(parts[1]) * 1024  # kB to bytes
            total = meminfo.get("MemTotal:", 0)
            avail = meminfo.get("MemAvailable:", 0)
            lines.append(f"Memory: {_fmt_bytes(total)} total, {_fmt_bytes(avail)} available")
    except OSError:
        lines.append("Memory: stats not available (non-Linux host)")

    # Container uptime
    try:
        with open("/proc/1/stat", "r") as f:
            fields = f.read().split()
        # Field 21 is starttime in clock ticks
        starttime_ticks = int(fields[21])
        clock_ticks = os.sysconf("SC_CLK_TCK")
        with open("/proc/uptime", "r") as f:
            system_uptime = float(f.read().split()[0])
        process_age = system_uptime - (starttime_ticks / clock_ticks)
        days = int(process_age // 86400)
        hours = int((process_age % 86400) // 3600)
        mins = int((process_age % 3600) // 60)
        if days > 0:
            lines.append(f"Container uptime: {days}d {hours}h {mins}m")
        elif hours > 0:
            lines.append(f"Container uptime: {hours}h {mins}m")
        else:
            lines.append(f"Container uptime: {mins}m")
    except OSError:
        lines.append("Container uptime: not available (non-Linux host)")

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

    # brain/ directory stats
    try:
        total_size = 0
        file_count = 0
        for dirpath, _, filenames in os.walk(brain_dir):
            for fname in filenames:
                fpath = os.path.join(dirpath, fname)
                total_size += os.path.getsize(fpath)
                file_count += 1
        daily_count = len(glob.glob(os.path.join(brain_dir, "daily_notes", "*.md")))
        lines.append(f"Brain: {_fmt_bytes(total_size)} across {file_count} files, {daily_count} daily notes")
    except Exception as e:
        lines.append(f"Brain: error ({e})")

    return "\n".join(lines)
