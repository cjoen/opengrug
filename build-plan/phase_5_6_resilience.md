# Phase 5.6 — Resilience Improvements

**Status:** Complete
**Goal:** Close the seven follow-ups from the 5.4/5.5 code review. No new user-facing features — only resilience under failure.
**Guiding principles:** KISS first. Each item should be the minimum change that closes its risk. Reach for an abstraction only when two callers actually share behavior. Prefer touching one file over three.

## Scope (the seven items)

1. Retry backoff
2. Dispatch worker pool (replace single dispatch thread)
3. `WorkerHealth` protocol (replace string heuristics in monitor)
4. Worker circuit breaker (skip unhealthy workers)
5. Scheduled-tool HITL policy
6. Retry traceability (preserve task identity through retries)
7. Retry-success integration test

Out of scope: any new agent, tool, or backend. No queue persistence. No metrics export.

---

## 1. Retry backoff

**Problem.** `core/task_queue.py:_try_retry` re-enqueues immediately, hammering a flaky backend.

**Simplest viable fix.** Don't touch the heap or add a `not_before` field. Use the watchdog pattern that's already in this file: schedule the re-enqueue with `threading.Timer`.

```python
def _try_retry(self, task: Task) -> bool:
    retries = task.metadata.get("_retries", 0)
    if retries >= self._max_retries:
        return False
    delay = min(2 ** retries, 30)  # 1s, 2s, 4s, …, capped at 30s
    clone = Task(...)  # same as today, plus root_task_id (see §6)
    threading.Timer(delay, lambda: self.enqueue(clone)).start()
    return True
```

**Why this over `Task.not_before`.** A `not_before` field requires changing heap ordering or peek-and-wait logic. Timer-based delay reuses an existing pattern, adds no new state, and keeps `_take_next_batch` unchanged. The only cost: a few daemon Timers in flight during outages. Acceptable.

**Tests** (`tests/test_task_queue.py`):
- Retry happens after the configured delay (use `monkeypatch` on `threading.Timer` to capture the delay value).
- Cap is honoured: 5th attempt still uses ≤30s.

---

## 2. Dispatch worker pool

**Problem.** `core/orchestrator.py` runs a single `_dispatch_loop` daemon. One slow Dispatcher LLM call blocks every user.

**Simplest viable fix.** Replace the single thread with a fixed-size pool. Same loop body, just N of them sharing the same `_dispatch_inbox`.

```python
def __init__(self, ..., dispatch_worker_count: int = 2):
    ...
    self._dispatch_workers: list[threading.Thread] = []

def start(self):
    self._queue.start()
    for i in range(self._dispatch_worker_count):
        t = threading.Thread(target=self._dispatch_loop,
                             name=f"orchestrator-dispatch-{i}", daemon=True)
        t.start()
        self._dispatch_workers.append(t)
```

Add `dispatcher.worker_count` (default `2`) to `_DEFAULTS["dispatcher"]` in `core/config.py`. Wire through in `app.py`.

**Why not refactor to `concurrent.futures`.** A `ThreadPoolExecutor` adds a future-management layer for zero benefit — the loop bodies don't return values. KISS wins.

**Tests** (`tests/test_orchestrator_queue.py`):
- N concurrent slow classify calls all start within ε of each other (mock dispatcher with a barrier).

---

## 3. `WorkerHealth` protocol

**Problem.** `workers/monitor.py:_check_worker` greps for `"unreachable"`, `"timeout"`, etc. — fragile across backends.

**Simplest viable fix.** Add one dataclass and one method. Workers report structured health; monitor reads the structured value. Keep string compatibility for any worker that hasn't migrated.

`workers/health.py` (new, ~15 lines):
```python
@dataclass(frozen=True)
class WorkerHealth:
    healthy: bool
    status: str  # short human-readable phrase
```

Each `ChatWorker` / `EmbeddingWorker` subclass implements:
```python
def health_check(self) -> WorkerHealth: ...
```

