import os
import json
import threading
from slack_bolt import App
from slack_bolt.adapter.socket_mode import SocketModeHandler
from core.config import config
from core.llm import OllamaClient
from core.storage import GrugStorage
from core.sessions import SessionStore
from core.summarizer import Summarizer
from core.vectors import VectorMemory
from core.registry import ToolRegistry, load_prompt_files
from core.router import GrugRouter
from core.scheduler import ScheduleStore
from core.context import load_summary_files, build_system_prompt, find_turn_boundary, auto_offload_pruned_turns
from tools.tasks import TaskBoard
from tools.notes import add_note, get_recent_notes
from tools.scheduler_tools import add_schedule, list_schedules, cancel_schedule
from tools.system import set_timezone
from tools.search import search
from core.queue import GrugMessageQueue, QueuedMessage
from workers.background import boot_summarize, idle_sweep_loop, nightly_summarize_loop, scheduler_poll_loop

# ---------------------------------------------------------------------------
# Init
# ---------------------------------------------------------------------------
_slack_token = os.environ.get("SLACK_BOT_TOKEN", "mock_token")
app = App(
    token=_slack_token,
    token_verification_enabled=bool(os.environ.get("SLACK_BOT_TOKEN")),
)

llm_client = OllamaClient(
    host=config.llm.ollama_host,
    model=config.llm.model_name,
    timeout=config.llm.ollama_timeout,
    num_keep=config.llm.num_keep,
)
storage = GrugStorage(base_dir=config.storage.base_dir)
vector_memory = VectorMemory(db_path=os.path.join(config.storage.base_dir, "memory.db"))
session_store = SessionStore(db_path=os.path.join(config.storage.base_dir, "sessions.db"))
summarizer = Summarizer(llm_client=llm_client)
schedule_store = ScheduleStore(
    db_path=os.path.join(config.storage.base_dir, config.scheduler.db_file),
    timezone_str=config.scheduler.timezone,
)
registry = ToolRegistry()
task_board = TaskBoard(tasks_file=os.path.join(config.storage.base_dir, "tasks.md"))

# ---------------------------------------------------------------------------
# Register tools
# ---------------------------------------------------------------------------
registry.register_python_tool(
    name="query_memory",
    schema={"description": "[NOTES] Semantic/fuzzy search for older notes when you don't have an exact keyword. Use 'search' tool first for keyword lookups.", "type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]},
    func=vector_memory.query_memory,
    category="NOTES",
    friendly_name="Search memory"
)
registry.register_python_tool(
    name="add_task",
    schema={
        "description": "[BOARD] Create a new task on the project board. Requires human approval.",
        "type": "object",
        "properties": {
            "title": {"type": "string"},
            "description": {"type": "string"},
            "priority": {"type": "string", "enum": ["high", "medium", "low"]},
            "assignee": {"type": "string", "description": "Username without @ prefix"}
        },
        "required": ["title"]
    },
    func=task_board.add_task,
    destructive=False,
    category="TASKS",
    friendly_name="Create a task"
)
registry.register_python_tool(
    name="list_tasks",
    schema={
        "description": "[BOARD] List tasks on the project board. Optionally filter by status ('open' or 'done').",
        "type": "object",
        "properties": {"status": {"type": "string", "description": "Filter: 'open' or 'done'"}},
    },
    func=task_board.list_tasks,
    category="TASKS",
    friendly_name="List tasks"
)
registry.register_python_tool(
    name="edit_task",
    schema={
        "description": "[BOARD] Update an existing task's status or append notes. Requires human approval.",
        "type": "object",
        "properties": {
            "line_number": {"type": "string", "description": "Line number of the task in tasks.md"},
            "status": {"type": "string", "description": "'done' or 'open'"},
            "append_notes": {"type": "string"}
        },
        "required": ["line_number"]
    },
    func=task_board.edit_task,
    destructive=False,
    category="TASKS",
    friendly_name="Update a task"
)

registry.register_python_tool(
    name="summarize_board",
    schema={
        "description": "[BOARD] Give a short natural-language summary of tasks on the project board. Use when the user asks for an overview, summary, or 'state of the board'. Optionally filter by status.",
        "type": "object",
        "properties": {
            "status": {"type": "string", "description": "Filter by task status (e.g. 'Todo', 'In Progress', 'Done')"}
        }
    },
    func=lambda status=None: task_board.summarize_board(llm_client, status),
    category="TASKS",
    friendly_name="Summarize the board"
)

router = GrugRouter(registry, storage, llm_client=llm_client)

registry.register_python_tool(
    name="add_note",
    schema={
        "description": "[NOTES] Save an insight, thought, or generic memory permanently.",
        "type": "object",
        "properties": {
            "content": {"type": "string"},
            "tags": {"type": "array", "items": {"type": "string", "enum": ["dev", "personal", "infra", "meeting", "urgent", "draft", "misc"]}}
        },
        "required": ["content"]
    },
    func=lambda content, tags=None: add_note(storage, llm_client, content, tags),
    category="NOTES",
    friendly_name="Save a note"
)
registry.register_python_tool(
    name="get_recent_notes",
    schema={"description": "[NOTES] Fetch and display recent notes as a readable grouped bulletin.", "type": "object", "properties": {}},
    func=lambda: get_recent_notes(storage),
    category="NOTES",
    friendly_name="Read recent notes"
)
registry.register_python_tool(
    name="search",
    schema={
        "description": "[NOTES] Search all notes, summaries, and tasks for a keyword or phrase. Use this as the default search tool.",
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "Text to search for"},
            "limit": {"type": "integer", "description": "Max results (default 20)"}
        },
        "required": ["query"]
    },
    func=lambda query, limit=config.memory.search_result_limit: search(config.storage.base_dir, query, vector_memory, limit),
    category="NOTES",
    friendly_name="Search everything"
)

