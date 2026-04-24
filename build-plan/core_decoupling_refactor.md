# Build Plan: Core Decoupling Refactor (UI-Agnostic Engine)

**Status:** Ready to implement
**Priority:** High (Prerequisite for Phase 3: Background tasks and Web UI)
**Goal:** Establish a strict "Dumb Input, Smart Core" boundary so OpenGrug can be triggered by Slack, OpenWebUI, or Background Workers simultaneously without race conditions or state leaks.

---

## 1. Abstracting Session Identifiers
The engine currently assumes all input comes from Slack threads.

**Current:** `process_message(text, thread_ts, channel_id, user_id)`
**Refactor:** `submit_event(session_id: str, text: str, user_id: str, metadata: dict)`

- `thread_ts` becomes `session_id`.
- `channel_id` is moved inside a flexible `metadata` dictionary (e.g., `metadata={"reply_to_channel": "C123", "platform": "slack"}`).
- **Files to update:** `core/orchestrator.py`, `core/sessions.py` (rename schema fields conceptually), all adapter code.

---

## 2. Pushing the Queue Inside the Orchestrator
Currently, the `GrugMessageQueue` sits between the Slack Adapter and the Orchestrator. The Web Adapter in `grug-web-ui` had to bypass it entirely, introducing its own semaphore lock. This breaks concurrency if both run simultaneously.

**Refactor:**
- Move `GrugMessageQueue` initialization inside the `Orchestrator` class.
- Adapters (Slack, FastApi, Background Scripts) should call a non-blocking `orchestrator.enqueue(...)`.
- The Queue's internal worker thread calls the actual `_process_message_internal`.
- **Files to update:** `app.py`, `core/orchestrator.py`, `core/queue.py`, `adapters/slack.py`, `adapters/web.py`.

---

## 3. Abstracting the Scheduler Output
The background cron loop is hardcoded to use the Slack `app.client` to deliver its reminders.

**Current (in `app.py`):** `scheduler_poll_loop(schedule_store, registry, app.client, config)`
**Refactor:**
- Implement an Event Emitter or Notification Router.
- Adapters register their notification callbacks at boot: `notification_router.register("slack", slack_adapter.send_notification)`.
- The scheduler tool writes the `metadata.platform` into the database when scheduling a task.
- When the cron fires, the `scheduler_poll_loop` calls `notification_router.emit(task.platform, task.payload)`.
- **Files to update:** `core/scheduler.py`, `workers/background.py`, `tools/scheduler_tools.py`, `app.py`.

---

## 4. Centralizing HITL State Management
Both the Slack adapter and Web adapter manually check `session.get("pending_hitl")` to see if they need to intercept "yes/no" responses. The adapters shouldn't know anything about the state machine.

**Refactor:**
- Adapters just pass the text string to the Orchestrator.
- `Orchestrator._process_message_internal` checks for `pending_hitl`. If active, it inspects the string for approval/denial keywords, processes the execution, and returns a final `MessageReply`.
- **Files to update:** `core/orchestrator.py`, `adapters/slack.py`, `adapters/web.py`.

---

## The Target Request Flow
1. **Adapter** receives text and calls `orchestrator.enqueue(session_id, text, metadata)`.
2. **Orchestrator's internal Queue** picks up the task, ensuring safe concurrency.
3. **Orchestrator** checks for HITL. If none, it runs the `StepLoop` (LLM -> Tool -> LLM). **Critical Addition:** The `StepLoop` must implement a circuit breaker (e.g., tracking a hash of recent tool calls) to forcibly abort if the exact same tool and arguments are called consecutively, preventing infinite hallucination loops.
4. **Orchestrator** returns a generic `EngineResponse` dataclass.
5. **Adapter** translates the generic response into its specific UI (e.g., Slack Block Kit or Web Server-Sent Events).
