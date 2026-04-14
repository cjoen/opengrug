"""Scheduler tools for Grug — add, list, cancel scheduled tasks."""


def add_schedule(schedule_store, registry, tool_name, arguments=None,
                 schedule=None, description=None,
                 _channel=None, _user=None, _thread_ts=None):
    """Create a scheduled task. Returns confirmation string.

    ``_channel``, ``_user``, ``_thread_ts`` are injected by the Slack handler,
    not emitted by the LLM.
    """
    if arguments is None:
        arguments = {}

    # Validate that the target tool exists
    all_tools = {s["name"] for s in registry.get_all_schemas()}
    if tool_name not in all_tools:
        return f"Grug not know tool '{tool_name}'. Check list_capabilities."

    try:
        row_id = schedule_store.add_schedule(
            channel=_channel or "",
            user=_user or "",
            thread_ts=_thread_ts or "",
            tool_name=tool_name,
            arguments=arguments,
            schedule=schedule,
            description=description,
        )
    except ValueError as e:
        return f"Bad schedule: {e}"

    return f"Schedule #{row_id} created: {description or tool_name} ({schedule})"


def list_schedules(schedule_store, _channel=None, _user=None):
    """List active schedules."""
    rows = schedule_store.list_schedules(channel=_channel, user=_user)
    if not rows:
        return "No active schedules."

    lines = []
    for r in rows:
        recurring = "recurring" if r["is_recurring"] else "one-shot"
        desc = r["description"] or r["tool_name"]
        lines.append(f"#{r['id']} [{recurring}] {desc} — next: {r['next_run_at']} ({r['schedule']})")
    return "\n".join(lines)


def cancel_schedule(schedule_store, schedule_id):
    """Cancel a schedule by ID."""
    schedule_store.delete(int(schedule_id))
    return f"Schedule #{schedule_id} cancelled."
