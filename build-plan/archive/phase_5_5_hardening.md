# Phase 5.5: Hardening & Cleanup

## Objective
Address bugs, architectural rough edges, and test gaps surfaced by the Phase 5.3 code review against `sub_agent_router_prd.md`. Items already covered by `phase_5_4_migration_observability.md` (DLQ, scheduler/nightly loop migration, operator tools incl. `cancel_task`, health monitor) are explicitly out of scope here.

## Design Patterns
- **SRP** — Split orchestrator chat paths and centralize router threadlocal lifecycle.
- **Liskov / DRY** — Collapse near-duplicate `_run_chat_agent` / `_run_chat_legacy`.
- **Defensive ordering** — Heap iteration is not creation-ordered; sort batches before dispatch.

## Changes

### Bugs

#### [FIX] URGENT batch FIFO ordering — `core/task_queue.py`
`_take_next_batch` collects same-session URGENT tasks via `for t in self._heap`, but heap iteration order is not insertion order, so batches can be processed out of submission order. After collection, sort `batch` by `created_at` so the worker sees the user's messages in the order they were sent.
- New test: `tests/test_task_queue.py::test_urgent_batch_preserves_fifo_within_session` — enqueue 5 same-session URGENTs with monotonically increasing `created_at`, assert batch order matches enqueue order.

#### [FIX] Cancelled mid-loop tasks transition to wrong state — `core/orchestrator.py`
When the router returns a `Task cancelled` `ToolExecutionResult` because `cancel_event` was set, `_run_task` currently completes normally: the task transitions `RUNNING → COMPLETED`, `"Task cancelled"` is appended to session history, and `on_result` fires with a `MessageReply` containing that string.
- After the StepLoop returns, check `task.cancel_event.is_set()`.
- If set: transition to `CANCELLED` (not `COMPLETED`), skip the session-history append, fire `on_result` with an `ErrorReply("Task cancelled")` (or equivalent), and do not write the cancel sentinel into chat history.
- New test: `tests/test_orchestrator_queue.py::test_cancelled_task_does_not_pollute_history` — enqueue a task, set `cancel_event` before dispatch, assert final state is `CANCELLED` and session store has no `"Task cancelled"` assistant turn.

#### [FIX] `_session_locks` unbounded growth — `core/task_queue.py`
`_session_locks[session_id]` is created on every batch and never reaped, so long-running processes accumulate one Lock per Slack thread forever.
- Cheapest fix: drop the lock entry in `_run_batch`'s `finally` block when the heap has no more pending tasks for that session AND the lock is currently free. Use `lock.acquire(blocking=False)` / `release()` to verify it's idle, then `pop` under `self._lock`.
- Alternative if races prove tricky: time-bucket reap during `pending_count()` calls or as part of `idle_sweep_loop`. Pick whichever measures cleaner.
- New test: `tests/test_task_queue.py::test_session_lock_reaped_after_drain` — process two batches for the same session, assert `len(q._session_locks) == 0` afterward.

### Architectural

#### [REFACTOR] Move Dispatcher LLM call off the ingress thread — `core/orchestrator.py`
Today, `enqueue()` runs the Dispatcher LLM call inline on the Slack handler thread. A slow local model can block Slack acks. Two options:
1. **Two-stage queue** — enqueue a `DISPATCH` Task (highest priority, no session affinity) whose worker runs the Dispatcher and enqueues the resulting downstream Task. Keeps the existing queue plumbing.
2. **Dedicated dispatcher thread** — small `queue.Queue` consumed by one thread; same end state, less invasive.

Pick option 2 for the MVP. If the inline cost turns out to be acceptable in practice (<200ms p95), document that decision in the file and close the item without code change.
- New test: `tests/test_orchestrator_queue.py::test_enqueue_returns_before_dispatcher_completes` — stub Dispatcher to block on an `Event`, assert `orchestrator.enqueue()` returns immediately, then release the event and assert the task lands.

#### [REFACTOR] Collapse `_run_chat_agent` / `_run_chat_legacy` — `core/orchestrator.py`
The legacy path exists only because we wired the new agent container before deleting the old code path. Confirm `_run_chat_legacy` has no remaining callers in tests or other modules, then delete it. If both paths are still reachable, fold the differences into a single function with a `container | None` branch.

#### [REFACTOR] Threadlocal context manager — `core/router.py`
`_dispatch_*` and `_schedule_*` threadlocal setup/teardown is duplicated in two orchestrator code paths and is easy to leak on exception.
- Add `router.request_state(session_id, user_id, on_result) -> contextmanager` that sets all current threadlocals on enter and unconditionally clears them on exit.
- Replace open-coded setup in `core/orchestrator.py` with `with router.request_state(...):`.
- Test: `tests/test_router.py::test_request_state_cleared_on_exception`.

### Config

#### [ADD] `queue.expert_max_steps` default — `core/config.py`
`_run_expert_agent` reads `config.queue.expert_max_steps` but `_DEFAULTS["queue"]` doesn't define it, so a barebones config crashes on first expert dispatch. Add `"expert_max_steps": 12` (or whatever current callsite assumes) to `_DEFAULTS["queue"]`.

### Test Coverage Gaps

- `tests/test_orchestrator_queue.py::test_background_gate_blocks_dispatch_end_to_end` — boot orchestrator with `background_runnable=lambda: False`, enqueue a BG task, assert it stays `QUEUED` while an URGENT for the same session enqueued afterward gets processed first.
- (Covered above) FIFO-within-batch test, cancelled→CANCELLED e2e test, request-state cleanup on exception.

## Out of Scope (handled in 5.4)
- Dead Letter Queue (`core/dlq.py`, FAILED/CANCELLED routing)
- `cancel_task` / `queue_status` / `retry_dlq` / `clear_dlq` / `drain_queue` operator tools
- `scheduler_poll_loop` and `nightly_grug_tasks_loop` migration to TaskQueue
- Health monitor + `brain/system_health.md`
- Multi-tier BACKGROUND routing — defer until a second worker tier actually exists; revisit when the worker pool grows.

## Verification
1. `python3 -m pytest tests/` — all green, including the four new tests above.
2. Manual: send three rapid same-thread Slack messages → confirm responses arrive in send-order (FIFO bug fix).
3. Manual: cancel an in-flight task via the 5.4 `cancel_task` tool → confirm session history shows no `"Task cancelled"` assistant turn and the task's terminal state is `CANCELLED`.