# Scheduler tools — channel/user/thread_ts injected at call time, not by LLM
registry.register_python_tool(
    name="add_schedule",
    schema={
        "description": "[SCHEDULE] Create a recurring cron job or one-shot scheduled task. For reminders, use tool_name='reply_to_user'. Schedule is a cron expression (e.g. '0 9 * * 1') or ISO datetime (e.g. '2026-04-14T15:00:00').",
        "type": "object",
        "properties": {
            "tool_name": {"type": "string", "description": "The tool to execute on schedule"},
            "arguments": {"type": "object", "description": "Arguments to pass to the tool"},
            "schedule": {"type": "string", "description": "Cron expression or ISO datetime"},
            "description": {"type": "string", "description": "Human-readable label"}
        },
        "required": ["tool_name", "schedule"]
    },
    func=lambda tool_name, arguments=None, schedule=None, description=None: add_schedule(
        schedule_store, registry, tool_name, arguments, schedule, description,
        _channel=getattr(router._request_state, '_schedule_channel', ''),
        _user=getattr(router._request_state, '_schedule_user', ''),
        _thread_ts=getattr(router._request_state, '_schedule_thread_ts', ''),
    ),
    category="SCHEDULE",
    friendly_name="Schedule a task"
)
registry.register_python_tool(
    name="list_schedules",
    schema={
        "description": "[SCHEDULE] List all active scheduled tasks and reminders.",
        "type": "object",
        "properties": {}
    },
    func=lambda: list_schedules(
        schedule_store,
        _channel=getattr(router._request_state, '_schedule_channel', None),
        _user=getattr(router._request_state, '_schedule_user', None),
    ),
    category="SCHEDULE",
    friendly_name="List schedules"
)
registry.register_python_tool(
    name="cancel_schedule",
    schema={
        "description": "[SCHEDULE] Cancel a scheduled task by its ID number.",
        "type": "object",
        "properties": {"schedule_id": {"type": "integer"}},
        "required": ["schedule_id"]
    },
    func=lambda schedule_id: cancel_schedule(schedule_store, schedule_id),
    category="SCHEDULE",
    friendly_name="Cancel a schedule"
)
registry.register_python_tool(
    name="set_timezone",
    schema={
        "description": "[SCHEDULE] Update the scheduler timezone used to interpret one-shot reminder times. Use an IANA timezone name (e.g. 'America/Los_Angeles', 'Europe/London', 'Asia/Tokyo'). Cron expressions are always evaluated in UTC.",
        "type": "object",
        "properties": {
            "timezone_str": {"type": "string", "description": "IANA timezone name"}
        },
        "required": ["timezone_str"]
    },
    func=lambda timezone_str: set_timezone(timezone_str, config, schedule_store),
    category="SCHEDULE",
    friendly_name="Set scheduler timezone"
)

base_prompt = load_prompt_files("prompts")


