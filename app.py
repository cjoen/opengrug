import os
import json
import glob
import time
import threading
from datetime import datetime
from slack_bolt import App
from slack_bolt.adapter.socket_mode import SocketModeHandler
from core.config import config
from core.storage import GrugStorage
from core.sessions import SessionStore
from core.summarizer import Summarizer
from core.vectors import VectorMemory
from core.orchestrator import ToolRegistry, GrugRouter, load_prompt_files

_slack_token = os.environ.get("SLACK_BOT_TOKEN", "mock_token")
app = App(
    token=_slack_token,
    token_verification_enabled=bool(os.environ.get("SLACK_BOT_TOKEN")),
)

# ---------------------------------------------------------------------------
# 1. Initialize Components
# ---------------------------------------------------------------------------
storage = GrugStorage(base_dir=config.storage.base_dir)
vector_memory = VectorMemory(
    db_path=os.path.join(config.storage.base_dir, "memory.db")
)
session_store = SessionStore(
    db_path=os.path.join(config.storage.base_dir, "sessions.db")
)
summarizer = Summarizer(
    storage=storage,
    ollama_host=os.environ.get("OLLAMA_HOST", "http://localhost:11434"),
    model_name=config.llm.model_name,
)
registry = ToolRegistry()

# ---------------------------------------------------------------------------
# 2. Simple Markdown Task Board
# ---------------------------------------------------------------------------
_TASKS_FILE = os.path.join(config.storage.base_dir, "tasks.md")


def _ensure_tasks_file():
    """Create tasks.md if it doesn't exist."""
    if not os.path.exists(_TASKS_FILE):
        os.makedirs(os.path.dirname(_TASKS_FILE), exist_ok=True)
        with open(_TASKS_FILE, "w", encoding="utf-8") as f:
            f.write("# Grug Task Board\n\n")


def add_task(title, priority=None, assignee=None, description=None):
    """Append a markdown checkbox to brain/tasks.md."""
    _ensure_tasks_file()
    parts = [f"- [ ] {title}"]
    if priority:
        parts.append(f"[{priority}]")
    if assignee:
        parts.append(f"@{assignee}")
    line = " ".join(parts)
    if description:
        line += f"\n  > {description}"
    with open(_TASKS_FILE, "a", encoding="utf-8") as f:
        f.write(line + "\n")
    return f"Task added: {title}"


def list_tasks(status=None):
    """Read brain/tasks.md, optionally filter by open/done."""
    _ensure_tasks_file()
    with open(_TASKS_FILE, "r", encoding="utf-8") as f:
        lines = f.readlines()

    tasks = []
    for i, line in enumerate(lines, 1):
        stripped = line.strip()
        if stripped.startswith("- [ ] "):
            if status is None or status.lower() in ("open", "todo", "to do"):
                tasks.append(f"{i}: {stripped}")
        elif stripped.startswith("- [x] "):
            if status is None or status.lower() == "done":
                tasks.append(f"{i}: {stripped}")

    if not tasks:
        return "No tasks found."
    return "\n".join(tasks)


def edit_task(line_number, status=None, append_notes=None):
    """Toggle task checkbox or append notes by line number."""
    _ensure_tasks_file()
    with open(_TASKS_FILE, "r", encoding="utf-8") as f:
        lines = f.readlines()

    idx = int(line_number) - 1
    if idx < 0 or idx >= len(lines):
        return f"Line {line_number} not found in tasks.md"

    line = lines[idx]
    if status and status.lower() == "done" and "- [ ] " in line:
        lines[idx] = line.replace("- [ ] ", "- [x] ", 1)
    elif status and status.lower() in ("open", "todo", "to do") and "- [x] " in line:
        lines[idx] = line.replace("- [x] ", "- [ ] ", 1)

    if append_notes:
        lines[idx] = lines[idx].rstrip("\n") + f"  ({append_notes})\n"

    with open(_TASKS_FILE, "w", encoding="utf-8") as f:
        f.writelines(lines)
    return f"Task on line {line_number} updated."


# ---------------------------------------------------------------------------
# 3. Register Python Tools
# ---------------------------------------------------------------------------
registry.register_python_tool(
    name="query_memory",
    schema={"description": "[NOTES] Use this tool to remember past conversations or search for older notes.", "type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]},
    func=vector_memory.query_memory,
    friendly_name="Search memory"
)

# Task board tools (pure Python, markdown-backed)
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
    func=add_task,
    destructive=True,
    friendly_name="Create a task"
)
registry.register_python_tool(
    name="list_tasks",
    schema={
        "description": "[BOARD] List tasks on the project board. Optionally filter by status ('open' or 'done').",
        "type": "object",
        "properties": {
            "status": {"type": "string", "description": "Filter: 'open' or 'done'"}
        }
    },
    func=list_tasks,
    destructive=False,
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
    func=edit_task,
    destructive=True,
    friendly_name="Update a task"
)

