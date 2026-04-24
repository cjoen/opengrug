"""Context assembly for the Grug system prompt.

Loads summaries, builds the system prompt, detects turn boundaries,
and handles auto-offload of pruned conversation turns.
"""

import os
from datetime import datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
from core.config import config



def build_system_prompt(base, capped_tail, rag_context="", instructions_block=""):
    """Assemble the full system prompt with persona, RAG hits, and today's activity."""
    try:
        tz = ZoneInfo(config.scheduler.timezone)
    except ZoneInfoNotFoundError:
        tz = ZoneInfo("UTC")

    now_local = datetime.now(tz=tz)
    today = now_local.strftime("%Y-%m-%d")
    current_time = now_local.strftime("%H:%M %Z")

    prompt = base.replace("{{CURRENT_DATE}}", today)
    prompt = prompt.replace("{{CURRENT_TIME}}", current_time)

    if instructions_block:
        prompt += f"\n\n## Self-Instructions\n{instructions_block}"
    if rag_context:
        prompt += f"\n\n## Relevant Memory\n{rag_context}"
    if capped_tail:
        prompt += f"\n\n## Today's Activity\n{capped_tail}"

    if getattr(config.llm, "thinking_mode", False):
        prompt += "<|think|>"

    return prompt


def find_turn_boundary(messages):
    """Find the index of the end of the first complete Turn.

    A Turn boundary is defined by the NEXT user message after position 0.
    Returns the index (exclusive) to slice at.
    """
    for i in range(1, len(messages)):
        if messages[i].get("role") == "user":
            return i
    return len(messages)


def auto_offload_pruned_turns(pruned, summarizer, storage):
    """Summarize pruned turns and append to daily notes."""
    turns_text = "\n".join(
        f"{m.get('role', 'unknown').upper()}: {m.get('content', '')}"
        for m in pruned
    )
    summary = summarizer.summarize_pruned_turns(turns_text)
    if summary:
        storage.append_log("auto-offload", summary)