# ---------------------------------------------------------------------------
# Queue worker — processes one QueuedMessage at a time
# ---------------------------------------------------------------------------
def process_message(msg: QueuedMessage, silent_success=False):
    """Process a single queued message. Called by GrugMessageQueue workers."""
    text = msg.text
    thread_ts = msg.thread_ts
    channel_id = msg.channel_id
    ts = msg.ts
    user_id = msg.user_id
    client = msg.client

    try:
        # Inject schedule context for scheduler tools
        router._request_state._schedule_channel = channel_id
        router._request_state._schedule_user = user_id
        router._request_state._schedule_thread_ts = thread_ts

        session = session_store.get_or_create(thread_ts, channel_id)
        history = session["messages"][-config.memory.thread_history_limit:]

        summaries_dir = os.path.join(config.storage.base_dir, "summaries")
        summaries = load_summary_files(summaries_dir, config.memory.summary_days_limit)
        capped_tail = storage.get_capped_tail(config.memory.capped_tail_lines)

        system_prompt = build_system_prompt(base_prompt, summaries, capped_tail)
        messages = history + [{"role": "user", "content": text}]

        # Turn-based pruning
        estimated_tokens = len(str(system_prompt) + str(messages)) // 4
        while estimated_tokens > config.llm.target_context_tokens and len(messages) > 1:
            turn_end = find_turn_boundary(messages)
            pruned = messages[:turn_end]
            messages = messages[turn_end:]
            threading.Thread(
                target=auto_offload_pruned_turns,
                args=(pruned, summarizer, storage),
                daemon=True,
            ).start()
            estimated_tokens = len(str(system_prompt) + str(messages)) // 4

        result = router.route_message(
            user_message=text,
            system_prompt=system_prompt,
            message_history=messages,
        )

        if result.requires_approval:
            session_store.set_pending_hitl(thread_ts, {
                "tool_name": result.tool_name,
                "arguments": result.arguments,
                "user": user_id,
            })
            args_preview = json.dumps(result.arguments or {}, indent=2)
            blocks = [
                {
                    "type": "section",
                    "text": {"type": "mrkdwn", "text": f":warning: Grug wants to run *{result.tool_name}*\n```\n{args_preview}\n```"}
                },
                {
                    "type": "actions",
                    "elements": [
                        {"type": "button", "text": {"type": "plain_text", "text": "Approve"}, "style": "primary", "action_id": "grug_approve", "value": thread_ts},
                        {"type": "button", "text": {"type": "plain_text", "text": "Deny"}, "style": "danger", "action_id": "grug_deny", "value": thread_ts},
                    ]
                }
            ]
            client.chat_postMessage(channel=channel_id, thread_ts=thread_ts, text=f"Grug wants to run {result.tool_name}. Approve?", blocks=blocks)
        else:
            assistant_content = result.llm_response if result.llm_response else result.output
            new_messages = session["messages"] + [
                {"role": "user", "content": text},
                {"role": "assistant", "content": assistant_content},
            ]
            session_store.update_messages(thread_ts, new_messages)
            if silent_success and result.success:
                try:
                    client.reactions_add(channel=channel_id, timestamp=ts, name="white_check_mark")
                except Exception:
                    pass
            else:
                client.chat_postMessage(channel=channel_id, thread_ts=thread_ts, text=result.output)

    except Exception as e:
        print(f"[grug-queue] error: {e}")
        try:
            recent_context = storage.get_raw_notes(limit=10)
            if not recent_context:
                recent_context = "No recent memory. The cave is empty."
            fallback_prompt = build_system_prompt(base_prompt, "", recent_context, compression_mode="FULL")
            fallback_result = router.route_message(
                user_message=text, system_prompt=fallback_prompt,
            )
            client.chat_postMessage(channel=channel_id, thread_ts=thread_ts, text=fallback_result.output)
        except Exception as fallback_err:
            print(f"[grug-queue] fallback also failed: {fallback_err}")
            client.chat_postMessage(channel=channel_id, thread_ts=thread_ts, text="Grug brain hurt. Something went wrong. Try again?")
    finally:
        router._request_state._schedule_channel = None
        router._request_state._schedule_user = None
        router._request_state._schedule_thread_ts = None


message_queue = GrugMessageQueue(
    process_fn=process_message,
    worker_count=config.queue.worker_count,
)