# ---------------------------------------------------------------------------
# 4. Mount Router & Read Prompts
# ---------------------------------------------------------------------------
router = GrugRouter(registry, storage)
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
    func=router.execute_add_note,
    friendly_name="Save a note"
)
registry.register_python_tool(
    name="get_recent_notes",
    schema={"description": "[NOTES] Fetch and display recent notes as a readable grouped bulletin. Use when the user asks to see, show, or read their notes.", "type": "object", "properties": {}},
    func=router.execute_get_recent_notes,
    friendly_name="Read recent notes"
)
base_prompt = load_prompt_files("prompts")

# ---------------------------------------------------------------------------
# 5. Context Injection Pipeline Helpers
# ---------------------------------------------------------------------------

def load_summary_files(summaries_dir, days_limit):
    """Read up to ``days_limit`` summary files, newest first, return concatenated content."""
    if not os.path.isdir(summaries_dir):
        return ""
    summary_files = sorted(
        glob.glob(os.path.join(summaries_dir, "*.summary.md")),
        reverse=True,
    )[:days_limit]
    parts = []
    for fpath in summary_files:
        try:
            with open(fpath, "r", encoding="utf-8") as f:
                parts.append(f.read().strip())
        except OSError:
            continue
    return "\n\n".join(parts)


def build_system_prompt(base, summaries, capped_tail, compression_mode=None):
    """Assemble the full system prompt with persona, summaries, and today's notes."""
    if compression_mode is None:
        compression_mode = config.llm.default_compression
    today = datetime.now().strftime("%Y-%m-%d")
    prompt = base.replace("{{COMPRESSION_MODE}}", compression_mode)
    prompt = prompt.replace("{{CURRENT_DATE}}", today)

    if summaries:
        prompt += f"\n\n## Recent Summaries (last {config.memory.summary_days_limit} days)\n{summaries}"
    if capped_tail:
        prompt += f"\n\n## Today's Notes\n{capped_tail}"

    return prompt


def find_turn_boundary(messages):
    """Find the index of the end of the first complete Turn.

    A Turn boundary is defined by the NEXT user message after position 0.
    Everything from index 0 to that boundary is one atomic Turn:
    (User → Assistant Tool Call(s) → Tool Result(s) → Assistant Final Reply).

    Returns the index (exclusive) to slice at.
    """
    for i in range(1, len(messages)):
        if messages[i].get("role") == "user":
            return i
    # No second user message — keep at least the last message
    return max(len(messages) - 1, 1)


def _auto_offload_pruned_turns(pruned, summ, stor):
    """Background thread: summarize pruned turns and append to daily notes."""
    try:
        turns_text = "\n".join(
            f"{m.get('role', 'unknown').upper()}: {m.get('content', '')}"
            for m in pruned
        )
        summary = summ.summarize_pruned_turns(turns_text)
        if summary:
            stor.append_log("auto-offload", summary)
    except Exception as e:
        print(f"[auto-offload] error: {e}")


# ---------------------------------------------------------------------------
# 6. Slack Event Handlers
# ---------------------------------------------------------------------------

