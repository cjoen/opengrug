# Phase 5.4: Migration, Observability & Hardening

## Objective
Migrate existing background workers to the new Task architecture, add the Dead Letter Queue, health monitoring, operator tools, and update all documentation.

## Design Patterns
- **Facade Pattern** — `system_utils.py` exposes simple CLI commands hiding internal complexity.
- **Observer Pattern** — The health monitor observes worker and queue state without coupling to their internals.
- **SRP** — DLQ is its own module; monitoring is its own thread; operator tools are their own script.

## Changes

### Background Worker Migration

#### [MODIFY] `workers/background.py`

**`scheduler_poll_loop`** — Full rewrite.
- Remove `registry` and `slack_client` parameters (currently bypasses the entire architecture).
- New signature: `scheduler_poll_loop(schedule_store, task_queue, config)`.
- When a due schedule is found, create a `Task` with the pre-specified tool/arguments and enqueue it. The task's `on_result` callback handles Slack delivery via the adapter, not via direct `slack_client.chat_postMessage()`.
- Determine priority: scheduled reminders (user-facing) → `URGENT`. Cron jobs (system maintenance) → `BACKGROUND`.

**`nightly_grug_tasks_loop`** — Refactor to Task producer.
- New signature: `nightly_grug_tasks_loop(grug_task_queue, task_queue, storage, config)`.
- Create `BACKGROUND` Tasks for each item and enqueue them instead of calling `orchestrator.process_message()`.
- The off-hours window in the priority queue naturally handles scheduling.

**`boot_summarize`, `idle_sweep_loop`, `nightly_summarize_loop`** — No signature changes.
- These already receive `Summarizer`, which now holds a `ChatWorker` reference (from Phase 5.1).
- The `ChatWorker`'s semaphore ensures GPU concurrency is respected. No other changes needed.

### Dead Letter Queue

#### [NEW] `core/dlq.py`
```python
class DeadLetterQueue:
    """Append-only failure log backed by brain/failed_tasks.md."""
    def __init__(self, file_path: str):
        ...
    def add(self, task: Task, error: str, traceback: str):
        """Serialize failed task state to markdown."""
    def list_failed(self) -> list[dict]:
        """Parse markdown back into structured dicts for retry."""
    def remove(self, task_id: str):
        """Remove an entry after successful retry."""
```

Format in `brain/failed_tasks.md`:
```markdown
## [task_id] — [timestamp]
- **Agent:** researcher
- **Priority:** BACKGROUND  
- **Context:** [distilled context]
- **Error:** [error message]
- **Traceback:** [stack trace in code block]
```

#### [MODIFY] `core/queue.py`
- On `Task` state transition to `FAILED`, route to `DeadLetterQueue.add()`.
- On `Task` state transition to `CANCELLED` (user cancel or timeout), route to `DeadLetterQueue.add()` with reason (`user_cancelled` | `timeout`).
- Configurable retry count before DLQ routing (default: 1 retry, then DLQ).

### Health Monitoring

#### [NEW] `workers/monitor.py`
Plain Python thread (not an LLM Agent):
```python
def health_monitor_loop(worker_pool, task_queue, dlq, alert_callback, config):
    """Periodic health check. Writes brain/system_health.md, alerts on critical failures."""
```
- Poll each worker's `health_check()` method.
- Report queue depth, DLQ size, worker status.
- Write results to `brain/system_health.md` (Obsidian-friendly dashboard).
- If a worker is unreachable or DLQ exceeds threshold, call `alert_callback(message)` which the adapter wires to the appropriate notification channel (e.g., Slack DM). The monitor never imports or calls Slack directly.

### Operator Tools (Grug Tools)

#### [NEW] `tools/operator.py`
Operator actions registered as Grug tools (callable from Slack), replacing the original `scripts/system_utils.py` CLI approach:

| Tool | Description | Destructive? |
|:---|:---|:---|
| `queue_status` | Report worker health, queue depth, DLQ size | No |
| `retry_dlq` | Re-enqueue all DLQ items | No |
| `clear_dlq` | Purge `brain/failed_tasks.md` | Yes (HITL) |
| `drain_queue` | Cancel all `BACKGROUND` tasks | Yes (HITL) |
| `cancel_task` | Cancel a specific task by ID | Yes (HITL) |

All tools receive `task_queue`, `dlq`, and `worker_pool` via closure injection at registration time. Destructive tools are gated behind HITL approval.

### Documentation

#### [MODIFY] `ai-context.md`
Full rewrite to reflect the new architecture:
- Update Core File Structure to include `core/agents.py`, `core/task.py`, `core/dlq.py`, `core/workers.py`.
- Document the Dispatcher → Agent flow.
- Update Tool Categories to reflect per-agent scoping.
- Document the Worker/Agent/RAG config structure.
- Update Core Rules (replace "Single LLM client" with Worker abstraction, update queue docs).
- Add operator tools section referencing `scripts/system_utils.py`.

#### [MODIFY] `roadmap.md`
Update Phase 5 description and sub-phase links to match new file names.

#### [DELETE] `build-plan/phase_5_multi_agent_plan.md`
Already deprecated. Remove to avoid confusion.

### Final Wiring

#### [MODIFY] `app.py`
- Add `DLQ` initialization and injection into the queue.
- Start `health_monitor_loop` daemon thread.
- Remove `slack_client` from `scheduler_poll_loop` call (it no longer needs it).
- Update `nightly_grug_tasks_loop` to pass `task_queue` instead of `orchestrator`.

## Verification
1. `pytest tests/` — all tests pass.
2. New `tests/test_dlq.py` — verify write/read/remove cycle, markdown format parsing.
3. New `tests/test_monitor.py` — verify health report generation with mocked workers.
4. Manual: trigger a task failure → verify it appears in `brain/failed_tasks.md` → run `system_utils.py retry-dlq` → verify re-enqueue.
5. Manual: kill Ollama → verify health monitor writes degraded status to `brain/system_health.md` and sends Slack alert.
6. End-to-end: full Slack conversation → background task → scheduled reminder → all routed through new architecture.