Update `_check_worker` in `workers/monitor.py`:
```python
def _check_worker(worker) -> tuple[str, bool]:
    fn = getattr(worker, "health_check", None)
    if fn is None:
        return "no health_check available", True
    try:
        result = fn()
        if isinstance(result, WorkerHealth):
            return result.status, result.healthy
        # Legacy string fallback — flag for follow-up removal.
        return str(result or ""), True
    except Exception as e:
        return f"health_check raised: {e}", False
```

**DRY check.** `WorkerHealth` is shared by the monitor (§3) and the circuit breaker (§4). One type, two consumers — appropriate abstraction.

**Tests** (`tests/test_monitor.py`):
- `WorkerHealth(healthy=False, status="unreachable")` produces a DEGRADED row regardless of message wording.
- Legacy string return still works.

---

## 4. Worker circuit breaker

**Problem.** A failing worker stays in rotation. Monitor only reports.

**Simplest viable fix.** A small `WorkerCircuitBreaker` mixin/wrapper that:
- counts consecutive failures
- after `threshold` (default 3), `health_check()` returns `WorkerHealth(healthy=False, …)` regardless of the underlying probe
- a successful call resets the counter

Place this on the `ChatWorker` base class so every backend gets it for free (DRY). The base `chat()` becomes a template method:

```python
# workers/base.py (existing ChatWorker)
def chat(self, system_prompt, messages, tools=None):
    try:
        result = self._chat_impl(system_prompt, messages, tools)
        self._consecutive_failures = 0
        return result
    except Exception:
        self._consecutive_failures = getattr(self, "_consecutive_failures", 0) + 1
        raise

def health_check(self) -> WorkerHealth:
    base = self._probe()  # subclass-provided ping
    if self._consecutive_failures >= self.failure_threshold:
        return WorkerHealth(False, f"circuit open after {self._consecutive_failures} failures")
    return base
```

Subclasses now implement `_chat_impl` and `_probe`, not `chat` / `health_check`. **This is a breaking refactor inside the worker hierarchy** — do it only because the same try/except wrapper would otherwise duplicate across every backend (Ollama, Gemini, future). SOLID: open/closed via subclass extension, single responsibility for the breaker.

**Where the breaker is actually consumed.** The monitor already surfaces unhealthy workers (§3). For now, "consumption" is only via the dashboard + alerts — we do **not** wire the dispatcher to skip unhealthy workers in this phase. Reason: routing-around requires a fallback policy (which tier replaces which?) that isn't worth designing speculatively. Logging + alerts are sufficient resilience for the current single-tier deployment.

**If the user wants automatic skipping later**, the hook is one line in the dispatcher: `if not worker_pool.get(tier).health_check().healthy: tier = fallback_tier`.

**Tests** (`tests/test_workers.py`, new):
- 3 consecutive `_chat_impl` exceptions → `health_check().healthy is False`.
- One success after 2 failures → counter resets, healthy.

---

## 5. Scheduled-tool HITL policy

**Problem.** `core/orchestrator.py:_run_scheduled_tool` calls `registry.execute(..., skip_hitl=True)`. A destructive scheduled job runs unattended.

**Simplest viable fix.** Read the `destructive` flag already on the registry entry. Refuse unattended destructive runs unless the schedule explicitly opts in:

```python
def _run_scheduled_tool(self, task, scheduled):
    name = scheduled.get("name", "")
    args = scheduled.get("arguments") or {}
    desc = scheduled.get("description") or name
    allow_unattended = scheduled.get("allow_unattended", False)

    if self.registry.is_destructive(name) and not allow_unattended:
        return MessageReply(
            text=f"[Scheduled: {desc}] refused — destructive tool requires "
                 f"allow_unattended=True on the schedule entry."
        )
    try:
        result = self.registry.execute(name, args, skip_hitl=True)
        ...
```

Add `ToolRegistry.is_destructive(name) -> bool` (one-liner — the flag is already stored at index 2 of the tuple).

**Why not filter by category.** Categories are advisory (`SYSTEM`, `OPERATOR`, etc.) and a tool's destructiveness is already a first-class boolean. Reusing the existing flag is DRY; introducing a second classification axis isn't.

**Tests** (`tests/test_orchestrator_queue.py`):
- Scheduled destructive tool without `allow_unattended` → returns refusal MessageReply, registry never called.
- Same with `allow_unattended=True` → registry called with `skip_hitl=True`.

