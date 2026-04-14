"""Scheduler store for Grug.

SQLite-backed CRUD for cron jobs and one-shot scheduled tasks.
All datetimes stored as UTC ISO 8601 strings.
Cron expressions are evaluated in UTC.
Naive ISO datetimes from the user are interpreted in the configured timezone.
"""

import json
import sqlite3
from datetime import datetime, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
from croniter import croniter


_DDL = """\
CREATE TABLE IF NOT EXISTS schedules (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    channel      TEXT NOT NULL,
    user         TEXT NOT NULL,
    thread_ts    TEXT,
    tool_name    TEXT NOT NULL,
    arguments    TEXT NOT NULL DEFAULT '{}',
    schedule     TEXT NOT NULL,
    next_run_at  TEXT NOT NULL,
    is_recurring INTEGER NOT NULL DEFAULT 0,
    description  TEXT,
    created_at   TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_schedules_next_run ON schedules(next_run_at);
"""


class ScheduleStore:
    """SQLite CRUD for scheduled tasks and reminders."""

    def __init__(self, db_path: str, timezone_str: str = "UTC"):
        self.db_path = db_path
        try:
            self.tz = ZoneInfo(timezone_str)
        except ZoneInfoNotFoundError:
            self.tz = ZoneInfo("UTC")
        self.conn = sqlite3.connect(db_path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(_DDL)
        self.conn.commit()

    def _now_utc(self) -> datetime:
        return datetime.now(tz=timezone.utc)

    def _to_utc(self, dt: datetime) -> datetime:
        """Convert to UTC. Naive datetimes are assumed to be in self.tz."""
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=self.tz)
        return dt.astimezone(timezone.utc)

    def add_schedule(self, channel: str, user: str, thread_ts: str,
                     tool_name: str, arguments: dict, schedule: str,
                     description: str = None) -> int:
        """Insert a new schedule. Returns the row id.

        ``schedule`` is either a cron expression (evaluated in UTC) or an
        ISO 8601 datetime (naive datetimes interpreted in the configured timezone).
        """
        is_recurring, next_run = self._parse_schedule(schedule)
        cursor = self.conn.execute(
            "INSERT INTO schedules (channel, user, thread_ts, tool_name, arguments, "
            "schedule, next_run_at, is_recurring, description) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (channel, user, thread_ts, tool_name, json.dumps(arguments),
             schedule, next_run, int(is_recurring), description),
        )
        self.conn.commit()
        return cursor.lastrowid

    def get_due(self) -> list:
        """Return all rows where next_run_at <= now (UTC)."""
        now = self._now_utc().isoformat()
        cursor = self.conn.execute(
            "SELECT * FROM schedules WHERE next_run_at <= ?", (now,)
        )
        return [self._row_to_dict(row) for row in cursor.fetchall()]

    def advance(self, schedule_id: int, cron_expr: str):
        """Compute next_run_at from cron expression in UTC."""
        cursor = self.conn.execute(
            "SELECT next_run_at FROM schedules WHERE id = ?", (schedule_id,)
        )
        row = cursor.fetchone()
        if not row:
            return
        current_utc = datetime.fromisoformat(row["next_run_at"]).replace(tzinfo=timezone.utc)
        new_next = croniter(cron_expr, current_utc).get_next(datetime).replace(tzinfo=timezone.utc).isoformat()
        self.conn.execute(
            "UPDATE schedules SET next_run_at = ? WHERE id = ?",
            (new_next, schedule_id),
        )
        self.conn.commit()

    def delete(self, schedule_id: int):
        """Delete a schedule by id."""
        self.conn.execute("DELETE FROM schedules WHERE id = ?", (schedule_id,))
        self.conn.commit()

    def list_schedules(self, channel: str = None, user: str = None) -> list:
        """List active schedules, optionally filtered by channel or user."""
        query = "SELECT * FROM schedules"
        params = []
        conditions = []
        if channel:
            conditions.append("channel = ?")
            params.append(channel)
        if user:
            conditions.append("user = ?")
            params.append(user)
        if conditions:
            query += " WHERE " + " AND ".join(conditions)
        query += " ORDER BY next_run_at"
        cursor = self.conn.execute(query, params)
        return [self._row_to_dict(row) for row in cursor.fetchall()]

    def _parse_schedule(self, schedule: str) -> tuple:
        """Return (is_recurring, next_run_at_utc_iso).

        - ISO datetime: naive → interpreted in configured tz, then UTC.
                        tz-aware → converted directly to UTC.
        - Cron expression: evaluated from now in UTC.
        """
        try:
            dt = datetime.fromisoformat(schedule)
            return False, self._to_utc(dt).isoformat()
        except ValueError:
            pass

        if not croniter.is_valid(schedule):
            raise ValueError(
                f"Invalid schedule: {schedule!r} (not a valid cron expression or ISO datetime)"
            )
        next_run = croniter(schedule, self._now_utc()).get_next(datetime).replace(tzinfo=timezone.utc)
        return True, next_run.isoformat()

    @staticmethod
    def _row_to_dict(row: sqlite3.Row) -> dict:
        return {
            "id": row["id"],
            "channel": row["channel"],
            "user": row["user"],
            "thread_ts": row["thread_ts"],
            "tool_name": row["tool_name"],
            "arguments": json.loads(row["arguments"]),
            "schedule": row["schedule"],
            "next_run_at": row["next_run_at"],
            "is_recurring": bool(row["is_recurring"]),
            "description": row["description"],
            "created_at": row["created_at"],
        }

    def __del__(self):
        if hasattr(self, "conn"):
            self.conn.close()
