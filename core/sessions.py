"""Session store for Grug conversation threads.

SQLite-backed CRUD for Slack thread sessions, stored in a dedicated
``sessions.db`` (separate from the VSS vector cache ``memory.db``).
"""

import json
import sqlite3
from datetime import datetime, timedelta
from typing import Optional


_DDL = """\
CREATE TABLE IF NOT EXISTS sessions (
    thread_ts   TEXT PRIMARY KEY,
    channel_id  TEXT NOT NULL,
    messages    TEXT NOT NULL DEFAULT '[]',
    pending_hitl TEXT DEFAULT NULL,
    last_active TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_sessions_last_active ON sessions(last_active);
"""


class SessionStore:
    """SQLite CRUD for Slack thread conversation sessions."""

    def __init__(self, db_path: str):
        self.db_path = db_path
        self.conn = sqlite3.connect(db_path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(_DDL)
        self.conn.commit()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def get_or_create(self, thread_ts: str, channel_id: str) -> dict:
        """Fetch session by ``thread_ts``, creating a new row if missing.

        Always updates ``last_active`` on access.

        Returns::

            {
                "thread_ts": str,
                "channel_id": str,
                "messages": list[dict],
                "pending_hitl": dict | None,
            }
        """
        cursor = self.conn.cursor()

        # Try to fetch existing
        cursor.execute("SELECT * FROM sessions WHERE thread_ts = ?", (thread_ts,))
        row = cursor.fetchone()

        if row is None:
            # Insert new session
            cursor.execute(
                "INSERT INTO sessions (thread_ts, channel_id) VALUES (?, ?)",
                (thread_ts, channel_id),
            )
            self.conn.commit()
            cursor.execute("SELECT * FROM sessions WHERE thread_ts = ?", (thread_ts,))
            row = cursor.fetchone()
        else:
            # Touch last_active
            cursor.execute(
                "UPDATE sessions SET last_active = CURRENT_TIMESTAMP WHERE thread_ts = ?",
                (thread_ts,),
            )
            self.conn.commit()

        return self._row_to_dict(row)

    def update_messages(self, thread_ts: str, messages: list[dict]):
        """Serialize ``messages`` to JSON, update the row, and touch ``last_active``."""
        self.conn.execute(
            "UPDATE sessions SET messages = ?, last_active = CURRENT_TIMESTAMP WHERE thread_ts = ?",
            (json.dumps(messages), thread_ts),
        )
        self.conn.commit()

    def set_pending_hitl(self, thread_ts: str, hitl_data: Optional[dict]):
        """Store or clear the pending HITL action.

        Does NOT update ``last_active`` — HITL state is system state, not user activity.
        """
        value = json.dumps(hitl_data) if hitl_data is not None else None
        self.conn.execute(
            "UPDATE sessions SET pending_hitl = ? WHERE thread_ts = ?",
            (value, thread_ts),
        )
        self.conn.commit()

    def get_idle_sessions(self, idle_hours: float) -> list[dict]:
        """Return all sessions whose ``last_active`` is older than ``idle_hours`` ago."""
        cutoff = (datetime.now() - timedelta(hours=idle_hours)).strftime(
            "%Y-%m-%d %H:%M:%S"
        )
        cursor = self.conn.execute(
            "SELECT * FROM sessions WHERE last_active < ?", (cutoff,)
        )
        return [self._row_to_dict(row) for row in cursor.fetchall()]

    def delete_session(self, thread_ts: str):
        """Delete the session row."""
        self.conn.execute("DELETE FROM sessions WHERE thread_ts = ?", (thread_ts,))
        self.conn.commit()

    def check_last_active(self, thread_ts: str) -> Optional[str]:
        """Return the current ``last_active`` timestamp string, or ``None`` if missing.

        Used for the optimistic concurrency check before deleting idle sessions.
        """
        cursor = self.conn.execute(
            "SELECT last_active FROM sessions WHERE thread_ts = ?", (thread_ts,)
        )
        row = cursor.fetchone()
        return row["last_active"] if row else None

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _row_to_dict(row: sqlite3.Row) -> dict:
        """Deserialize a SQLite Row into a plain dict with parsed JSON fields."""
        pending_raw = row["pending_hitl"]
        return {
            "thread_ts": row["thread_ts"],
            "channel_id": row["channel_id"],
            "messages": json.loads(row["messages"]),
            "pending_hitl": json.loads(pending_raw) if pending_raw else None,
        }

    def __del__(self):
        if hasattr(self, "conn"):
            self.conn.close()
