"""System tools for Grug (clarification, reply, capabilities)."""

import json
import os
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


def set_timezone(timezone_str, config, schedule_store):
    """Update the scheduler timezone in grug_config.json and live objects."""
    try:
        tz = ZoneInfo(timezone_str)
    except ZoneInfoNotFoundError:
        return f"Unknown timezone: {timezone_str!r}. Use an IANA name like 'America/Los_Angeles' or 'Europe/London'."

    # Find the config file
    config_path = None
    for candidate in ("./grug_config.json", "/app/grug_config.json"):
        if os.path.isfile(candidate):
            config_path = candidate
            break

    if config_path is None:
        return "Could not find grug_config.json to update."

    with open(config_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    data.setdefault("scheduler", {})["timezone"] = timezone_str

    with open(config_path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)

    # Update live objects
    config.scheduler.timezone = timezone_str
    schedule_store.tz = tz

    return ""


def ask_for_clarification(reason_for_confusion):
    return f"Grug confused! {reason_for_confusion}"


def reply_to_user(message):
    return message


def list_capabilities(registry):
    """List all registered tools (excluding internal ones)."""
    hidden_tools = {"ask_for_clarification", "list_capabilities", "reply_to_user"}
    lines = ["I can help you with the following things:"]
    for name, data in registry._python_tools.items():
        if name in hidden_tools:
            continue
        friendly = data[3]
        lines.append(f"• {friendly}")
    for name, data in registry._cli_tools.items():
        if name in hidden_tools:
            continue
        friendly = data[3]
        lines.append(f"• {friendly}")
    return "\n".join(lines)
