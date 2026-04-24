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
    """Escape angle brackets in untrusted input to prevent prompt injection."""
    return text.replace("<", "&lt;")
