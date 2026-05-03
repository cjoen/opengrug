# Phase 5.3: Queue Engine & Dispatcher

## Objective
Replace the FIFO message queue with a priority Task queue, and refactor the Orchestrator into the Dispatcher-driven Plan-and-Execute engine. This is the phase where the runtime behavior changes.

## Design Patterns
- **State Pattern** — `Task` has explicit state transitions (`QUEUED` → `RUNNING` → `COMPLETED`|`FAILED`). Invalid transitions raise errors.
- **Open/Closed (OCP)** — Priority levels are an enum with comparable ordering. Adding a new priority (e.g., `MEDIUM`) requires no queue logic changes.
- **Command Pattern** — Each `Task` encapsulates everything needed for execution: target agent, context, plan, callback.
- **SRP** — The queue manages ordering and worker allocation. The Orchestrator manages dispatch logic. Neither knows about Slack.

## Changes

### Task Model

#### [NEW] `core/task.py`
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

### Queue Rewrite

#### [REWRITE] `core/queue.py`
Full rewrite. Delete `GrugMessageQueue` and `QueuedMessage`.

New `TaskQueue`:
- **Priority heap** via `heapq`, ordered by `(priority, created_at)`.
- **Session affinity lock** — `dict[session_id, Lock]`. Only one worker processes a session at a time.
- **Message batching** — when dequeuing an `URGENT` task, drain all pending `URGENT` tasks for the same `session_id` into a batch.
- **Worker allocation** — match `task.agent_name` → `AgentContainer.worker` tier. Acquire the worker's concurrency semaphore before execution.
- **Background scheduling** — if only one chat worker tier exists and a `BACKGROUND` task is enqueued, check against the configured off-hours window. If outside the window, leave in `QUEUED` until the window opens.
- **Saturation handling** — if all workers are busy on an `URGENT` task, hold the task in queue (the semaphore blocks naturally). No HITL prompt for MVP.
- **Cancellation** — `cancel(task_id)` transitions `QUEUED` tasks to `CANCELLED` (removed from heap) or sets `cancel_event` on `RUNNING` tasks. A per-task watchdog thread sets `cancel_event` after `max_run_time` elapses.
- **StepLoop cancellation check** — the Router's StepLoop checks `task.cancel_event.is_set()` between each Think→Act iteration. If set, the loop exits early and the task transitions to `CANCELLED`.

### Orchestrator Refactor

#### [REWRITE] `core/orchestrator.py`
Refactor into the Dispatcher-driven engine. Core flow:

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

### Router Updates

#### [MODIFY] `core/router.py`
- `route_message()` remains the StepLoop implementation (Think → Act → Think).
- Add an `agent_container` parameter so it uses the agent's scoped registry (`agent.registry.get_all_schemas()`) instead of the global one.
- The Router doesn't need to know about the Dispatcher — it just runs the StepLoop for whatever agent it's given.
- Add cancellation awareness: check `task.cancel_event.is_set()` at the top of each step iteration. If set, return early with a `ToolExecutionResult(output="Task cancelled")` marker.

### Dispatch Tool Wiring

#### [MODIFY] `tools/dispatch.py`
Wire the `dispatch_task` stub (created in Phase 5.2) to the live `TaskQueue`:
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

### Adapter Updates

#### [MODIFY] `adapters/slack.py`
- Update to work with the new `Task`-based callback pattern.
- The `on_result` callback for user-initiated tasks posts to the Slack thread.

## Verification
1. `pytest tests/` — update `test_router.py`, `test_orchestrator.py` mocks for new interfaces.
2. New `tests/test_queue.py` — verify priority ordering, session affinity, message batching, background deferral.
3. New `tests/test_dispatcher.py` — verify Dispatcher prompt produces valid routing decisions (mock LLM responses).
4. New `evals/` cases — add golden dataset entries for Dispatcher classification (chat vs. complex task) and To-Do List generation quality.
5. Manual end-to-end: send a Slack message → verify Dispatcher routes to `chat_agent` → response appears in thread → follow-up works.
