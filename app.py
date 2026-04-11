import os
import json
import uuid
import subprocess
from datetime import datetime, timedelta
from slack_bolt import App
from slack_bolt.adapter.socket_mode import SocketModeHandler
from core.storage import GrugStorage
from core.vectors import VectorMemory
from core.orchestrator import ToolRegistry, GrugRouter, load_prompt_files

app = App(token=os.environ.get("SLACK_BOT_TOKEN", "mock_token"))

# In-memory pending HITL approvals keyed by UUID stored in button `value`.
PENDING: dict[str, dict] = {}
PENDING_TTL = timedelta(hours=1)

# 1. Initialize Components
storage = GrugStorage(base_dir="/app/brain" if os.environ.get("DOCKER") else "./brain")
vector_memory = VectorMemory(db_path="/app/brain/memory.db" if os.environ.get("DOCKER") else "./brain/memory.db")
registry = ToolRegistry()

# --- Backlog.md CLI tools ---
# Working directory for backlog commands. BACKLOG_CWD tells the CLI where the project root is.
_BACKLOG_CWD = os.environ.get("BACKLOG_CWD", "/app" if os.environ.get("DOCKER") else ".")
_BACKLOG_ENV = {**os.environ, "BACKLOG_CWD": _BACKLOG_CWD}

# Initialize the backlog project on startup (idempotent — no-ops if already initialized).
try:
    subprocess.run(
        ["backlog", "init", "grug", "--defaults"],
        capture_output=True,
        env=_BACKLOG_ENV,
    )
except FileNotFoundError:
    print("[backlog] backlog CLI not found — skipping init. Install with: npm install -g backlog.md")

def _backlog(*args):
    """Run a backlog CLI command, return stdout. Raises CalledProcessError on failure."""
    return subprocess.check_output(
        ["backlog"] + list(args),
        stderr=subprocess.STDOUT,
        text=True,
        env=_BACKLOG_ENV,
    ).strip()

def backlog_list_tasks(status=None):
    args = ["task", "list", "--plain"]
    if status:
        args += ["-s", status]
    return _backlog(*args)

def backlog_search_tasks(query, status=None, priority=None):
    args = ["search", query, "--plain"]
    if status:
        args += ["--status", status]
    if priority:
        args += ["--priority", priority]
    return _backlog(*args)

def backlog_create_task(title, description=None, priority=None, assignee=None, acceptance_criteria=None):
    args = ["task", "create", title]
    if description:
        args += ["-d", description]
    if priority:
        args += ["--priority", priority]
    if assignee:
        args += ["-a", f"@{assignee}"]
    if acceptance_criteria:
        args += ["--ac", acceptance_criteria]
    return _backlog(*args)

def backlog_edit_task(task_id, status=None, append_notes=None):
    args = ["task", "edit", str(task_id)]
    if status:
        args += ["-s", status]
    if append_notes:
        args += ["--append-notes", append_notes]
    return _backlog(*args)

_backlog_browser_proc = None

