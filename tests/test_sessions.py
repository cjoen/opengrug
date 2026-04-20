"""Tests for SessionStore: CRUD, last_active, HITL persistence."""

import os
import tempfile
from core.sessions import SessionStore


def test_session_store_crud():
    db_path = os.path.join(tempfile.mkdtemp(), "test_sessions.db")
    store = SessionStore(db_path)

    s = store.get_or_create("1234.5678", "C_TEST")
    assert s["thread_ts"] == "1234.5678"
    assert s["messages"] == []
    assert s["pending_hitl"] is None

    store.update_messages("1234.5678", [{"role": "user", "content": "hello"}])
    s = store.get_or_create("1234.5678", "C_TEST")
    assert len(s["messages"]) == 1
    assert s["messages"][0]["content"] == "hello"

    store.set_pending_hitl("1234.5678", {"tool_name": "add_note", "arguments": {"content": "test"}})
    s = store.get_or_create("1234.5678", "C_TEST")
    assert s["pending_hitl"]["tool_name"] == "add_note"

    store.set_pending_hitl("1234.5678", None)
    s = store.get_or_create("1234.5678", "C_TEST")
    assert s["pending_hitl"] is None

    store.delete_session("1234.5678")
    s = store.get_or_create("1234.5678", "C_TEST")
    assert s["messages"] == []

    os.unlink(db_path)


def test_session_store_check_last_active():
    db_path = os.path.join(tempfile.mkdtemp(), "test_sessions.db")
    store = SessionStore(db_path)

    store.get_or_create("ts1", "C1")
    ts = store.check_last_active("ts1")
    assert ts is not None
    assert store.check_last_active("nonexistent") is None

    os.unlink(db_path)


def test_hitl_persists_across_restart():
    db_path = os.path.join(tempfile.mkdtemp(), "test_persist.db")

    store1 = SessionStore(db_path)
    store1.get_or_create("ts1", "C1")
    store1.set_pending_hitl("ts1", {"tool_name": "add_task", "arguments": {"title": "test"}})
    del store1

    store2 = SessionStore(db_path)
    s = store2.get_or_create("ts1", "C1")
    assert s["pending_hitl"] is not None
    assert s["pending_hitl"]["tool_name"] == "add_task"

    os.unlink(db_path)
