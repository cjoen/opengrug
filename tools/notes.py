"""Note tools for Grug."""

import re
from core.config import config


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

    return storage.add_note(content=content, tags=tags)


def get_recent_notes(storage):
    """Fetch and format recent notes grouped by tag."""
    if storage is None:
        return "Grug cannot find notes. Storage not connected."

    raw = storage.get_raw_notes(limit=config.memory.notes_display_limit)
    if not raw:
        return "Cave empty. No notes yet."

    groups = {}
    for line in raw.splitlines():
        if "[note]" not in line:
            continue
        tag = "misc"
        tag_match = re.search(r"#(\w+)", line)
        if tag_match:
            tag = tag_match.group(1)
        content = re.sub(r"^- \d+:\d+:\d+ \[note\] ", "", line).strip()
        content = re.sub(r"\s*#\w+", "", content).strip()
        groups.setdefault(tag, []).append(content)

    sections = []
    for tag, notes in groups.items():
        lines = f"[{tag.upper()}]\n"
        lines += "\n".join(f"  - {n}" for n in notes)
        sections.append(lines)

    return "\n\n".join(sections)
