import os
import pytest
from unittest.mock import MagicMock
from tools.grug_tasks import GrugTaskQueue


def _make_queue(tmp_path):
    storage = MagicMock()
    storage.append_log = MagicMock()
    tasks_file = str(tmp_path / "agent_tasks.md")
    return GrugTaskQueue(tasks_file=tasks_file, storage=storage), storage


def test_add_and_list(tmp_path):
    queue, _ = _make_queue(tmp_path)
    queue.add_task("Buy mushrooms")
    queue.add_task("Sharpen axe")
    result = queue.list_tasks()
    assert "#1: - Buy mushrooms" in result
    assert "#2: - Sharpen axe" in result


def test_list_empty(tmp_path):
    queue, _ = _make_queue(tmp_path)
    result = queue.list_tasks()
    assert "Grug task queue empty" in result


def test_complete_task(tmp_path):
    queue, storage = _make_queue(tmp_path)
    queue.add_task("Gather berries")
    queue.add_task("Make fire")
    result = queue.complete_task(1)
    assert "Gather berries" in result
    remaining = queue.list_tasks()
    assert "Gather berries" not in remaining
    assert "Make fire" in remaining
    storage.append_log.assert_called_once_with("grug-task", "Completed: Gather berries")


def test_complete_invalid_number(tmp_path):
    queue, _ = _make_queue(tmp_path)
    queue.add_task("Only task")
    result = queue.complete_task(99)
    assert "No Grug task #99 found" in result


def test_get_pending(tmp_path):
    queue, _ = _make_queue(tmp_path)
    queue.add_task("Task alpha")
    queue.add_task("Task beta")
    queue.add_task("Task gamma")
    pending = queue.get_pending()
    assert pending == [(1, "Task alpha"), (2, "Task beta"), (3, "Task gamma")]


def test_complete_shifts_numbers(tmp_path):
    queue, _ = _make_queue(tmp_path)
    queue.add_task("A")
    queue.add_task("B")
    queue.add_task("C")
    queue.complete_task(1)
    pending = queue.get_pending()
    assert pending == [(1, "B"), (2, "C")]
