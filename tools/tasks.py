"""Markdown task list for Grug."""

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
            "description": "[TASKS] Show all items on the task list.",
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
            "description": "[TASKS] Mark a task as done by its line number. The item is removed from the list and logged to daily notes.",
            "type": "object",
            "properties": {
                "line_number": {"type": "string", "description": "Line number from list_tasks output"}
            },
            "required": ["line_number"]
        },
        func=task_list.complete_task,
        category="TASKS",
        friendly_name="Complete a task"
    )


class TaskList:
    """Simple markdown-backed prioritized task list."""

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
        return ""

    def list_tasks(self, **_kwargs):
        """Return the full task list with line numbers."""
        self._ensure()
        with open(self.tasks_file, "r", encoding="utf-8") as f:
            lines = f.readlines()

        tasks = []
        for i, line in enumerate(lines, 1):
            if line.strip().startswith("- "):
                tasks.append(f"{i}: {line.strip()}")

        if not tasks:
            return "No tasks found."
        return "\n".join(tasks)

    def complete_task(self, line_number):
        """Remove a task by line number and log completion to daily notes."""
        self._ensure()
        with open(self.tasks_file, "r", encoding="utf-8") as f:
            lines = f.readlines()

        idx = int(line_number) - 1
        if idx < 0 or idx >= len(lines):
            return f"Line {line_number} not found in tasks.md"

        removed = lines[idx].strip()
        if not removed.startswith("- "):
            return f"Line {line_number} is not a task."

        # Log completion to daily notes
        task_text = removed.lstrip("- ").strip()
        self.storage.append_log("task", f"Completed: {task_text}")

        del lines[idx]
        with open(self.tasks_file, "w", encoding="utf-8") as f:
            f.writelines(lines)
        return ""
