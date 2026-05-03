# Sub-Agent Router & Priority Queue Engine PRD

This document serves as the Product Requirements Document (PRD) and core blueprint for overhauling OpenGrug's architecture into a dynamic, multi-agent queue system with CI/CD-style worker configurations.

## 1. The Ideal Framework vs. Current OpenGrug

### The Ideal Framework
The ideal agentic framework operates like a modern Kubernetes cluster or CI/CD pipeline:
- **Workers as Compute Nodes:** LLMs (local Gemma, cloud Gemini, etc.) are treated as raw compute workers with specific capabilities (context length, speed, cost, intelligence).
- **Agents as Containers:** Agents are scoped environments containing specific instructions, tool registries, and data access (RAG).
- **Router as Planner:** The Dispatcher (Router) analyzes incoming tasks. For complex goals, it extracts relevant chat history and generates a numbered To-Do List. It then routes the task, the distilled context, and the To-Do list to a specific *Expert Agent*.
- **Autonomous Execution:** The Expert Agent executes the entire task autonomously in a continuous StepLoop on a designated *Worker* tier.
- **Priority Queue:** User messages (`URGENT`) are assigned to fast local workers. Background tasks (`BACKGROUND`) are scheduled to cloud workers or run at night to ensure the queue remains unblocked.

### Comparison to Current OpenGrug
| Feature | Current OpenGrug | Proposed Architecture |
| :--- | :--- | :--- |
| **Agent Routing** | Simple LLM prompt "Dispatcher" that chooses a persona prompt. | Dispatcher extracts context, generates a To-Do List (Plan), and routes to an Expert Agent (Execute). |
| **Data (RAG) Access** | Unified `VectorMemory`. All context is injected regardless of the persona. | **Dynamic RAG Registry.** Each agent has a scoped subset of data (e.g., Scheduling agent only queries calendars). All databases share a unified `sqlite-vec` schema. |
| **Workers** | Configured globally. One primary LLM backend processes everything. | **CI/CD Style Config.** Define multiple workers (e.g., `local-fast`, `cloud-smart`). Tasks define which worker tier they need. |
| **Queue System** | FIFO per-session `GrugMessageQueue`. | **Unified Priority Queue.** `URGENT` messages hit local workers. `BACKGROUND` tasks hit cloud workers or are scheduled for off-hours to prevent queue lock. |
| **Tool Registry** | Tools are registered globally. | Each agent has a completely isolated tool registry and schema definition. |

---

## 2. Configuration Requirements

The `grug_config.json` will evolve to explicitly define *Workers*, *Agents*, and *RAG Sources*.

```json
{
  "workers": {
    "local-fast": {
      "provider": "ollama",
      "model": "gemma4:grug",
      "type": "chat",
      "context_window": 32768,
      "concurrency": 1
    },
    "cloud-smart": {
      "provider": "gemini",
      "model": "gemini-1.5-pro",
      "type": "chat",
      "context_window": 128000,
      "concurrency": 4
    },
    "embedder": {
      "provider": "ollama",
      "model": "nomic-embed-text",
      "type": "embedding",
      "concurrency": 4
    }
  },
  "dispatcher": {
    "worker_tier": "local-fast",
    "prompt": "prompts/dispatcher.md"
  },
  "agents": {
    "chat_agent": {
      "required_worker_tier": "local-fast",
      "prompt": "prompts/agents/chat_agent.md",
      "tools": "all",
      "rag_sources": ["core_memory"]
    },
    "researcher": {
      "required_worker_tier": "cloud-smart",
      "prompt": "prompts/agents/researcher.md",
      "tools": ["search_web", "read_url", "fetch_rss"],
      "rag_sources": ["web_cache"]
    }
  },
  "rag_sources": {
    "core_memory": {
      "db_path": "brain/memory.db",
      "embedding_worker": "embedder"
    },
    "web_cache": {
      "db_path": "brain/memory_web.db",
      "embedding_worker": "embedder"
    }
  }
}
```

### Prompt Architecture

Each agent's system prompt is assembled from two layers:

1. **`prompts/base.md`** — Shared across all agents. Contains personality (the Grug voice), security rules (injection resistance, untrusted input handling), and date/time variable interpolation (`{{CURRENT_DATE}}`, `{{CURRENT_TIME}}`).
2. **Agent-specific prompt** — Referenced by the `prompt` field in config. Contains the agent's domain framing, expertise description, and tool usage instructions scoped to its registry.

The final system prompt for any agent = `base.md` + agent-specific prompt + dynamic context (RAG, memory, instructions).

```
prompts/
  base.md                  ← personality, security rules, date vars
  dispatcher.md            ← intent classification, plan generation instructions
  agents/
    chat_agent.md          ← conversational behavior, tool categories
    researcher.md          ← research methodology, summarization style
```

