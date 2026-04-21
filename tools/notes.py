"""Note tools for Grug."""

import re
from functools import partial
from core.config import config


def register_tools(registry, storage, llm_client, vector_memory, base_dir):
    """Register all NOTES tools with the given registry."""
    from tools.search import search

    registry.register_python_tool(
        name="add_note",
        schema={
            "description": "[NOTES] Save an insight, thought, or generic memory permanently.",
            "type": "object",
            "properties": {
                "content": {"type": "string"},
                "tags": {"type": "array", "items": {"type": "string", "enum": ["dev", "personal", "infra", "meeting", "urgent", "draft", "misc"]}}
            },
            "required": ["content"]
        },
        func=partial(add_note, storage, llm_client),
        category="NOTES",
        friendly_name="Save a note"
    )
    registry.register_python_tool(
        name="get_recent_notes",
        schema={"description": "[NOTES] Fetch and display recent notes as a readable grouped bulletin.", "type": "object", "properties": {}},
        func=partial(get_recent_notes, storage),
        category="NOTES",
        friendly_name="Read recent notes"
    )
    registry.register_python_tool(
        name="query_memory",
        schema={"description": "[NOTES] Semantic/fuzzy search for older notes when you don't have an exact keyword. Use 'search' tool first for keyword lookups.", "type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]},
        func=vector_memory.query_memory,
        category="NOTES",
        friendly_name="Search memory"
    )
    registry.register_python_tool(
        name="search",
        schema={
            "description": "[NOTES] Search all notes, summaries, and tasks for a keyword or phrase. Use this as the default search tool.",
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Text to search for"},
                "limit": {"type": "integer", "description": "Max results (default 20)"}
            },
            "required": ["query"]
        },
        func=partial(search, base_dir, vector_memory=vector_memory),
        category="NOTES",
        friendly_name="Search everything"
    )


def add_note(storage, llm_client, content, tags=None):
    """Save a note, optionally generating a title for longer notes."""
    if storage is None:
        return "Grug cannot save note. Storage not connected."

    word_count = len(content.split())
    if word_count >= 10 and llm_client is not None:
        title_prompt = (
            "Generate a short title of 5-8 words for the note below. "
            "Return ONLY the title text — no punctuation, no quotes, no explanation.\n\n"
            f"NOTE: {content}"
        )
        title = llm_client.generate(title_prompt)
        if title:
            content = f"**Title: {title.strip()}** {content}"

    storage.add_note(content=content, tags=tags)
    return "Note saved."


def get_recent_notes(storage, **_kwargs):
    """Fetch and format recent notes grouped by tag."""
    if storage is None:
        return "Grug cannot find notes. Storage not connected."

    raw = storage.get_raw_notes(limit=config.memory.notes_display_limit)
    if not raw:
        return "No notes yet."

    groups = {}
    for line in raw.splitlines():
        stripped = line.strip()
        if not stripped.startswith("- "):
            continue

        tag = "misc"
        tag_match = re.search(r"#(\w+)", stripped)
        if tag_match:
            tag = tag_match.group(1)

        # Strip timestamp prefix: "- HH:MM:SS content" → "content"
        content = re.sub(r"^- \d{2}:\d{2}:\d{2} ", "", stripped).strip()
        content = re.sub(r"\s*#\w+", "", content).strip()
        if not content:
            continue

        groups.setdefault(tag, []).append(content)

    if not groups:
        return "No notes yet."

    sections = []
    for tag, notes in groups.items():
        lines = f"[{tag.upper()}]\n"
        lines += "\n".join(f"  - {n}" for n in notes)
        sections.append(lines)

    return "\n\n".join(sections)