---

## 6. Retry traceability

**Problem.** Every retry gets a new `task_id`. DLQ entries and logs can't be grouped.

**Simplest viable fix.** Two new fields on `Task`:

```python
@dataclass
class Task:
    ...
    root_task_id: str = ""        # defaults to self.id in __post_init__
    attempt: int = 1
```

In `_try_retry`, the clone gets `root_task_id=task.root_task_id, attempt=task.attempt + 1`. Drop `metadata["_retries"]` and `metadata["_retry_of"]` — they become redundant.

DLQ markdown header gains an `Attempt:` line and uses `root_task_id` as the grouping key. `tools/operator.py:retry_dlq` keeps current behaviour (creates a fresh task) but preserves the original `root_task_id`.

**SOLID note.** This is a single-responsibility extension of `Task`, not a new abstraction. No new classes required.

**Tests:**
- `tests/test_task.py`: `root_task_id` defaults to `id`; `attempt` defaults to 1.
- `tests/test_task_queue.py`: retried clone shares `root_task_id`, increments `attempt`.
- `tests/test_dlq.py`: DLQ entry records `attempt` and `root_task_id`.

---

## 7. Retry-success integration test

**Problem.** We test that retry happens, not that a retried task can succeed.

**Test sketch** (`tests/test_orchestrator_queue.py`):
- `chat_worker.chat` raises on first call, returns a normal reply on the second.
- Enqueue task → assert `on_result` ultimately receives a `MessageReply` with the success content.
- Assert DLQ size is 0 (success consumed the retry).

No production code changes — this is a verification gap.

---

## File-touch summary

| File | Change |
|---|---|
| `core/task.py` | + `root_task_id`, `attempt` fields. |
| `core/task_queue.py` | Backoff in `_try_retry`. Use new Task fields. Drop `_retries`/`_retry_of` metadata. |
| `core/orchestrator.py` | Dispatch worker pool. Destructive-tool guard in `_run_scheduled_tool`. |
| `core/config.py` | + `dispatcher.worker_count` default. |
| `core/registry.py` | + `is_destructive(name)` accessor. |
| `core/dlq.py` | Record `attempt` + `root_task_id`. |
| `workers/health.py` (new) | `WorkerHealth` dataclass. |
| `workers/base.py` | Circuit-breaker template-method refactor. |
| `workers/ollama_*.py`, `workers/gemini_*.py` | Rename `chat` → `_chat_impl`, `health_check` → `_probe`. |
| `workers/monitor.py` | Read structured `WorkerHealth`. |
| `tools/operator.py` | Preserve `root_task_id` on `retry_dlq`. |
| `app.py` | Pass `dispatch_worker_count` to Orchestrator. |
| Tests | Per-section additions; full suite stays green. |

Estimated diff size: ~250 LOC net, mostly the worker-base refactor.

---

## Risk register

- **Worker-base refactor (§4)** is the only invasive change. Mitigation: do it first on its own commit so test failures localize there.
- **Timer-based retry (§1)** holds Timer objects across process restart — they're lost. Acceptable: tasks are not durable today; that's a separate (unscoped) concern.
- **Field rename in `Task` (§6)** — search for `metadata["_retries"]` and `metadata["_retry_of"]` references; only the queue and DLQ touch them.

## Definition of done

- All seven items implemented with tests.
- Full suite passes (`python3 -m pytest tests/`).
- `ai-context.md` updated with: WorkerHealth protocol, dispatch pool count, scheduled-tool unattended policy, retry semantics with `root_task_id`/`attempt`.
- No new feature flags or backwards-compat shims.

## Suggested commit order

1. `Task.root_task_id`/`attempt` + DLQ fields (foundation for §1, §6).
2. Backoff + traceability in `_try_retry` (§1, §6).
3. Retry-success integration test (§7).
4. `WorkerHealth` dataclass + monitor consumption (§3).
5. Worker base-class circuit-breaker refactor (§4).
6. Dispatch worker pool (§2).
7. Scheduled-tool destructive guard (§5).
