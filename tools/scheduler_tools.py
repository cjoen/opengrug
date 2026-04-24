"""Scheduler tools for Grug — add, list, cancel scheduled tasks."""

from functools import partial
from datetime import datetime, timezone


def register_tools(registry, schedule_store, router, config):
    """Register all SCHEDULE tools with the given registry."""
    from tools.system import set_timezone

    def _add_schedule_wrapper(tool_name, arguments=None, schedule=None, description=None):
        return add_schedule(
            schedule_store, registry, tool_name, arguments, schedule, description,
            _channel=getattr(router._request_state, '_schedule_channel', ''),
            _user=getattr(router._request_state, '_schedule_user', ''),
            _thread_ts=getattr(router._request_state, '_schedule_thread_ts', ''),
        )

    def _list_schedules_wrapper(**_kwargs):
        return list_schedules(
            schedule_store,
            _channel=getattr(router._request_state, '_schedule_channel', None),
            _user=getattr(router._request_state, '_schedule_user', None),
        )

    def _cancel_schedule_wrapper(schedule_number):
        return cancel_schedule(
            schedule_store, int(schedule_number),
            _channel=getattr(router._request_state, '_schedule_channel', None),
            _user=getattr(router._request_state, '_schedule_user', None),
        )

    registry.register_python_tool(
        name="add_schedule",
        schema={
            "description": "[SCHEDULE] Create a recurring cron job or one-shot scheduled task. For reminders, use tool_name='reply_to_user'. Schedule is a cron expression (e.g. '0 9 * * 1') or ISO datetime (e.g. '2026-04-14T15:00:00').",
            "type": "object",
            "properties": {
                "tool_name": {"type": "string", "description": "The tool to execute on schedule"},
                "arguments": {"type": "object", "description": "Arguments to pass to the tool"},
                "schedule": {"type": "string", "description": "Cron expression or ISO datetime"},
                "description": {"type": "string", "description": "Human-readable label"}
            },
            "required": ["tool_name", "schedule"]
        },
        func=_add_schedule_wrapper,
        category="SCHEDULE",
        friendly_name="Schedule a task"
    )
    registry.register_python_tool(
        name="list_schedules",
        schema={
            "description": "[SCHEDULE] List all active scheduled tasks and reminders.",
            "type": "object",
            "properties": {}
        },
        func=_list_schedules_wrapper,
        category="SCHEDULE",
        friendly_name="List schedules"
    )
    registry.register_python_tool(
        name="cancel_schedule",
        schema={
            "description": "[SCHEDULE] Cancel a scheduled task by its position number from list_schedules output.",
            "type": "object",
            "properties": {"schedule_number": {"type": "integer", "description": "Position number from list_schedules output (e.g. 1, 2, 3)"}},
            "required": ["schedule_number"]
        },
        func=_cancel_schedule_wrapper,
        category="SCHEDULE",
        friendly_name="Cancel a schedule"
    )
    def _remind_me_wrapper(message, when):
        return remind_me(
            schedule_store, message, when,
            _channel=getattr(router._request_state, '_schedule_channel', ''),
            _user=getattr(router._request_state, '_schedule_user', ''),
            _thread_ts=getattr(router._request_state, '_schedule_thread_ts', ''),
        )

    registry.register_python_tool(
        name="remind_me",
        schema={
            "description": "[SCHEDULE] Set a reminder. Grug will send you the message at the specified time. 'when' must be an ISO datetime (e.g. '2026-04-23T18:00:00').",
            "type": "object",
            "properties": {
                "message": {"type": "string", "description": "What to remind about"},
                "when": {"type": "string", "description": "ISO datetime for the reminder"}
            },
            "required": ["message", "when"]
        },
        func=_remind_me_wrapper,
        category="SCHEDULE",
        friendly_name="Set a reminder"
    )
    registry.register_python_tool(
        name="set_timezone",
        schema={
            "description": "[SCHEDULE] Update the scheduler timezone used to interpret one-shot reminder times. Use an IANA timezone name (e.g. 'America/Los_Angeles', 'Europe/London', 'Asia/Tokyo'). Cron expressions are always evaluated in UTC.",
            "type": "object",
            "properties": {
                "timezone_str": {"type": "string", "description": "IANA timezone name"}
            },
            "required": ["timezone_str"]
        },
        func=partial(set_timezone, config=config, schedule_store=schedule_store),
        category="SCHEDULE",
        friendly_name="Set scheduler timezone"
    )


def _fmt_next_run(next_run_at: str, tz) -> str:
    """Convert a UTC ISO string to a human-readable local time string."""
    utc_dt = datetime.fromisoformat(next_run_at).replace(tzinfo=timezone.utc)
    local_dt = utc_dt.astimezone(tz)
    return local_dt.strftime("%Y-%m-%d %H:%M %Z")


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

    desc_label = description or tool_name
    return f"Schedule created: {desc_label}"


def list_schedules(schedule_store, _channel=None, _user=None):
    """List active schedules with ephemeral position numbers."""
    rows = schedule_store.list_schedules(channel=_channel, user=_user)
    if not rows:
        return "No active schedules."

    lines = []
    for i, r in enumerate(rows, 1):
        recurring = "recurring" if r["is_recurring"] else "one-shot"
        desc = r["description"] or r["tool_name"]
        next_run = _fmt_next_run(r["next_run_at"], schedule_store.tz)
        lines.append(f"#{i} [{recurring}] {desc} — next: {next_run} ({r['schedule']})")
    return "\n".join(lines)


def remind_me(schedule_store, message, when,
              _channel=None, _user=None, _thread_ts=None):
    """Create a one-shot reminder. Thin wrapper around add_schedule."""
    try:
        schedule_store.add_schedule(
            channel=_channel or "",
            user=_user or "",
            thread_ts=_thread_ts or "",
            tool_name="reply_to_user",
            arguments={"message": message},
            schedule=when,
            description=f"Reminder: {message}",
        )
    except ValueError as e:
        return f"Bad reminder time: {e}"
    return f"Reminder set: {message}"


def cancel_schedule(schedule_store, schedule_number, _channel=None, _user=None):
    """Cancel a schedule by its position number from the last listing."""
    rows = schedule_store.list_schedules(channel=_channel, user=_user)

    idx = schedule_number - 1
    if idx < 0 or idx >= len(rows):
        return f"No schedule #{schedule_number} found."

    target = rows[idx]
    schedule_store.delete(target["id"])
    desc = target["description"] or target["tool_name"]
    return f"Schedule #{schedule_number} cancelled: {desc}"
