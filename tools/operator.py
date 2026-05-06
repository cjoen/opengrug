"""Operator tools — runtime ops actions exposed as Grug tools.

Replaces the legacy ``scripts/system_utils.py`` CLI: queue inspection, DLQ
management, and task cancellation are all callable from Slack via the normal
tool dispatch path. Destructive actions (clear, drain, cancel) are gated
behind HITL approval at the registry layer.

Tools receive the queue, DLQ, and worker_pool via closure injection at
registration time so the live runtime references are captured.
"""

from __future__ import annotations

from core.task import Task, TaskPriority, TaskState
from workers.monitor import collect_report


def register_tools(registry, task_queue, dlq, worker_pool):
    """Register operator tools. All tools are SYSTEM category."""

    registry.register_category_description(
        "OPERATOR",
        "inspect or manage Grug's task queue, dead letter queue, and workers"
    )

    registry.register_python_tool(
        name="queue_status",
        schema={
            "description": "[OPERATOR] Report worker health, queue depth, and DLQ size.",
            "type": "object",
            "properties": {},
        },
        func=lambda **_: queue_status(worker_pool, task_queue, dlq),
        destructive=False,
        category="OPERATOR",
        friendly_name="Queue status",
    )

    registry.register_python_tool(
        name="retry_dlq",
        schema={
            "description": "[OPERATOR] Re-enqueue all tasks in the dead letter queue.",
            "type": "object",
            "properties": {},
        },
        func=lambda **_: retry_dlq(task_queue, dlq),
        destructive=False,
        category="OPERATOR",
        friendly_name="Retry DLQ",
    )

    registry.register_python_tool(
        name="clear_dlq",
        schema={
            "description": "[OPERATOR] Permanently purge brain/failed_tasks.md.",
            "type": "object",
            "properties": {},
        },
        func=lambda **_: clear_dlq(dlq),
        destructive=True,
        category="OPERATOR",
        friendly_name="Clear DLQ",
    )

    registry.register_python_tool(
        name="drain_queue",
        schema={
            "description": "[OPERATOR] Cancel all queued BACKGROUND tasks.",
            "type": "object",
            "properties": {},
        },
        func=lambda **_: drain_queue(task_queue),
        destructive=True,
        category="OPERATOR",
        friendly_name="Drain background queue",
    )

    registry.register_python_tool(
        name="cancel_task",
        schema={
            "description": "[OPERATOR] Cancel a specific task by id.",
            "type": "object",
            "properties": {
                "task_id": {"type": "string", "description": "The task id to cancel."},
            },
            "required": ["task_id"],
        },
        func=lambda task_id, **_: cancel_task(task_queue, task_id),
        destructive=True,
        category="OPERATOR",
        friendly_name="Cancel task",
    )


# ---------------------------------------------------------------------------
# Tool implementations
# ---------------------------------------------------------------------------

def queue_status(worker_pool, task_queue, dlq) -> str:
    rpt = collect_report(worker_pool, task_queue, dlq)
    lines = [f"Queue depth: {rpt['queue_depth']}", f"DLQ size: {rpt['dlq_size']}", "Workers:"]
    for name, w in rpt["workers"].items():
        marker = "OK" if w["healthy"] else "DEGRADED"
        lines.append(f"  - {name} ({w['model']}): {marker} — {w['status']}")
    return "\n".join(lines)


def retry_dlq(task_queue, dlq) -> str:
    entries = dlq.list_failed()
    if not entries:
        return "DLQ empty — nothing to retry."
    re_enqueued = 0
    for e in entries:
        try:
            t = Task(
                session_id=e.get("session", "dlq-retry"),
                user_id=e.get("user", "system"),
                agent_name=e.get("agent", "chat_agent"),
                context=e.get("context", "") or "",
                priority=_priority(e.get("priority", "BACKGROUND")),
                metadata={"_retry_of_dlq": e["task_id"]},
                root_task_id=e.get("root") or e["task_id"],
            )
            task_queue.enqueue(t)
            dlq.remove(e["task_id"])
            re_enqueued += 1
        except Exception as ex:
            print(f"[operator] retry_dlq failed for {e.get('task_id')}: {ex}")
    return f"Re-enqueued {re_enqueued}/{len(entries)} task(s) from the DLQ."


def clear_dlq(dlq) -> str:
    n = dlq.clear()
    return f"DLQ cleared ({n} entries removed)."


def drain_queue(task_queue) -> str:
    """Cancel all QUEUED BACKGROUND tasks. URGENT tasks are left alone."""
    with task_queue._lock:
        targets = [t for t in task_queue._heap
                   if t.priority is TaskPriority.BACKGROUND and t.state is TaskState.QUEUED]
    cancelled = 0
    for t in targets:
        if task_queue.cancel(t.id):
            cancelled += 1
    return f"Drained {cancelled} background task(s)."


def cancel_task(task_queue, task_id: str) -> str:
    if task_queue.cancel(task_id):
        return f"Cancelled task {task_id}."
    return f"Task {task_id} not found or already terminal."


def _priority(name: str) -> TaskPriority:
    try:
        return TaskPriority[name.strip().upper()]
    except (KeyError, AttributeError):
        return TaskPriority.BACKGROUND
