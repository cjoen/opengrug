"""System tools for Grug (clarification, reply, capabilities)."""

import json
import os
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
from core.utils import load_prompt_files


def register_tools(registry, router):
    """Register core SYSTEM tools (chat, clarification, capabilities, reload)."""
    registry.register_category_description("NOTES", "save a note, or search old notes")
    registry.register_category_description("TASKS", "add a task, list tasks, or complete a task")
    registry.register_category_description("SYSTEM", "chat, ask for help, or see what Grug can do")
    registry.register_category_description("SCHEDULE", "add, list, or cancel a scheduled task or reminder")

    registry.register_python_tool(
        name="ask_for_clarification",
        schema={
            "description": "[CHAT] Output ONLY when you need more details from the user to act on a board/note/task request (e.g. missing title, unclear which task to edit, ambiguous date). Do NOT use for factual trivia or chit-chat — those go to reply_to_user. The `reason_for_confusion` MUST be written in warm caveman voice (e.g. 'Grug need more. Which task you mean?').",
            "type": "object",
            "properties": {
                "reason_for_confusion": {"type": "string"}
            },
            "required": ["reason_for_confusion"]
        },
        func=ask_for_clarification,
        category="SYSTEM",
        friendly_name="Ask for clarification"
    )
    registry.register_python_tool(
        name="list_capabilities",
        schema={
            "description": "[META] Output ONLY when the user explicitly asks what tools/commands are available or what Grug can do (e.g. 'what can you do?', 'list your commands', 'help'). Do NOT use for greetings like 'hi' or 'hey grug' — those go to reply_to_user.",
            "type": "object",
            "properties": {},
            "required": []
        },
        func=lambda: list_capabilities(registry),
        category="SYSTEM",
        friendly_name="List capabilities"
    )
    registry.register_python_tool(
        name="reply_to_user",
        schema={
            "description": "[CHAT] Output this when holding conversations, brainstorming, providing analysis, or chatting with the user when no concrete action is requested.",
            "type": "object",
            "properties": {
                "message": {"type": "string"}
            },
            "required": ["message"]
        },
        func=reply_to_user,
        category="SYSTEM",
        friendly_name="Chat with Grug"
    )
    registry.register_python_tool(
        name="reload_prompts",
        schema={
            "description": "[SYSTEM] Reload system prompts from disk without restarting. Use after editing prompts/ files.",
            "type": "object",
            "properties": {}
        },
        func=lambda: reload_prompts(router),
        category="SYSTEM",
        friendly_name="Reload prompts"
    )


def reload_prompts(router):
    """Reload prompt files and update the router's cached base prompt."""
    router._cached_base_prompt = load_prompt_files(router._prompt_dir)
    return "Prompts reloaded."


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

    return f"Timezone set to {timezone_str}."


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
