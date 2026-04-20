"""Grug entrypoint — wires components together and boots the Slack listener."""

import os
import threading
from slack_bolt import App
from slack_bolt.adapter.socket_mode import SocketModeHandler
from core.config import config
from core.llm import OllamaClient
from core.storage import GrugStorage
from core.sessions import SessionStore
from core.summarizer import Summarizer
from core.vectors import VectorMemory
from core.registry import ToolRegistry
from core.utils import load_prompt_files
from core.router import GrugRouter
from core.scheduler import ScheduleStore
from core.orchestrator import Orchestrator
from core.context import load_summary_files, build_system_prompt, find_turn_boundary, auto_offload_pruned_turns
from core.queue import GrugMessageQueue
from tools.tasks import TaskList
from tools.system import register_tools as register_system_tools
from tools.notes import register_tools as register_note_tools
from tools.tasks import register_tools as register_task_tools
from tools.scheduler_tools import register_tools as register_scheduler_tools
from tools.health import register_tools as register_health_tools
from adapters.slack import SlackAdapter
from workers.background import boot_summarize, idle_sweep_loop, nightly_summarize_loop, scheduler_poll_loop

# ---------------------------------------------------------------------------
# Init components
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
task_list = TaskList(tasks_file=os.path.join(config.storage.base_dir, "tasks.md"), storage=storage)
router = GrugRouter(registry, storage, llm_client=llm_client)
base_prompt = load_prompt_files("prompts")

# ---------------------------------------------------------------------------
# Register tools
# ---------------------------------------------------------------------------
register_system_tools(registry, router)
register_note_tools(registry, storage, llm_client, vector_memory, config.storage.base_dir)
register_task_tools(registry, task_list, storage)

# ---------------------------------------------------------------------------
# Orchestrator + Queue + Adapter
# ---------------------------------------------------------------------------
orchestrator = Orchestrator(
    router=router,
    registry=registry,
    session_store=session_store,
    storage=storage,
    summarizer=summarizer,
    vector_memory=vector_memory,
    config=config,
    load_summary_files=load_summary_files,
    build_system_prompt=build_system_prompt,
    find_turn_boundary=find_turn_boundary,
    auto_offload_pruned_turns=auto_offload_pruned_turns,
    base_prompt=base_prompt,
)

# SlackAdapter creates its own process_queued_message callback
slack_adapter = SlackAdapter(app, orchestrator, session_store, message_queue=None)

message_queue = GrugMessageQueue(
    process_fn=slack_adapter.process_queued_message,
    worker_count=config.queue.worker_count,
)
slack_adapter.message_queue = message_queue

# Tools that depend on message_queue
register_health_tools(registry, vector_memory, session_store, message_queue, schedule_store, llm_client, config.storage.base_dir)
register_scheduler_tools(registry, schedule_store, router, config)

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