def backlog_start_browser():
    global _backlog_browser_proc
    if _backlog_browser_proc is not None and _backlog_browser_proc.poll() is None:
        port = os.environ.get("BACKLOG_DASHBOARD_PORT", "6420")
        return f"Backlog dashboard is already running at http://localhost:{port}"
    port = os.environ.get("BACKLOG_DASHBOARD_PORT", "6420")
    _backlog_browser_proc = subprocess.Popen(
        ["backlog", "browser", "--port", port, "--no-open"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        env=_BACKLOG_ENV,
    )
    return f"Backlog dashboard started at http://localhost:{port}"

# 2. Register Python Tools mapping to Storage layer
registry.register_python_tool(
    name="add_note",
    schema={
        "description": "Save an insight, thought, or generic memory permanently.",
        "type": "object",
        "properties": {
            "content": {"type": "string"},
            "tags": {"type": "array", "items": {"type": "string"}}
        },
        "required": ["content"]
    },
    func=storage.add_note
)
registry.register_python_tool(
    name="get_recent_notes",
    schema={"description": "Fetch the most recent temporal notes submitted to the system.", "type": "object", "properties": {"limit": {"type": "integer"}}},
    func=storage.get_recent_notes
)
registry.register_python_tool(
    name="query_memory",
    schema={"description": "Perform an AI semantic vector search against the entire historical memory database.", "type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]},
    func=vector_memory.query_memory
)

# Backlog.md task board tools
registry.register_python_tool(
    name="backlog_start_browser",
    schema={
        "description": "Start the Backlog.md web dashboard in the background. Safe to call multiple times — no-ops if already running.",
        "type": "object",
        "properties": {}
    },
    func=backlog_start_browser,
    destructive=False
)
registry.register_python_tool(
    name="backlog_list_tasks",
    schema={
        "description": "List tasks on the project board. Optionally filter by status (e.g. 'In Progress', 'Todo', 'Done').",
        "type": "object",
        "properties": {
            "status": {"type": "string", "description": "Filter by task status"}
        }
    },
    func=backlog_list_tasks,
    destructive=False
)
registry.register_python_tool(
    name="backlog_search_tasks",
    schema={
        "description": "Fuzzy-search tasks and documents on the project board by keyword.",
        "type": "object",
        "properties": {
            "query": {"type": "string"},
            "status": {"type": "string"},
            "priority": {"type": "string", "enum": ["high", "medium", "low"]}
        },
        "required": ["query"]
    },
    func=backlog_search_tasks,
    destructive=False
)
registry.register_python_tool(
    name="backlog_create_task",
    schema={
        "description": "Create a new task on the project board. Requires human approval before executing.",
        "type": "object",
        "properties": {
            "title": {"type": "string"},
            "description": {"type": "string"},
            "priority": {"type": "string", "enum": ["high", "medium", "low"]},
            "assignee": {"type": "string", "description": "Username without the @ prefix"},
            "acceptance_criteria": {"type": "string"}
        },
        "required": ["title"]
    },
    func=backlog_create_task,
    destructive=True
)
registry.register_python_tool(
    name="backlog_edit_task",
    schema={
        "description": "Update an existing task's status or append notes. Requires human approval before executing.",
        "type": "object",
        "properties": {
            "task_id": {"type": "string", "description": "Task ID (e.g. '1' or 'task-1')"},
            "status": {"type": "string"},
            "append_notes": {"type": "string"}
        },
        "required": ["task_id"]
    },
    func=backlog_edit_task,
    destructive=True
)

# 3. Mount Router
router = GrugRouter(registry)

# Read and stitch system prompts
base_prompt = load_prompt_files("prompts")

def _sweep_pending():
    """Drop PENDING entries older than PENDING_TTL. Opportunistic sweep."""
    now = datetime.now()
    stale = [k for k, v in PENDING.items() if now - v["created_at"] > PENDING_TTL]
    for k in stale:
        PENDING.pop(k, None)

@app.event("message")
def handle_message(event, say, client):
    if event.get("subtype"):
        return

    text = event.get("text")
    if not text:
        return

    _sweep_pending()

    channel_id = event.get("channel")
    ts = event.get("ts")

    try:
        client.reactions_add(channel=channel_id, timestamp=ts, name="thought_balloon")
    except Exception as e:
        print(f"Failed to add reaction: {e}")

    recent_context = storage.get_recent_notes(limit=10)
    if not recent_context:
        recent_context = "No recent memory. The cave is empty."

    result = router.route_message(
        user_message=text,
        context=recent_context,
        compression_mode="FULL",
        base_system_prompt=base_prompt
    )

    try:
        client.reactions_remove(channel=channel_id, timestamp=ts, name="thought_balloon")
    except Exception:
        pass

    if result.requires_approval:
        key = str(uuid.uuid4())
        PENDING[key] = {
            "tool_name": result.tool_name,
            "arguments": result.arguments,
            "channel": channel_id,
            "user": event.get("user"),
            "created_at": datetime.now(),
        }
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
                        "value": key
                    },
                    {
                        "type": "button",
                        "text": {"type": "plain_text", "text": "Deny"},
                        "style": "danger",
                        "action_id": "grug_deny",
                        "value": key
                    }
                ]
            }
        ]
        client.chat_postMessage(
            channel=channel_id,
            text=f"Grug wants to run {result.tool_name}. Approve?",
            blocks=blocks
        )
        return

    say(result.output)

@app.action("grug_approve")
def handle_approve(ack, body, client):
    ack()
    key = body["actions"][0]["value"]
    pending = PENDING.pop(key, None)
    channel = body["channel"]["id"]
    user = body["user"]["id"]

    if not pending:
        client.chat_postMessage(channel=channel, text="No pending call found (expired or already handled).")
        return

    result = registry.execute(pending["tool_name"], pending["arguments"], skip_hitl=True)
    status_prefix = "" if result.success else ":x: "
    client.chat_postMessage(
        channel=channel,
        text=f"{status_prefix}<@{user}> approved `{pending['tool_name']}`: {result.output}"
    )

@app.action("grug_deny")
def handle_deny(ack, body, client):
    ack()
    key = body["actions"][0]["value"]
    pending = PENDING.pop(key, None)
    channel = body["channel"]["id"]
    user = body["user"]["id"]

    tool_name = pending["tool_name"] if pending else "unknown"
    client.chat_postMessage(
        channel=channel,
        text=f":no_entry_sign: <@{user}> denied `{tool_name}`. Cancelled."
    )

if __name__ == "__main__":
    print("Grug is awakening...")
    vector_memory.start_background_indexer()
    try:
        SocketModeHandler(app, os.environ.get("SLACK_APP_TOKEN", "mock_app_token")).start()
    except Exception as e:
        print("Missing valid Slack tokens to boot websocket listener. Grug sleeps.")
