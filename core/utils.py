"""Shared utilities for Grug — prompt loading, input sanitization."""

import os


def load_prompt_files(prompts_dir: str) -> str:
    """Concatenate system.md, rules.md, schema_examples.md with headers."""
    filenames = ["system.md", "rules.md", "schema_examples.md"]
    parts = []
    for name in filenames:
        path = os.path.join(prompts_dir, name)
        with open(path, "r", encoding="utf-8") as f:
            parts.append(f"## {name}\n\n{f.read()}")
    return "\n\n".join(parts)


def _sanitize_untrusted(text: str, tag_name: str = "") -> str:
    """Strip XML-style delimiter tags from untrusted input to prevent prompt injection.

    Escapes both open and close variants of the given tag_name (e.g.
    <untrusted_context> and </untrusted_context>). When no tag_name is
    given, escapes all '<' as a broad defence.
    """
    if not tag_name:
        return text.replace("<", "&lt;")
    text = text.replace(f"</{tag_name}>", f"[{tag_name}_tag_stripped]")
    text = text.replace(f"<{tag_name}>", f"[{tag_name}_tag_stripped]")
    return text
