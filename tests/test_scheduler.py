"""Tests for ScheduleStore: add, list, one-shot, recurring, validation."""

import os
import tempfile
import pytest
from core.scheduler import ScheduleStore
from core.registry import ToolRegistry
from tools.scheduler_tools import add_schedule as _add_schedule, remind_me as _remind_me


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


def test_remind_me_creates_one_shot_schedule():
    db_path = os.path.join(tempfile.mkdtemp(), "test_schedules.db")
    store = ScheduleStore(db_path)

    result = _remind_me(store, message="Send rent check", when="2026-05-01T10:00:00")
    assert "Reminder set" in result
    assert "Send rent check" in result

    rows = store.list_schedules()
    assert len(rows) == 1
    assert rows[0]["tool_name"] == "reply_to_user"
    assert rows[0]["is_recurring"] is False
    assert rows[0]["description"] == "Reminder: Send rent check"

    os.unlink(db_path)


def test_remind_me_invalid_time():
    db_path = os.path.join(tempfile.mkdtemp(), "test_schedules.db")
    store = ScheduleStore(db_path)

    result = _remind_me(store, message="Test", when="not a time")
    assert "Bad reminder time" in result

    os.unlink(db_path)


# --- retry marker -----------------------------------------------------------

from datetime import datetime, timedelta, timezone
import sqlite3
from core.scheduler import parse_retry_marker


def test_parse_retry_marker_extracts_minutes_and_strips_line():
    assert parse_retry_marker("GRUG_RETRY_IN_MINUTES: 3\nstill running") == (3, "still running")


def test_parse_retry_marker_marker_only():
    assert parse_retry_marker("GRUG_RETRY_IN_MINUTES: 5") == (5, "")


@pytest.mark.parametrize("output", [
    "plain output",
    "",
    "note first\nGRUG_RETRY_IN_MINUTES: 3",   # not the first line
    "GRUG_RETRY_IN_MINUTES: soon",            # not a number
    "GRUG_RETRY_IN_MINUTES: 0",               # out of range
    "GRUG_RETRY_IN_MINUTES: 99999",           # out of range
])
def test_parse_retry_marker_ignores_non_markers(output):
    assert parse_retry_marker(output) == (None, output)


def _one_shot_job(store, **overrides):
    store.add_schedule(
        channel="C1", user="U1", thread_ts="ts1",
        tool_name="get_research", arguments={"job_id": "abc"},
        schedule="2020-01-01T00:00:00", description="check research",
    )
    job = store.get_due()[0]
    job.update(overrides)
    return job


def test_new_schedules_start_with_zero_retries():
    store = ScheduleStore(os.path.join(tempfile.mkdtemp(), "s.db"))
    job = _one_shot_job(store)
    assert job["retries"] == 0


def test_add_retry_creates_one_shot_with_incremented_count():
    store = ScheduleStore(os.path.join(tempfile.mkdtemp(), "s.db"))
    job = _one_shot_job(store)
    store.delete(job["id"])  # the poll loop deletes one-shots on enqueue

    before = datetime.now(tz=timezone.utc)
    assert store.add_retry(job, minutes=3, max_retries=10) is True

    rows = store.list_schedules(channel="C1")
    assert len(rows) == 1
    retry = rows[0]
    assert retry["tool_name"] == "get_research"
    assert retry["arguments"] == {"job_id": "abc"}
    assert retry["thread_ts"] == "ts1"
    assert retry["description"] == "check research"
    assert retry["is_recurring"] is False
    assert retry["retries"] == 1
    due_at = datetime.fromisoformat(retry["next_run_at"])
    assert timedelta(minutes=2, seconds=50) < due_at - before < timedelta(minutes=3, seconds=10)


def test_add_retry_refuses_at_cap():
    store = ScheduleStore(os.path.join(tempfile.mkdtemp(), "s.db"))
    job = _one_shot_job(store, retries=3)
    assert store.add_retry(job, minutes=3, max_retries=3) is False
    assert len(store.list_schedules()) == 1  # only the original row


def test_existing_database_without_retries_column_is_migrated():
    path = os.path.join(tempfile.mkdtemp(), "old.db")
    conn = sqlite3.connect(path)
    conn.executescript("""
        CREATE TABLE schedules (
            id INTEGER PRIMARY KEY AUTOINCREMENT, channel TEXT NOT NULL, user TEXT NOT NULL,
            thread_ts TEXT, tool_name TEXT NOT NULL, arguments TEXT NOT NULL DEFAULT '{}',
            schedule TEXT NOT NULL, next_run_at TEXT NOT NULL, is_recurring INTEGER NOT NULL DEFAULT 0,
            description TEXT, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        INSERT INTO schedules (channel, user, thread_ts, tool_name, schedule, next_run_at)
        VALUES ('C1', 'U1', 'ts1', 'reply_to_user', '2020-01-01T00:00:00', '2020-01-01T00:00:00+00:00');
    """)
    conn.commit()
    conn.close()

    store = ScheduleStore(path)
    assert store.get_due()[0]["retries"] == 0


# --- delivery callback ------------------------------------------------------

from core.orchestrator import MessageReply
from workers.background import _build_scheduled_task


def _delivery_setup(tmp_path, retries=0, recurring=False, max_retries=3):
    store = ScheduleStore(str(tmp_path / "s.db"))
    store.add_schedule(
        channel="C1", user="U1", thread_ts="ts1",
        tool_name="get_research", arguments={"job_id": "abc"},
        schedule="* * * * *" if recurring else "2020-01-01T00:00:00",
        description="check research", retries=retries,
    )
    job = store.list_schedules()[0]
    store.delete(job["id"])  # mimic the poll loop deleting a one-shot on enqueue
    delivered = []
    task = _build_scheduled_task(
        job, deliver_fn=lambda ch, ts, text: delivered.append(text),
        schedule_store=store, max_retries=max_retries,
    )
    return store, task, delivered


def test_retry_requested_reschedules_and_posts_nothing(tmp_path):
    store, task, delivered = _delivery_setup(tmp_path)
    task.metadata["retry_in_minutes"] = 3
    task.on_result(MessageReply(text="[Scheduled: check research] still running"))
    assert delivered == []
    rows = store.list_schedules()
    assert len(rows) == 1 and rows[0]["retries"] == 1


def test_retry_cap_posts_gave_up_message(tmp_path):
    store, task, delivered = _delivery_setup(tmp_path, retries=3, max_retries=3)
    task.metadata["retry_in_minutes"] = 3
    task.on_result(MessageReply(text="[Scheduled: check research] still running"))
    assert store.list_schedules() == []
    assert len(delivered) == 1
    assert "gave up" in delivered[0] and "3" in delivered[0]


def test_no_retry_requested_posts_result(tmp_path):
    store, task, delivered = _delivery_setup(tmp_path)
    task.on_result(MessageReply(text="[Scheduled: check research] the answer"))
    assert delivered == ["[Scheduled: check research] the answer"]
    assert store.list_schedules() == []


def test_recurring_job_ignores_retry_request(tmp_path):
    store, task, delivered = _delivery_setup(tmp_path, recurring=True)
    task.metadata["retry_in_minutes"] = 3
    task.on_result(MessageReply(text="[Scheduled: check research] still running"))
    assert delivered == ["[Scheduled: check research] still running"]
    assert store.list_schedules() == []
