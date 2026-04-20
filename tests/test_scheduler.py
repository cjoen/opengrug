"""Tests for ScheduleStore: add, list, one-shot, recurring, validation."""

import os
import tempfile
import pytest
from core.scheduler import ScheduleStore
from core.registry import ToolRegistry
from tools.scheduler_tools import add_schedule as _add_schedule


def test_schedule_store_add_and_list():
    db_path = os.path.join(tempfile.mkdtemp(), "test_schedules.db")
    store = ScheduleStore(db_path)

    row_id = store.add_schedule(
        channel="C1", user="U1", thread_ts="ts1",
        tool_name="reply_to_user",
        arguments={"message": "hello"},
        schedule="* * * * *",
        description="every minute test",
    )
    assert row_id is not None

    rows = store.list_schedules(channel="C1")
    assert len(rows) == 1
    assert rows[0]["tool_name"] == "reply_to_user"
    assert rows[0]["is_recurring"] is True
    assert rows[0]["description"] == "every minute test"

    os.unlink(db_path)


def test_schedule_store_one_shot_lifecycle():
    db_path = os.path.join(tempfile.mkdtemp(), "test_schedules.db")
    store = ScheduleStore(db_path)

    store.add_schedule(
        channel="C1", user="U1", thread_ts="ts1",
        tool_name="reply_to_user",
        arguments={"message": "once"},
        schedule="2020-01-01T00:00:00",
        description="past one-shot",
    )

    due = store.get_due()
    assert len(due) == 1
    assert due[0]["is_recurring"] is False

    store.delete(due[0]["id"])
    due2 = store.get_due()
    assert len(due2) == 0

    os.unlink(db_path)


def test_schedule_store_recurring_advances():
    db_path = os.path.join(tempfile.mkdtemp(), "test_schedules.db")
    store = ScheduleStore(db_path)

    row_id = store.add_schedule(
        channel="C1", user="U1", thread_ts="ts1",
        tool_name="reply_to_user",
        arguments={},
        schedule="0 9 * * *",
        description="daily 9am",
    )

    rows_before = store.list_schedules()
    old_next = rows_before[0]["next_run_at"]

    store.advance(row_id, "0 9 * * *")

    rows_after = store.list_schedules()
    new_next = rows_after[0]["next_run_at"]

    assert new_next > old_next

    os.unlink(db_path)


def test_add_schedule_tool_validates_tool_name():
    db_path = os.path.join(tempfile.mkdtemp(), "test_schedules.db")
    store = ScheduleStore(db_path)
    registry = ToolRegistry()

    result = _add_schedule(store, registry, tool_name="nonexistent_tool", schedule="* * * * *")
    assert "not know tool" in result

    os.unlink(db_path)


def test_schedule_store_invalid_schedule_rejected():
    db_path = os.path.join(tempfile.mkdtemp(), "test_schedules.db")
    store = ScheduleStore(db_path)

    with pytest.raises(ValueError, match="Invalid schedule"):
        store.add_schedule(
            channel="C1", user="U1", thread_ts="ts1",
            tool_name="reply_to_user",
            arguments={},
            schedule="not a valid schedule",
        )

    os.unlink(db_path)
