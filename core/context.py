"""Context assembly for the Grug system prompt.

Loads summaries, builds the system prompt, detects turn boundaries,
and handles auto-offload of pruned conversation turns.
"""

import os
import glob
from datetime import datetime
from core.config import config


def load_summary_files(summaries_dir, days_limit):
    """Read up to ``days_limit`` summary files, newest first, return concatenated content."""
    if not os.path.isdir(summaries_dir):
        return ""
    summary_files = sorted(
        glob.glob(os.path.join(summaries_dir, "*.summary.md")),
        reverse=True,
    )[:days_limit]
    parts = []
    for fpath in summary_files:
        try:
            with open(fpath, "r", encoding="utf-8") as f:
                parts.append(f.read().strip())
        except OSError:
            continue
    return "\n\n".join(parts)


def build_system_prompt(base, summaries, capped_tail, compression_mode=None):
    """Assemble the full system prompt with persona, summaries, and today's notes."""
    if compression_mode is None:
        compression_mode = config.llm.default_compression
    today = datetime.now().strftime("%Y-%m-%d")
    prompt = base.replace("{{COMPRESSION_MODE}}", compression_mode)
    prompt = prompt.replace("{{CURRENT_DATE}}", today)

    if summaries:
        prompt += f"\n\n## Recent Summaries (last {config.memory.summary_days_limit} days)\n{summaries}"
    if capped_tail:
        prompt += f"\n\n## Today's Notes\n{capped_tail}"

    if config.llm.thinking_mode:
        prompt += "\n<|think|>"

    return prompt


def find_turn_boundary(messages):
    """Find the index of the end of the first complete Turn.

    A Turn boundary is defined by the NEXT user message after position 0.
    Returns the index (exclusive) to slice at.
    """
    for i in range(1, len(messages)):
        if messages[i].get("role") == "user":
            return i
    return max(len(messages) - 1, 1)


def auto_offload_pruned_turns(pruned, summarizer, storage):
    """Summarize pruned turns and append to daily notes."""
    try:
        turns_text = "\n".join(
            f"{m.get('role', 'unknown').upper()}: {m.get('content', '')}"
            for m in pruned
        )
        summary = summarizer.summarize_pruned_turns(turns_text)
        if summary:
            storage.append_log("auto-offload", summary)
    except Exception as e:
        print(f"[auto-offload] error: {e}")