@app.event("message")
def handle_message(event, say, client):
    if event.get("subtype"):
        return

    text = event.get("text")
    if not text:
        return

    # Step 1 — Identity
    thread_ts = event.get("thread_ts", event["ts"])
    channel_id = event.get("channel")
    ts = event.get("ts")

    # Add thinking reaction
    try:
        client.reactions_add(channel=channel_id, timestamp=ts, name="thought_balloon")
    except Exception as e:
        print(f"Failed to add reaction: {e}")

    # Run the pipeline in a background thread so we don't block the event loop
    def _process():
        try:
            # Step 2 — Recall
            session = session_store.get_or_create(thread_ts, channel_id)
            history = session["messages"][-config.memory.thread_history_limit:]

            # Step 3 — Environment
            summaries_dir = os.path.join(config.storage.base_dir, "summaries")
            summaries = load_summary_files(summaries_dir, config.memory.summary_days_limit)
            capped_tail = storage.get_capped_tail(config.memory.capped_tail_lines)

            # Step 4 — Assemble
            system_prompt = build_system_prompt(base_prompt, summaries, capped_tail)
            messages = history + [{"role": "user", "content": text}]

            # Step 5 — Safety Check & Auto-Offload (Turn-Based Pruning)
            estimated_tokens = len(str(system_prompt) + str(messages)) // 4
            while estimated_tokens > config.llm.target_context_tokens and len(messages) > 1:
                turn_end = find_turn_boundary(messages)
                pruned = messages[:turn_end]
                messages = messages[turn_end:]
                # Auto-offload pruned turns in background
                threading.Thread(
                    target=_auto_offload_pruned_turns,
                    args=(pruned, summarizer, storage),
                    daemon=True,
                ).start()
                estimated_tokens = len(str(system_prompt) + str(messages)) // 4

            # Step 6 — Route
            result = router.route_message(
                user_message=text,
                system_prompt=system_prompt,
                message_history=messages,
            )

            # Remove thinking reaction
            try:
                client.reactions_remove(channel=channel_id, timestamp=ts, name="thought_balloon")
            except Exception:
                pass

            # Step 7 — Handle result
            if result.requires_approval:
                # HITL: store pending action in session
                session_store.set_pending_hitl(thread_ts, {
                    "tool_name": result.tool_name,
                    "arguments": result.arguments,
                    "user": event.get("user"),
                })
                args_preview = json.dumps(result.arguments or {}, indent=2)
                blocks = [
                    {
                        "type": "section",
                        "text": {
                            "type": "mrkdwn",
                            "text": f":warning: Grug wants to run *{result.tool_name}*\n```\n{args_preview}\n```"
                        }
                    },
                    {
                        "type": "actions",
                        "elements": [
                            {
                                "type": "button",
                                "text": {"type": "plain_text", "text": "Approve"},
                                "style": "primary",
                                "action_id": "grug_approve",
                                "value": thread_ts
                            },
                            {
                                "type": "button",
                                "text": {"type": "plain_text", "text": "Deny"},
                                "style": "danger",
                                "action_id": "grug_deny",
                                "value": thread_ts
                            }
                        ]
                    }
                ]
                client.chat_postMessage(
                    channel=channel_id,
                    thread_ts=thread_ts,
                    text=f"Grug wants to run {result.tool_name}. Approve?",
                    blocks=blocks,
                )
            else:
                # Persist conversation history
                new_messages = session["messages"] + [
                    {"role": "user", "content": text},
                    {"role": "assistant", "content": result.output},
                ]
                session_store.update_messages(thread_ts, new_messages)

                client.chat_postMessage(
                    channel=channel_id,
                    thread_ts=thread_ts,
                    text=result.output,
                )

        except Exception as e:
            print(f"[handle_message] error: {e}")
            try:
                client.reactions_remove(channel=channel_id, timestamp=ts, name="thought_balloon")
            except Exception:
                pass
            # Graceful degradation: fall back to recent notes context
            try:
                recent_context = storage.get_raw_notes(limit=10)
                if not recent_context:
                    recent_context = "No recent memory. The cave is empty."
                fallback_result = router.route_message(
                    user_message=text,
                    context=recent_context,
                    compression_mode="FULL",
                    base_system_prompt=base_prompt,
                )
                client.chat_postMessage(
                    channel=channel_id,
                    thread_ts=thread_ts,
                    text=fallback_result.output,
                )
            except Exception as fallback_err:
                print(f"[handle_message] fallback also failed: {fallback_err}")
                client.chat_postMessage(
                    channel=channel_id,
                    thread_ts=thread_ts,
                    text="Grug brain hurt. Something went wrong. Try again?",
                )

    threading.Thread(target=_process, daemon=True).start()


# ---------------------------------------------------------------------------
# 7. HITL Action Handlers (Persistent via sessions.db)
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
        client.chat_postEphemeral(
            channel=channel, user=clicker,
            text=":no_entry_sign: Only the person who requested this action can approve it."
        )
        return

    # Execute the approved tool
    result = registry.execute(pending["tool_name"], pending["arguments"], skip_hitl=True)

    # Clear pending state
    session_store.set_pending_hitl(thread_ts, None)

    # Append tool result to session messages
    messages = session["messages"]
    messages.append({"role": "assistant", "content": f"[Tool executed: {pending['tool_name']}] {result.output}"})
    session_store.update_messages(thread_ts, messages)

    status_prefix = "" if result.success else ":x: "
    client.chat_postMessage(
        channel=channel,
        thread_ts=thread_ts,
        text=f"{status_prefix}<@{clicker}> approved `{pending['tool_name']}`: {result.output}"
    )

    # Re-trigger inference so Grug can react to the tool output
    def _re_infer():
        try:
            summaries_dir = os.path.join(config.storage.base_dir, "summaries")
            summaries = load_summary_files(summaries_dir, config.memory.summary_days_limit)
            capped_tail = storage.get_capped_tail(config.memory.capped_tail_lines)
            system_prompt = build_system_prompt(base_prompt, summaries, capped_tail)

            updated_session = session_store.get_or_create(thread_ts, channel)
            hist = updated_session["messages"][-config.memory.thread_history_limit:]

            follow_up = router.route_message(
                user_message="",
                system_prompt=system_prompt,
                message_history=hist,
            )
            if follow_up.output and not follow_up.requires_approval:
                messages_now = updated_session["messages"]
                messages_now.append({"role": "assistant", "content": follow_up.output})
                session_store.update_messages(thread_ts, messages_now)
                client.chat_postMessage(
                    channel=channel,
                    thread_ts=thread_ts,
                    text=follow_up.output,
                )
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
        client.chat_postEphemeral(
            channel=channel, user=clicker,
            text=":no_entry_sign: Only the person who requested this action can deny it."
        )
        return

    session_store.set_pending_hitl(thread_ts, None)
    client.chat_postMessage(
        channel=channel,
        thread_ts=thread_ts,
        text=f":no_entry_sign: <@{clicker}> denied `{pending['tool_name']}`. Cancelled."
    )