Worker `type` determines the interface: `chat` workers expose `chat()` and `generate()`, `embedding` workers expose `embed()`. Embedding workers have their own concurrency semaphore and do not contend with chat inference for GPU resources.

*(Note: All `rag_sources` will share the same `sqlite-vec` table schema for consistency, allowing frontier models to easily query and adapt to them).*

*(Note: `"tools": "all"` is a special value meaning the agent inherits every tool in the global registry. Use explicit tool lists to restrict an agent's capabilities.)*

---

## 3. Priority Queue Engine

The queue engine is refactored into a **Task Priority Queue**.

### Core Concepts
1. **Task Entities:** Everything is a Task. User messages are `URGENT`. Scheduled skills/background research are `BACKGROUND`.
2. **Task State Machine:** Tasks are `QUEUED`, `RUNNING`, `COMPLETED`, `FAILED`, or `CANCELLED`. Cancellation is cooperative — the StepLoop checks a cancellation flag between steps.
3. **Clean Slate Context:** To prevent identity confusion, Expert Agents do not see the raw user chat history. Instead, the Dispatcher extracts relevant details from the chat and distills it into a "Context Block" passed directly to the Agent.
4. **Context Management:** For the MVP, we assume heavy background tasks run on Cloud workers (e.g., Gemini 1.5 Pro) with massive context windows, avoiding the need for an explicit Context Compressor step.

### The Queue Loop & Execution
1. Tasks enter the queue.
2. The Dispatcher (using a fast local worker) evaluates intent, distills context, and generates a To-Do List.
3. **Worker Allocation:** The Queue assigns the task to an available worker matching the Agent's required tier.
4. **No Interrupts (MVP):** Once a task begins its autonomous StepLoop, it runs until completion.
5. **Single-Tier Scheduling Policy:** User-initiated tasks are always `URGENT` and run next on the available worker, regardless of tier — the user is waiting. System-initiated tasks (cron jobs, nightly research) are `BACKGROUND`. On multi-tier setups, background tasks route to `cloud-smart`. On single-tier setups, background tasks are deferred to the configured off-hours window (e.g., 3 AM). A background task enqueued outside the window sits in `QUEUED` until the window opens.
6. **Dispatcher Failure Fallback:** If the Dispatcher's LLM call fails (timeout, OOM, unparseable output), the system creates an `URGENT` Task routed to `chat_agent` with the raw user message. The `chat_agent` can converse with the user to clarify intent and then call `dispatch_task` to route work to the correct Expert Agent.

### Conversational Safeguards
To ensure active Slack threads don't feel sluggish or disjointed due to the task queue:
- **Session Affinity (Thread Locking):** The Priority Queue enforces a strict lock on `session_id`. If an active conversation is happening, only one worker can process it at a time.
- **Message Batching:** If a user sends multiple messages rapidly, the worker will drain all pending messages for that session and combine them.
- **The Chat Agent Fallback:** Simple conversational follow-ups bypass Plan generation entirely and are instantly routed to a lightweight `chat_agent`.

### Task Cancellation
Tasks support cooperative cancellation to prevent runaway StepLoops from holding workers indefinitely:
- **Queued tasks:** Removed from the priority heap and transitioned to `CANCELLED`.
- **Running tasks:** A `cancel_event` (`threading.Event`) is set on the Task. The StepLoop checks this flag between each Think→Act step and exits early if set.
- **Timeout:** Each agent declares a `max_run_time` (default: 300s). A watchdog sets the `cancel_event` after the limit, ensuring the same graceful exit path.
- **Cancelled/timed-out tasks** are logged to the Dead Letter Queue with a reason (`user_cancelled` | `timeout`).
- **User access:** A `cancel_task` Grug tool allows users to cancel tasks from Slack.

---

## 4. Reliability & Scalability Principles

1. **Idempotent Tool Executions:** Tools must be safe to run multiple times (e.g., safe upserts).
2. **Liveness & Readiness Probes:** The queue polls worker health. Offline workers are skipped.
3. **Circuit Breakers for External I/O:** Tools that hit failed external APIs trip circuit breakers to prevent retries from burning tokens.
4. **Dead Letter Queue (DLQ):** Failed tasks are removed from the active queue and logged to `brain/failed_tasks.md` for human review.
5. **Worker Concurrency Limits:** The queue strictly respects the `concurrency` config of each worker tier (e.g., `1` for local GPU models) to prevent Out of Memory (OOM) crashes. Embedding workers are declared separately in the config (§2) with their own concurrency limits.

---

## 5. Agent Execution Engine (Plan & Execute)

We use a **Plan and Execute** pattern instead of rigid DAG pipelines. This blends the structure of a pipeline with the flexibility of an autonomous agent.

- **The Planner (Dispatcher):** The Dispatcher creates an explicit numbered plan (To-Do List) for complex tasks.
- **The Executor (Expert Agent):** The queue engine spins up the targeted Expert Agent (e.g., `ResearcherGrug`), injects its scoped `ToolRegistry` and `VectorMemory`, and places the To-Do List at the top of its system prompt.
- **The StepLoop:** The Agent autonomously executes the standard StepLoop (Think -> Act -> Think). Because it has an explicit, numbered plan in its context window, it stays on track and executes the steps sequentially without wandering off-topic.

### Agent Result Return Path
Expert Agents do **not** have `reply_to_user` as a tool. They simply execute their plan and return a final result string when the StepLoop completes. The return path is:
1. The Expert Agent's StepLoop completes → returns a result string.
2. The queue worker wraps the result in a `MessageReply` event and fires the task's `on_result` callback.
3. The adapter posts the result to the **original Slack thread** (using the `session_id` stored on the Task).
4. The result is appended to the thread's **session history** as an `assistant` message.

This preserves conversational continuity: the user sees the Agent's output in-thread and can reply to it naturally. The Dispatcher will have the Agent's result in history for follow-up routing.

### The Chat Agent as Conversational Dispatcher
The `chat_agent` is the system's generalist: it inherits **all** registered tools (notes, tasks, scheduling, instructions, etc.) and additionally has a `dispatch_task` tool. This makes it both a capable conversational agent and a human-in-the-loop dispatcher:
- If the automated Dispatcher routes to `chat_agent` (simple chat), it handles the request directly using any available tool.
- If the Dispatcher **fails** (§3.6), the `chat_agent` receives the raw user message. It can ask for clarification and, once intent is clear, call `dispatch_task(agent, context, plan)` to create a new Task and route it to the correct Expert Agent.
- Users can also explicitly request agent routing (e.g., "have the researcher look into X"), and the `chat_agent` will call `dispatch_task` accordingly.

---

## 6. Ingress & Triggering Mechanisms

- **Dynamic Ingress (User Messages):** Hits Ingress Controller -> Fast LLM Dispatcher generates Plan -> Creates `URGENT` Task -> Queues.
- **Static Ingress (Scheduled Jobs):** Scheduler watches for due jobs -> Reads declarative Skill File (which includes a pre-written To-Do list) -> Creates `BACKGROUND` Task -> Queues.

---

## 7. Observability & AI-First Operations

- **Monitoring Agent:** Checks queue length, worker probes, and DLQ size on a cron schedule.
- **Alerts:** Critical system failures push an `URGENT` Slack notification directly to the user.
- **Markdown Dashboards:** System state is natively rendered in Obsidian via `brain/system_health.md` and `brain/failed_tasks.md`.
- **System Runbook (`ai-context.md`):** Updated to reflect the architecture.
- **Operator Tools (Grug Tools):** The system will *not* autonomously edit its own core configuration or databases. Instead, operator actions are exposed as registered Grug tools callable from Slack: `queue_status` (report worker health + queue depth), `retry_dlq` (re-enqueue failed tasks), `clear_dlq` (purge `brain/failed_tasks.md`, destructive), `drain_queue` (cancel all `BACKGROUND` tasks, destructive), `cancel_task` (cancel a specific task by ID, destructive). Destructive tools require HITL approval.

---

## 8. Background Worker Migration

The existing `app.py` spawns five daemon threads for infrastructure maintenance. All five remain as plain Python threads (not Agent Tasks), but several need cleanup to work correctly with the new architecture.

### Threads That Become Task Producers

- **`scheduler_poll_loop`** — Currently calls `registry.execute()` directly and posts to Slack via `slack_client.chat_postMessage()`, bypassing the queue, orchestrator, and adapter entirely. **Cleanup:** stop calling `registry.execute()` and `slack_client` directly. When a due schedule is found, create a `Task` (with the pre-specified tool and arguments) and enqueue it. The queue handles execution; the adapter handles Slack delivery via the Task's `on_result` callback. This removes the `slack_client` dependency from `background.py`.
- **`nightly_grug_tasks_loop`** — Currently calls `orchestrator.process_message()`, which is close to correct. **Cleanup:** create `BACKGROUND` Tasks for each item in `agent_tasks.md` and enqueue them, instead of calling `process_message()` directly. This lets the priority queue manage scheduling and respects the off-hours window on single-tier setups.

### Threads That Need Concurrency Coordination

- **`boot_summarize`, `idle_sweep_loop`, `nightly_summarize_loop`** — All three call the LLM via the `Summarizer` class without any concurrency coordination. In the new architecture, this would bypass the worker semaphore and risk GPU contention. **Cleanup:** the `Summarizer` class should acquire the chat worker's semaphore before making LLM calls. The threads themselves and their scheduling logic stay unchanged.

### New Thread

- **Monitoring health-check loop** (§7) — A plain Python thread (not an LLM Agent). Polls worker `health_check()` probes, queue depths, and DLQ size directly in Python and writes results to `brain/system_health.md`.
