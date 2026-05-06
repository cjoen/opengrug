"""Dead Letter Queue — append-only failure log for Tasks.

Failed and cancelled tasks are serialized to a markdown file (default
``brain/failed_tasks.md``) so an operator can inspect them in Obsidian and
either retry or purge. The format is human-friendly markdown, but the parser
is strict enough to round-trip its own output for ``list_failed`` / ``remove``.
"""

from __future__ import annotations

import os
import re
import threading
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

from core.task import Task


@dataclass
class DLQEntry:
    task_id: str
    timestamp: str
    agent: str
    priority: str
    session_id: str
    user_id: str
    context: str
    error: str
    traceback: str
    reason: str = "failed"  # failed | user_cancelled | timeout
    root_task_id: str = ""
    attempt: int = 1


_HEADER_RE = re.compile(r"^##\s+\[([^\]]+)\]\s+—\s+(.+)$")


class DeadLetterQueue:
    """Append-only failure log backed by a markdown file."""

    def __init__(self, file_path: str):
        self.file_path = file_path
        self._lock = threading.Lock()
        os.makedirs(os.path.dirname(file_path) or ".", exist_ok=True)

    # ------------------------------------------------------------------
    # Write
    # ------------------------------------------------------------------

    def add(self, task: Task, error: str, traceback_str: str = "",
            reason: str = "failed") -> None:
        entry = DLQEntry(
            task_id=task.id,
            timestamp=datetime.now().isoformat(timespec="seconds"),
            agent=task.agent_name,
            priority=task.priority.name,
            session_id=task.session_id,
            user_id=task.user_id,
            context=task.context or "",
            error=error or "",
            traceback=traceback_str or "",
            reason=reason,
            root_task_id=task.root_task_id or task.id,
            attempt=task.attempt,
        )
        block = self._format(entry)
        with self._lock:
            with open(self.file_path, "a", encoding="utf-8") as f:
                f.write(block)

    @staticmethod
    def _format(e: DLQEntry) -> str:
        ctx = (e.context or "").replace("\n", " ").strip()
        if len(ctx) > 500:
            ctx = ctx[:500] + "…"
        tb_block = f"\n```\n{e.traceback.rstrip()}\n```\n" if e.traceback else ""
        return (
            f"## [{e.task_id}] — {e.timestamp}\n"
            f"- **Agent:** {e.agent}\n"
            f"- **Priority:** {e.priority}\n"
            f"- **Session:** {e.session_id}\n"
            f"- **User:** {e.user_id}\n"
            f"- **Reason:** {e.reason}\n"
            f"- **Root:** {e.root_task_id}\n"
            f"- **Attempt:** {e.attempt}\n"
            f"- **Context:** {ctx}\n"
            f"- **Error:** {e.error}\n"
            f"- **Traceback:**{tb_block}\n"
        )

    # ------------------------------------------------------------------
    # Read
    # ------------------------------------------------------------------

    def list_failed(self) -> list[dict]:
        if not os.path.exists(self.file_path):
            return []
        with self._lock:
            with open(self.file_path, "r", encoding="utf-8") as f:
                text = f.read()
        return self._parse(text)

    @staticmethod
    def _parse(text: str) -> list[dict]:
        entries: list[dict] = []
        # Split on top-level headers; keep the header with each block.
        chunks = re.split(r"(?m)^(?=##\s+\[)", text)
        for chunk in chunks:
            chunk = chunk.strip()
            if not chunk:
                continue
            lines = chunk.splitlines()
            m = _HEADER_RE.match(lines[0])
            if not m:
                continue
            entry: dict = {
                "task_id": m.group(1),
                "timestamp": m.group(2).strip(),
                "traceback": "",
            }
            in_tb = False
            tb_lines: list[str] = []
            for line in lines[1:]:
                if line.startswith("```"):
                    in_tb = not in_tb
                    continue
                if in_tb:
                    tb_lines.append(line)
                    continue
                lm = re.match(r"^-\s+\*\*([^:]+):\*\*\s*(.*)$", line)
                if not lm:
                    continue
                key = lm.group(1).strip().lower()
                entry[key] = lm.group(2).strip()
            if tb_lines:
                entry["traceback"] = "\n".join(tb_lines).rstrip()
            entries.append(entry)
        return entries

    # ------------------------------------------------------------------
    # Mutate
    # ------------------------------------------------------------------

    def remove(self, task_id: str) -> bool:
        """Remove a single entry by task_id. Returns True if removed."""
        if not os.path.exists(self.file_path):
            return False
        with self._lock:
            with open(self.file_path, "r", encoding="utf-8") as f:
                text = f.read()
            chunks = re.split(r"(?m)^(?=##\s+\[)", text)
            kept: list[str] = []
            removed = False
            for chunk in chunks:
                if not chunk.strip():
                    continue
                m = _HEADER_RE.match(chunk.splitlines()[0])
                if m and m.group(1) == task_id:
                    removed = True
                    continue
                kept.append(chunk if chunk.endswith("\n") else chunk + "\n")
            if removed:
                with open(self.file_path, "w", encoding="utf-8") as f:
                    f.write("".join(kept))
            return removed

    def clear(self) -> int:
        """Purge all entries. Returns the number removed."""
        with self._lock:
            if not os.path.exists(self.file_path):
                return 0
            with open(self.file_path, "r", encoding="utf-8") as f:
                text = f.read()
            count = len(self._parse(text))
            with open(self.file_path, "w", encoding="utf-8") as f:
                f.write("")
            return count

    def size(self) -> int:
        return len(self.list_failed())
