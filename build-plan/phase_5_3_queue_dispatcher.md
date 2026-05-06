# Phase 5.3: Queue Engine & Dispatcher ✅ Complete (2026-05-06)

## Objective
Replace the FIFO message queue with a priority Task queue, and refactor the Orchestrator into the Dispatcher-driven Plan-and-Execute engine. This is the phase where the runtime behavior changes.

## Design Patterns
- **State Pattern** — `Task` has explicit state transitions (`QUEUED` → `RUNNING` → `COMPLETED`|`FAILED`). Invalid transitions raise errors.
- **Open/Closed (OCP)** — Priority levels are an enum with comparable ordering. Adding a new priority (e.g., `MEDIUM`) requires no queue logic changes.
- **Command Pattern** — Each `Task` encapsulates everything needed for execution: target agent, context, plan, callback.
- **SRP** — The queue manages ordering and worker allocation. The Orchestrator manages dispatch logic. Neither knows about Slack.

## Changes

### Task Model ✅ Done (2026-05-06)

#### [NEW] `core/task.py` ✅
```python
class TaskPriority(IntEnum):
    URGENT = 0       # Lower value = higher priority (heapq min-heap)
    BACKGROUND = 10

class TaskState(Enum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"

@dataclass
class Task:
    id: str                          # UUID
    priority: TaskPriority
    state: TaskState
    session_id: str                  # Original Slack thread
    agent_name: str                  # Target agent (set by Dispatcher)
    context: str                     # Distilled context
    plan: list[str] | None          # To-Do list (None for chat)
    user_id: str
    metadata: dict
    on_result: Callable | None       # Callback for result delivery
    created_at: float
    cancel_event: threading.Event    # Set to request cooperative cancellation
    max_run_time: float = 300.0      # Seconds before timeout watchdog cancels
    
    def transition(self, new_state: TaskState):
        """Enforce valid state transitions. CANCELLED is valid from QUEUED or RUNNING."""
```

### Queue Rewrite ✅ Done (2026-05-06)

Implemented in `core/task_queue.py`; legacy `core/queue.py` deleted.

#### [REWRITE] `core/queue.py` ✅
Old `GrugMessageQueue` and `QueuedMessage` deleted. New `TaskQueue` lives in `core/task_queue.py`.

`TaskQueue`:
- ✅ **Priority heap** via `heapq`, ordered by `(priority, created_at)`.
- ✅ **Session affinity lock** — `dict[session_id, Lock]`. Only one worker processes a session at a time.
- ✅ **Message batching** — when dequeuing an `URGENT` task, drain all pending `URGENT` tasks for the same `session_id` into a batch.
- ✅ **Worker allocation** — concurrency is enforced at the worker layer (`ChatWorker._semaphore` wraps every `chat()` call), so a queue-level acquire would double-acquire. Agent → worker mapping happens in the orchestrator's `_run_task`.
- ✅ **Background scheduling** — `background_runnable` callable gates BG dequeues. If closed, BG stays in the heap and the worker waits up to `background_poll_seconds` (default 60s) before re-checking. URGENT tasks bypass the gate. Configured via `queue.background_window` (`start_hour`/`end_hour`, wraps midnight); defaults to 22→6.
- **Saturation handling** — if all workers are busy, URGENT tasks hold in the heap until a worker is free. No HITL prompt for MVP.
- ✅ **Cancellation** — `cancel(task_id)` transitions `QUEUED` tasks to `CANCELLED` (removed from heap) or sets `cancel_event` on `RUNNING` tasks. Per-task watchdog (`threading.Timer`) sets `cancel_event` after `max_run_time` elapses.
- ✅ **StepLoop cancellation check** — Router checks `cancel_event.is_set()` at the top of each step and returns a `Task cancelled` `ToolExecutionResult` when set. The orchestrator transitions the task to `CANCELLED` and fires `on_result` with an `ErrorReply`.

### Orchestrator Refactor ✅ Done (2026-05-06)

#### [REWRITE] `core/orchestrator.py` ✅
Rewritten into the Dispatcher-driven engine. New flow lives in `_run_task` (queue worker callback) and `enqueue` (Dispatcher entry point). External API (`enqueue`, `start`, `process_message`, `execute_approved_action`, `re_infer`, `queue` property) is preserved so `adapters/slack.py` and other callers needed no changes. Tests: `tests/test_orchestrator_queue.py`.

Original spec for reference:

