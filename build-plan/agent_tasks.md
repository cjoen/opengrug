# Plan: Agent Task Queue + Nightly Processing

## Context
From `build-plan/backlog.md` — "Idle Task Queue". Revised design: instead of an idle-triggered SQLite queue, implement a simple markdown-backed task list for Grug (separate from the user's task list) with a nightly processing loop. Users add items via natural language ("add to your task list: xyz") and Grug works through them overnight.

---

## Part 1: `GrugTaskQueue` Class

New file: `tools/grug_tasks.py`

Reuse the same markdown-backed pattern as `TaskList` in `tools/tasks.py`. Stored at `brain/agent_tasks.md`.

### Class methods
- `add_task(description, priority=None)` — append to file, return confirmation
- `list_tasks()` — return numbered list of pending items
- `complete_task(task_number)` — remove by position, log completion
- `get_pending()` — return list of `(index, description)` for the nightly loop

### Registered tools
- `add_grug_task` — "[GRUG TASKS] Add an item to Grug's own task queue"
- `list_grug_tasks` — "[GRUG TASKS] Show Grug's pending task queue"
- `complete_grug_task` — "[GRUG TASKS] Mark a Grug task as done"

---

## Part 2: Nightly Processing Loop

New function in `workers/background.py`: `nightly_grug_tasks_loop()`

### Behavior
- Runs once per night (same timing pattern as `nightly_summarize_loop`)
- Calls `grug_task_queue.get_pending()` for all pending tasks
- For each task:
  - Calls `router.route_message(description, system_prompt)` with a highly constrained `TaskRunnerGrug` persona. This persona must explicitly omit access to destructive tools (like `remove_instruction` or note overwrites) to prevent hallucination damage while running unsupervised.
  - Captures the output
  - Posts result to configured Slack channel (config: `grug_tasks.results_channel`)
  - Marks the task complete
  - Logs via `storage.append_log("grug-task", ...)`
- Cap: process at most N tasks per night (`grug_tasks.nightly_limit`, default 5)

---

## Part 3: Config

Add to `_DEFAULTS` in `core/config.py`:

```python
"grug_tasks": {
    "file": "agent_tasks.md",
    "nightly_limit": 5,
    "results_channel": None,  # if None, skip Slack posting
}
```

---

## Part 4: Wiring in `app.py`

- Import and instantiate `GrugTaskQueue`
- Register tools
- Start nightly thread alongside existing background loops

---

## Part 5: Tests

New file: `tests/test_grug_tasks.py`
- Add/list/complete/get_pending on `GrugTaskQueue`
- Nightly loop processes tasks and marks them done

---

## Files to create/modify
- `tools/grug_tasks.py` (new) — task queue class + tool registration
- `workers/background.py` — add `nightly_grug_tasks_loop`
- `core/config.py` — add `grug_tasks` defaults
- `app.py` — wire up components + start nightly thread
- `tests/test_grug_tasks.py` (new) — tests

## Verification
1. `python3 -m pytest tests/test_grug_tasks.py`
2. `python3 -m pytest tests/` — no regressions
