"""Markdown task board for Grug."""

import os
import re


class TaskBoard:
    """Simple markdown-backed task board."""

    def __init__(self, tasks_file: str):
        self.tasks_file = tasks_file

    def _ensure(self):
        """Create tasks.md if it doesn't exist."""
        if not os.path.exists(self.tasks_file):
            os.makedirs(os.path.dirname(self.tasks_file), exist_ok=True)
            with open(self.tasks_file, "w", encoding="utf-8") as f:
                f.write("# Grug Task Board\n\n")

    def add_task(self, title, priority=None, assignee=None, description=None):
        """Append a markdown checkbox to tasks.md."""
        self._ensure()
        parts = [f"- [ ] {title}"]
        if priority:
            parts.append(f"[{priority}]")
        if assignee:
            parts.append(f"@{assignee}")
        line = " ".join(parts)
        if description:
            line += f"\n  > {description}"
        with open(self.tasks_file, "a", encoding="utf-8") as f:
            f.write(line + "\n")
        return f"Task added: {title}"

    def list_tasks(self, status=None):
        """Read tasks.md, optionally filter by open/done."""
        self._ensure()
        with open(self.tasks_file, "r", encoding="utf-8") as f:
            lines = f.readlines()

        tasks = []
        for i, line in enumerate(lines, 1):
            stripped = line.strip()
            if stripped.startswith("- [ ] "):
                if status is None or status.lower() in ("open", "todo", "to do"):
                    tasks.append(f"{i}: {stripped}")
            elif stripped.startswith("- [x] "):
                if status is None or status.lower() == "done":
                    tasks.append(f"{i}: {stripped}")

        if not tasks:
            return "No tasks found."
        return "\n".join(tasks)

    def edit_task(self, line_number, status=None, append_notes=None):
        """Toggle task checkbox or append notes by line number."""
        self._ensure()
        with open(self.tasks_file, "r", encoding="utf-8") as f:
            lines = f.readlines()

        idx = int(line_number) - 1
        if idx < 0 or idx >= len(lines):
            return f"Line {line_number} not found in tasks.md"

        line = lines[idx]
        if status and status.lower() == "done" and "- [ ] " in line:
            lines[idx] = line.replace("- [ ] ", "- [x] ", 1)
        elif status and status.lower() in ("open", "todo", "to do") and "- [x] " in line:
            lines[idx] = line.replace("- [x] ", "- [ ] ", 1)

        if append_notes:
            lines[idx] = lines[idx].rstrip("\n") + f"  ({append_notes})\n"

        with open(self.tasks_file, "w", encoding="utf-8") as f:
            f.writelines(lines)
        return f"Task on line {line_number} updated."

    def summarize_board(self, llm_client, status=None):
        """Fetch tasks and produce a caveman summary."""
        raw = self.list_tasks(status=status)
        if raw == "No tasks found.":
            return "Grug see empty board. No tasks here."

        summary_prompt = (
            "You are Grug, a friendly caveman. Read the task list below and write a short "
            "2-3 sentence summary in caveman voice. Mention total count and any patterns you see "
            "(lots in progress, many done, urgent items, etc). Plain text only — no JSON, no lists.\n\n"
            f"TASKS:\n{raw}\n\nGRUG SUMMARY:"
        )
        summary = llm_client.generate(summary_prompt) if llm_client else ""
        if not summary:
            summary = "Grug brain foggy. Here what Grug see:"

        buckets = {"Todo": [], "In Progress": [], "Done": [], "Other": []}
        for line in raw.splitlines():
            line = re.sub(r"^\d+:\s*", "", line).strip()
            if not line:
                continue
            matched = False
            for status_key in ("Todo", "In Progress", "Done"):
                if status_key.lower() in line.lower():
                    buckets[status_key].append(line)
                    matched = True
                    break
            if not matched:
                buckets["Other"].append(line)

        task_lines = []
        for status_key, items in buckets.items():
            for item in items:
                task_lines.append(f"• [{status_key}] {item}")

        formatted_tasks = "\n".join(task_lines) if task_lines else raw
        return f"{summary}\n\nTasks:\n{formatted_tasks}"