**For incoming user messages (Dynamic Ingress):**
1. Receive message → create `Task(priority=URGENT)`.
2. **Dispatcher phase** — run a fast LLM call on the dispatcher's worker with `dispatcher.md` prompt + recent chat history. The Dispatcher returns: `{agent: str, context: str, plan: list[str] | None}`.
3. **Fast path** — if Dispatcher returns `chat_agent` with no plan, set `task.agent_name = "chat_agent"`, pass standard chat history as context.
4. **Plan path** — if Dispatcher returns an expert agent with a plan, set `task.agent_name`, `task.context` (distilled), `task.plan` (To-Do list).
5. **Dispatcher failure** — if the Dispatcher LLM call fails (timeout, OOM, unparseable output), set `task.agent_name = "chat_agent"` with the raw user message as context and no plan. Log the failure for observability.
6. Enqueue the Task.

**For Task execution (by queue workers):**
1. Dequeue Task → get `AgentContainer` by `task.agent_name`.
2. Build the agent's system prompt: `base.md` + agent prompt + dynamic context (RAG query against agent's scoped sources using the distilled context).
3. **Chat agent**: message history from session store → standard StepLoop.
4. **Expert agent (Clean Slate)**: system prompt + distilled context + To-Do list → autonomous StepLoop (no raw chat history).
5. StepLoop completes → wrap result in `MessageReply` → fire `task.on_result` callback.
6. Append result to session history as `assistant` message (preserving conversational continuity).

**Key removals:**
- Remove `_build_context()` (replaced by per-agent context assembly).
- Remove `_prune_turns()` dependency on global config (pruning is per-agent based on worker's `context_window`).
- `process_message()` becomes the Dispatcher entry point, not the full execution pipeline.

### Router Updates ✅ Done (2026-05-06)

#### [MODIFY] `core/router.py` ✅
- ✅ `route_message()` remains the StepLoop implementation.
- ✅ `agent_container` kwarg switches the StepLoop to the agent's scoped registry and worker.
- ✅ `cancel_event` kwarg checked at the top of each step; returns `Task cancelled` early.
- Tests: `tests/test_router.py::test_router_uses_agent_container_registry`, `::test_router_respects_cancel_event`.

### Dispatch Tool Wiring ✅ Done (2026-05-06)

#### [MODIFY] `tools/dispatch.py` ✅
Wired to the live `TaskQueue`. Per-request `session_id` / `user_id` / `on_result` are read from `router._request_state` (same threadlocal pattern as scheduler_tools). Unknown agent names are rejected at dispatch time with the list of available agents. Falls back to a stub message when no queue is injected. Tests: `tests/test_dispatch_tool.py`.

Original spec for reference:
```python
def dispatch_task(agent: str, context: str, plan: list[str] = None) -> str:
    """Route a task to an Expert Agent. Called by chat_agent."""
    task = Task(
        priority=TaskPriority.URGENT,
        agent_name=agent,
        context=context,
        plan=plan,
        session_id=current_session_id,  # inherited from request state
        on_result=current_on_result,    # posts result to same thread
    )
    task_queue.enqueue(task)
    return f"Dispatched to {agent}. Results will appear in this thread."
```
The `dispatch_task` handler needs access to `task_queue` and the current request's `session_id` — inject these via closure at registration time (same pattern as `scheduler_tools.py`).

### Adapter Updates ✅ Done (2026-05-06) — no changes required

#### [MODIFY] `adapters/slack.py` ✅
The Slack adapter already calls `orchestrator.enqueue(...)` with an `on_result` callback that delivers a `MessageReply` / `ApprovalRequired` / `ErrorReply` event. The orchestrator rewrite preserved that public API verbatim, so no Slack changes were necessary. The orchestrator now fires the same `on_result` from inside the queue worker thread once the task reaches a terminal state.

## Verification ✅
1. ✅ `python3 -m pytest tests/` — 143 passing.
2. ✅ `tests/test_task.py` (10) — Task model + state machine + heap ordering.
3. ✅ `tests/test_task_queue.py` (12) — priority, session affinity, batching, watchdog, cancel, off-hours window.
4. ✅ `tests/test_dispatcher.py` (8) — JSON parse, fenced output, fallback on error, plan validation, `{{AVAILABLE_AGENTS}}` interpolation.
5. ✅ `tests/test_dispatch_tool.py` (4) — `dispatch_task` enqueues, rejects unknown agents, fires `on_result`.
6. ✅ `tests/test_orchestrator_queue.py` (4) — Dispatcher → chat_agent path, Expert Agent Clean Slate (no history), dispatcher-failure fallback, session affinity end-to-end.
7. ✅ Router additions covered in `tests/test_router.py` (`agent_container`, `cancel_event`).
8. ⬜ `evals/` golden dataset for Dispatcher classification — deferred; the Dispatcher class is ready to plug in once eval cases are written.
9. ⬜ Manual Slack end-to-end smoke — pending live boot with Slack tokens.
