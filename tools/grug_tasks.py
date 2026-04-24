"""Grug's own task queue — markdown-backed list for autonomous work.

Separate from the user's task list. Tasks are added via natural language
and processed by the nightly worker loop.
"""

import os
import threading


def register_tools(registry, grug_task_queue, storage):
    """Register GRUG TASKS tools."""
    registry.register_python_tool(
        name="add_grug_task",
        schema={
            "description": "[GRUG TASKS] Add an item to Grug's own task queue for overnight processing.",
            "type": "object",
            "properties": {
                "description": {"type": "string", "description": "What Grug should work on"}
            },
            "required": ["description"]
        },
        func=grug_task_queue.add_task,
        category="GRUG TASKS",
        friendly_name="Add a Grug task"
    )
    registry.register_python_tool(
        name="list_grug_tasks",
        schema={
            "description": "[GRUG TASKS] Show Grug's pending task queue.",
            "type": "object",
            "properties": {}
        },
        func=grug_task_queue.list_tasks,
        category="GRUG TASKS",
        friendly_name="List Grug tasks"
    )
    registry.register_python_tool(
        name="complete_grug_task",
        schema={
            "description": "[GRUG TASKS] Mark a Grug task as done by number.",
            "type": "object",
            "properties": {
                "task_number": {"type": "integer", "description": "Position number from list"}
            },
            "required": ["task_number"]
        },
        func=grug_task_queue.complete_task,
        category="GRUG TASKS",
        friendly_name="Complete a Grug task"
    )


class GrugTaskQueue:
    """Markdown-backed task queue for Grug's autonomous work."""

    def __init__(self, tasks_file, storage):
        self.tasks_file = tasks_file
        self.storage = storage
        self._lock = threading.Lock()

    def _ensure(self):
        if not os.path.exists(self.tasks_file):
            os.makedirs(os.path.dirname(self.tasks_file), exist_ok=True)
            with open(self.tasks_file, "w", encoding="utf-8") as f:
                f.write("# Grug Task Queue\n\n")

    def _parse_tasks(self):
        """Return (header_lines, task_lines) where task_lines are raw line strings.

        All non-task lines (blanks, comments, sub-items) are preserved by
        attaching them to the preceding task or the header.
        """
        self._ensure()
        with open(self.tasks_file, "r", encoding="utf-8") as f:
            lines = f.readlines()
        header = []
        tasks = []  # list of (task_line, [trailing_lines])
        for line in lines:
            if line.strip().startswith("- "):
                tasks.append((line, []))
            elif tasks:
                tasks[-1][1].append(line)
            else:
                header.append(line)
        return header, tasks

    def _write(self, header, tasks):
        with open(self.tasks_file, "w", encoding="utf-8") as f:
            f.writelines(header)
            for task_line, trailing in tasks:
                f.write(task_line)
                f.writelines(trailing)

    def add_task(self, description):
        with self._lock:
            header, tasks = self._parse_tasks()
            tasks.append((f"- {description.strip()}\n", []))
            self._write(header, tasks)
        return f"Grug task added: {description.strip()}"

    def list_tasks(self, **_kwargs):
        with self._lock:
            _, tasks = self._parse_tasks()
        if not tasks:
            return "Grug task queue empty. Grug rest."
        lines = []
        for i, (task_line, _trailing) in enumerate(tasks, 1):
            lines.append(f"#{i}: {task_line.strip()}")
        return "\n".join(lines)

    def complete_task(self, task_number):
        with self._lock:
            header, tasks = self._parse_tasks()
            idx = int(task_number) - 1
            if idx < 0 or idx >= len(tasks):
                return f"No Grug task #{task_number} found."
            removed = tasks[idx][0].strip().lstrip("- ").strip()
            del tasks[idx]
            self._write(header, tasks)
        self.storage.append_log("grug-task", f"Completed: {removed}")
        return f"Grug task #{task_number} completed: {removed}"

    def get_pending(self):
        """Return list of (index, description) for the nightly loop."""
        with self._lock:
            _, tasks = self._parse_tasks()
        result = []
        for i, (task_line, _trailing) in enumerate(tasks, 1):
            desc = task_line.strip().lstrip("- ").strip()
            if desc:
                result.append((i, desc))
        return result