# ---------------------------------------------------------------------------
# 8. Background Workers
# ---------------------------------------------------------------------------

def _boot_summarize():
    """Run daily note summarization on startup."""
    try:
        summaries_dir = os.path.join(config.storage.base_dir, "summaries")
        daily_notes_dir = os.path.join(config.storage.base_dir, "daily_notes")
        summarizer.summarize_daily_notes(
            summaries_dir=summaries_dir,
            daily_notes_dir=daily_notes_dir,
            threshold_bytes=config.memory.summarization_threshold_bytes,
            days_limit=config.memory.summary_days_limit,
        )
        print("[boot] daily note summarization complete")
    except Exception as e:
        print(f"[boot] summarization failed: {e}")


def _idle_sweep_loop():
    """Simple sleep-loop daemon: compact idle sessions to the Truth Layer."""
    interval = config.memory.idle_sweep_interval_minutes * 60
    while True:
        time.sleep(interval)
        try:
            idle_sessions = session_store.get_idle_sessions(
                config.memory.thread_idle_timeout_hours
            )
            for sess in idle_sessions:
                ts = sess["thread_ts"]
                original_last_active = session_store.check_last_active(ts)

                messages = sess["messages"]
                if not messages:
                    session_store.delete_session(ts)
                    continue

                # Summarize the conversation
                summary = summarizer.summarize_session_for_compaction(messages)
                if summary:
                    # Append each bullet to daily notes
                    for line in summary.strip().split("\n"):
                        line = line.strip()
                        if line.startswith("- "):
                            line = line[2:]  # strip the "- " prefix, append_log adds its own
                        if line:
                            storage.append_log("idle-compaction", line)

                # Optimistic check: abort deletion if user sent a message during compaction
                current_last_active = session_store.check_last_active(ts)
                if current_last_active != original_last_active:
                    print(f"[idle-sweep] session {ts} became active during compaction, skipping deletion")
                    continue

                session_store.delete_session(ts)
                print(f"[idle-sweep] compacted and deleted session {ts}")

        except Exception as e:
            print(f"[idle-sweep] error: {e}")


def _nightly_summarize_loop():
    """Simple sleep-loop daemon: run daily summarization once per night around midnight."""
    last_run_date = None
    while True:
        time.sleep(60)  # Check every minute
        now = datetime.now()
        today_str = now.strftime("%Y-%m-%d")
        if now.hour == 0 and last_run_date != today_str:
            last_run_date = today_str
            try:
                summaries_dir = os.path.join(config.storage.base_dir, "summaries")
                daily_notes_dir = os.path.join(config.storage.base_dir, "daily_notes")
                summarizer.summarize_daily_notes(
                    summaries_dir=summaries_dir,
                    daily_notes_dir=daily_notes_dir,
                    threshold_bytes=config.memory.summarization_threshold_bytes,
                    days_limit=config.memory.summary_days_limit,
                )
                print(f"[nightly] daily note summarization complete for {today_str}")
            except Exception as e:
                print(f"[nightly] summarization failed: {e}")


# ---------------------------------------------------------------------------
# 9. Main
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    print("Grug is awakening...")

    # Start background indexer for vector search
    vector_memory.start_background_indexer()

    # Boot summarization (runs once in background)
    threading.Thread(target=_boot_summarize, daemon=True).start()

    # Idle session sweep (runs every idle_sweep_interval_minutes)
    threading.Thread(target=_idle_sweep_loop, daemon=True).start()

    # Nightly summarization (checks for midnight every 60s)
    threading.Thread(target=_nightly_summarize_loop, daemon=True).start()

    try:
        SocketModeHandler(app, os.environ.get("SLACK_APP_TOKEN", "mock_app_token")).start()
    except Exception as e:
        print("Missing valid Slack tokens to boot websocket listener. Grug sleeps.")
