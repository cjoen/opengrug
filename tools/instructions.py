"""Instruction and AAR tools for Grug."""

from functools import partial
from core.config import config


def register_tools(registry, storage, session_store, summarizer, router):
    """Register all SELF tools with the given registry."""

    max_chars = getattr(config.memory, "instructions_max_chars", 1500)

    registry.register_python_tool(
        name="add_instruction",
        schema={
            "description": "[SELF] Record a learned rule or preference that Grug should follow in future conversations.",
            "type": "object",
            "properties": {
                "instruction": {"type": "string", "description": "The rule or preference to remember (10-200 chars)"},
                "tag": {
                    "type": "string",
                    "enum": ["tasks", "notes", "scheduling", "conversation", "general"],
                    "description": "Category for this instruction (default: general)",
                },
            },
            "required": ["instruction"],
        },
        func=partial(_add_instruction, storage, max_chars),
        category="SELF",
        friendly_name="Learn a rule",
    )

    registry.register_python_tool(
        name="list_instructions",
        schema={
            "description": "[SELF] List all learned instructions with their numbers and tags.",
            "type": "object",
            "properties": {},
        },
        func=partial(_list_instructions, storage),
        category="SELF",
        friendly_name="List learned rules",
    )

    registry.register_python_tool(
        name="edit_instruction",
        schema={
            "description": "[SELF] Edit an existing instruction by number.",
            "type": "object",
            "properties": {
                "instruction_number": {"type": "integer", "description": "The instruction number to edit"},
                "instruction": {"type": "string", "description": "New instruction text (10-200 chars)"},
                "tag": {
                    "type": "string",
                    "enum": ["tasks", "notes", "scheduling", "conversation", "general"],
                    "description": "New tag (optional, keeps current if omitted)",
                },
            },
            "required": ["instruction_number", "instruction"],
        },
        func=partial(_edit_instruction, storage),
        category="SELF",
        friendly_name="Edit a rule",
    )

    registry.register_python_tool(
        name="remove_instruction",
        schema={
            "description": "[SELF] Remove a learned instruction by number.",
            "type": "object",
            "properties": {
                "instruction_number": {"type": "integer", "description": "The instruction number to remove"},
            },
            "required": ["instruction_number"],
        },
        func=partial(_remove_instruction, storage),
        destructive=True,
        category="SELF",
        friendly_name="Remove a rule",
    )

    registry.register_python_tool(
        name="run_aar",
        schema={
            "description": "[SELF] Run an After Action Report on the current conversation thread. Reviews what went wrong and proposes candidate instructions.",
            "type": "object",
            "properties": {},
        },
        func=partial(_run_aar, session_store, summarizer, router),
        category="SELF",
        friendly_name="Run AAR",
    )


def _add_instruction(storage, max_chars, instruction, tag="general"):
    return storage.add_instruction(instruction, tag, max_chars)


def _list_instructions(storage, **_kwargs):
    items = storage.get_instructions()
    if not items:
        return "No instructions yet."
    lines = []
    for i, item in enumerate(items, 1):
        lines.append(f"{i}. #{item['tag']} {item['text']}")
    return "\n".join(lines)


def _edit_instruction(storage, instruction_number, instruction, tag=None):
    return storage.edit_instruction(int(instruction_number), instruction, tag)


def _remove_instruction(storage, instruction_number):
    return storage.remove_instruction(int(instruction_number))


def _run_aar(session_store, summarizer, router, **_kwargs):
    thread_ts = getattr(router._request_state, "_schedule_thread_ts", None)
    if not thread_ts:
        return "AAR requires a thread context. Run this from a Slack thread."

    channel_id = getattr(router._request_state, "_schedule_channel", "")
    session = session_store.get_or_create(thread_ts, channel_id)
    messages = session.get("messages", [])

    if not messages:
        return "No conversation history in this thread."

    report = summarizer.generate_aar(messages)
    return report
