"""Task tools for Grug — markdown-backed task list.

Tasks live in a plain markdown file (Obsidian-friendly).
Position numbers are assigned at display time only — they're ephemeral
references the user can use based on the last list_tasks output.
"""

import os


_PRIORITY_ORDER = {"high": 0, "medium": 1, "low": 2}


def register_tools(registry, task_list, storage):
    """Register all TASKS tools with the given registry."""
    registry.register_python_tool(
        name="add_task",
        schema={
            "description": "[TASKS] Add an item to the task list. Example: /todo high Fix the db index",
            "type": "object",
            "properties": {
                "title": {"type": "string"},
                "priority": {"type": "string", "enum": ["high", "medium", "low"]}
            },
            "required": ["title"]
        },
        func=task_list.add_task,
        category="TASKS",
        friendly_name="Add a task"
    )
    registry.register_python_tool(
        name="list_tasks",
        schema={
            "description": "[TASKS] Show all items on the task list with position numbers.",
            "type": "object",
            "properties": {}
        },
        func=task_list.list_tasks,
        category="TASKS",
        friendly_name="List tasks"
    )
    registry.register_python_tool(
        name="complete_task",
        schema={
            "description": "[TASKS] Mark a task as done by its position number from the last list_tasks output.",
            "type": "object",
            "properties": {
                "task_number": {"type": "integer", "description": "Position number from list_tasks output (e.g. 1, 2, 3)"}
            },
            "required": ["task_number"]
        },
        func=task_list.complete_task,
        category="TASKS",
        friendly_name="Complete a task"
    )


class TaskList:
    """Markdown-backed prioritized task list.

    The markdown file stays clean for Obsidian viewing — no IDs stored.
    Position numbers are assigned dynamically when listing tasks.
    """

    def __init__(self, tasks_file, storage):
        self.tasks_file = tasks_file
        self.storage = storage

    def _ensure(self):
        """Create tasks.md if it doesn't exist."""
        if not os.path.exists(self.tasks_file):
            os.makedirs(os.path.dirname(self.tasks_file), exist_ok=True)
            with open(self.tasks_file, "w", encoding="utf-8") as f:
                f.write("# Grug Task List\n\n")

    def _parse_tasks(self):
        """Read tasks.md and return (header_lines, task_lines) where each task is (line_text, priority_rank)."""
        self._ensure()
        with open(self.tasks_file, "r", encoding="utf-8") as f:
            lines = f.readlines()

        header = []
        tasks = []
        for line in lines:
            stripped = line.strip()
            if stripped.startswith("- "):
                # Extract priority tag like [high]
                priority_rank = 3  # unprioritized sorts last
                for tag, rank in _PRIORITY_ORDER.items():
                    if f"[{tag}]" in stripped:
                        priority_rank = rank
                        break
                tasks.append((line, priority_rank))
            else:
                if not tasks:
                    header.append(line)
        return header, tasks

    def _write_sorted(self, header, tasks):
        """Sort tasks by priority and write back."""
        tasks.sort(key=lambda t: t[1])
        with open(self.tasks_file, "w", encoding="utf-8") as f:
            f.writelines(header)
            for line, _ in tasks:
                f.write(line)

    def add_task(self, title, priority=None):
        """Add a task and re-sort by priority."""
        header, tasks = self._parse_tasks()

        parts = [f"- {title}"]
        if priority:
            parts.append(f"[{priority}]")
        line = " ".join(parts) + "\n"

        priority_rank = _PRIORITY_ORDER.get(priority, 3)
        tasks.append((line, priority_rank))
        self._write_sorted(header, tasks)

        tag = f" [{priority}]" if priority else ""
        return f"Task added: {title}{tag}"

    def list_tasks(self, **_kwargs):
        """Return the task list with position numbers."""
        header, tasks = self._parse_tasks()
        tasks.sort(key=lambda t: t[1])

        if not tasks:
            return "No tasks. Cave is clean."

        lines = []
        for i, (line_text, _) in enumerate(tasks, 1):
            lines.append(f"#{i}: {line_text.strip()}")
        return "\n".join(lines)

    def complete_task(self, task_number):
        """Remove a task by its position number and log completion."""
        header, tasks = self._parse_tasks()
        tasks.sort(key=lambda t: t[1])

        idx = int(task_number) - 1
        if idx < 0 or idx >= len(tasks):
            return f"No task #{task_number} found."

        removed_line, _ = tasks[idx]
        removed_text = removed_line.strip().lstrip("- ").strip()
        del tasks[idx]

        self._write_sorted(header, tasks)
        self.storage.append_log("task", f"Completed: {removed_text}")
        return f"Task #{task_number} completed: {removed_text}"
