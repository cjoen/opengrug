# Build Plan: Thread-Safe SQLite Stores

**Status:** Ready to implement
**Priority:** High
**Discovered:** 2026-04-23 (code review)
**Estimated scope:** ~15 lines changed across 3 files, no caller changes

---

## Problem

`SessionStore` and `ScheduleStore` share a SQLite connection across threads with **no locking**. `VectorMemory` has a lock but holds it during CPU-heavy ML inference, blocking all queries during indexing. Related sub-bugs:

- `SessionStore.get_or_create` has a TOCTOU race (SELECT → INSERT → SELECT with no transaction)
- `VectorMemory.index_markdown_directory` dual INSERT (`blocks` + `vec_blocks`) is not atomic — crash between them creates permanently orphaned rows

### Threads that access these stores concurrently

| Store | Threads |
|---|---|
| `SessionStore` | Queue workers (process_message), idle_sweep_loop, orchestrator (execute_approved_action, re_infer) |
| `ScheduleStore` | scheduler_poll_loop, scheduler_tools (via queue workers) |
| `VectorMemory` | Background indexer thread, query_memory_raw (via queue workers) |

---

## Plan

### 1. `core/sessions.py`

- Add `import threading`
- Add `self._lock = threading.Lock()` in `__init__`
- Wrap each public method body in `with self._lock:`
- **Fix `get_or_create` TOCTOU**: replace the SELECT → maybe INSERT → SELECT pattern with:
  ```python
  cursor.execute("INSERT OR IGNORE INTO sessions (thread_ts, channel_id) VALUES (?, ?)", ...)
  cursor.execute("UPDATE sessions SET last_active = CURRENT_TIMESTAMP WHERE thread_ts = ?", ...)
  cursor.execute("SELECT * FROM sessions WHERE thread_ts = ?", ...)
  self.conn.commit()
  ```
  All under one lock acquisition — eliminates the race.

Methods to wrap: `get_or_create`, `update_messages`, `set_pending_hitl`, `get_idle_sessions`, `delete_session`, `check_last_active`, `session_count`

### 2. `core/scheduler.py`

- Add `import threading`
- Add `self._lock = threading.Lock()` in `__init__`
- Wrap each public method body in `with self._lock:`

Methods to wrap: `add_schedule`, `get_due`, `advance`, `delete`, `list_schedules`

### 3. `core/vectors.py`

Already has `self._lock`. Only change: restructure `index_markdown_directory` to encode outside the lock.

Current (bad):
```
with self._lock:           # lock acquired here
    for file in files:
        for block in blocks:
            model.encode(block)   # CPU-heavy, blocks all queries
            INSERT blocks
            INSERT vec_blocks
    commit
```

Fixed:
```
# 1. Read files, collect blocks (no lock)
# 2. Filter already-indexed via _query per block (brief lock)
# 3. Encode new blocks via model.encode() (NO lock)
# 4. Acquire lock → re-check uniqueness → insert blocks + vec_blocks → single commit
```

This also fixes the orphaned-row bug since both inserts commit together.

---

## Files Modified

| File | Change |
|---|---|
| `core/sessions.py` | Add lock, wrap methods, fix `get_or_create` |
| `core/scheduler.py` | Add lock, wrap methods |
| `core/vectors.py` | Restructure `index_markdown_directory` only |

**No changes to:** `app.py`, `orchestrator.py`, `background.py`, adapters, tools, or tests.

---

## Verification

```bash
python3 -m pytest tests/ -v
```

Existing tests (`test_sessions.py`, `test_scheduler.py`) must pass unchanged. No vector memory tests exist (requires sentence-transformers).

---

## Future Considerations (not in scope)

- WAL mode — not needed while Python lock already serializes access. Add later if read contention becomes measurable.
- `ThreadSafeDB` base class — if more SQLite stores are added, extract common lock pattern then. Premature now with only 3 stores.
- HITL double-execution race in orchestrator — separate bug, requires compare-and-swap logic in `execute_approved_action`, not just store-level locking.