# ---------------------------------------------------------------------------
# Slack event handler
# ---------------------------------------------------------------------------
@app.event("message")
def handle_message(event, say, client):
    if event.get("subtype"):
        return
    text = event.get("text")
    if not text:
        return

    thread_ts = event.get("thread_ts", event["ts"])
    channel_id = event.get("channel")
    ts = event.get("ts")
    user_id = event.get("user")

    message_queue.enqueue(QueuedMessage(
        thread_ts=thread_ts,
        channel_id=channel_id,
        ts=ts,
        user_id=user_id,
        text=text,
        client=client,
    ))


# ---------------------------------------------------------------------------
# HITL action handlers
# ---------------------------------------------------------------------------
@app.action("grug_approve")
def handle_approve(ack, body, client):
    ack()
    thread_ts = body["actions"][0]["value"]
    channel = body["channel"]["id"]
    clicker = body["user"]["id"]

    session = session_store.get_or_create(thread_ts, channel)
    pending = session["pending_hitl"]

    if not pending:
        client.chat_postMessage(channel=channel, text="No pending action found (expired or already handled).")
        return

    if clicker != pending["user"]:
        client.chat_postEphemeral(channel=channel, user=clicker, text=":no_entry_sign: Only the person who requested this action can approve it.")
        return

    result = registry.execute(pending["tool_name"], pending["arguments"], skip_hitl=True)
    session_store.set_pending_hitl(thread_ts, None)

    messages = session["messages"]
    messages.append({"role": "assistant", "content": f"[Tool executed: {pending['tool_name']}] {result.output}"})
    session_store.update_messages(thread_ts, messages)

    status_prefix = "" if result.success else ":x: "
    client.chat_postMessage(channel=channel, thread_ts=thread_ts, text=f"{status_prefix}<@{clicker}> approved `{pending['tool_name']}`: {result.output}")

    def _re_infer():
        try:
            summaries_dir = os.path.join(config.storage.base_dir, "summaries")
            summaries = load_summary_files(summaries_dir, config.memory.summary_days_limit)
            capped_tail = storage.get_capped_tail(config.memory.capped_tail_lines)
            sys_prompt = build_system_prompt(base_prompt, summaries, capped_tail)

            updated_session = session_store.get_or_create(thread_ts, channel)
            hist = updated_session["messages"][-config.memory.thread_history_limit:]

            follow_up = router.route_message(user_message="", system_prompt=sys_prompt, message_history=hist)
            if follow_up.output and not follow_up.requires_approval:
                messages_now = updated_session["messages"]
                assistant_content = follow_up.llm_response if follow_up.llm_response else follow_up.output
                messages_now.append({"role": "assistant", "content": assistant_content})
                session_store.update_messages(thread_ts, messages_now)
                client.chat_postMessage(channel=channel, thread_ts=thread_ts, text=follow_up.output)
        except Exception as e:
            print(f"[re-infer] error: {e}")

    threading.Thread(target=_re_infer, daemon=True).start()


@app.action("grug_deny")
def handle_deny(ack, body, client):
    ack()
    thread_ts = body["actions"][0]["value"]
    channel = body["channel"]["id"]
    clicker = body["user"]["id"]

    session = session_store.get_or_create(thread_ts, channel)
    pending = session["pending_hitl"]

    if not pending:
        client.chat_postMessage(channel=channel, text="No pending action found (expired or already handled).")
        return

    if clicker != pending["user"]:
        client.chat_postEphemeral(channel=channel, user=clicker, text=":no_entry_sign: Only the person who requested this action can deny it.")
        return

    session_store.set_pending_hitl(thread_ts, None)
    client.chat_postMessage(channel=channel, thread_ts=thread_ts, text=f":no_entry_sign: <@{clicker}> denied `{pending['tool_name']}`. Cancelled.")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    print("Grug is awakening...")
    vector_memory.start_background_indexer()
    message_queue.start()
    print(f"  Queue started with {config.queue.worker_count} worker(s)")
    threading.Thread(target=boot_summarize, args=(summarizer, storage, config), daemon=True).start()
    threading.Thread(target=idle_sweep_loop, args=(session_store, summarizer, storage, config), daemon=True).start()
    threading.Thread(target=nightly_summarize_loop, args=(summarizer, storage, config), daemon=True).start()
    threading.Thread(target=scheduler_poll_loop, args=(schedule_store, registry, app.client, config), daemon=True).start()
    try:
        SocketModeHandler(app, os.environ.get("SLACK_APP_TOKEN", "mock_app_token")).start()
    except Exception as e:
        print("Missing valid Slack tokens to boot websocket listener. Grug sleeps.")
