"""Tests for TaskList — markdown-backed task list with ephemeral position numbers."""

import os
import pytest
from core.storage import GrugStorage
from tools.tasks import TaskList


@pytest.fixture
def task_env(tmp_path):
    storage = GrugStorage(base_dir=str(tmp_path))
    tasks_file = os.path.join(str(tmp_path), "tasks.md")
    task_list = TaskList(tasks_file=tasks_file, storage=storage)
    return task_list, tasks_file


class TestAdd:
    def test_returns_confirmation(self, task_env):
        task_list, _ = task_env
        result = task_list.add_task("Fix login", priority="high")
        assert "Task added" in result
        assert "Fix login" in result
        assert "[high]" in result

    def test_no_priority_confirmation(self, task_env):
        task_list, _ = task_env
        result = task_list.add_task("Do a thing")
        assert "Task added" in result
        assert "[" not in result


class TestList:
    def test_empty(self, task_env):
        task_list, _ = task_env
        result = task_list.list_tasks()
        assert "No tasks" in result

    def test_position_numbers(self, task_env):
        task_list, _ = task_env
        task_list.add_task("Low task", priority="low")
        task_list.add_task("High task", priority="high")
        result = task_list.list_tasks()
        lines = result.strip().split("\n")
        assert lines[0].startswith("#1:")
        assert "High task" in lines[0]
        assert lines[1].startswith("#2:")
        assert "Low task" in lines[1]

    def test_sorted_by_priority(self, task_env):
        task_list, _ = task_env
        task_list.add_task("Low", priority="low")
        task_list.add_task("High", priority="high")
        task_list.add_task("Medium", priority="medium")
        result = task_list.list_tasks()
        lines = result.strip().split("\n")
        assert "High" in lines[0]
        assert "Medium" in lines[1]
        assert "Low" in lines[2]


class TestComplete:
    def test_removes_task(self, task_env):
        task_list, _ = task_env
        task_list.add_task("To complete", priority="high")
        result = task_list.complete_task(1)
        assert "completed" in result.lower()
        assert "To complete" in result
        assert "No tasks" in task_list.list_tasks()

    def test_nonexistent_returns_error(self, task_env):
        task_list, _ = task_env
        result = task_list.complete_task(99)
        assert "No task #99" in result

    def test_file_stays_clean(self, task_env):
        """The markdown file should contain no position numbers — those are display-only."""
        task_list, tasks_file = task_env
        task_list.add_task("First", priority="high")
        task_list.add_task("Second", priority="low")
        with open(tasks_file, "r") as f:
            content = f.read()
        assert "#1" not in content
        assert "#2" not in content
        assert "- First [high]" in content
        assert "- Second [low]" in content
