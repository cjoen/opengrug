import os
import json
from slack_bolt import App
from slack_bolt.adapter.socket_mode import SocketModeHandler
from core.storage import GrugStorage
from core.vectors import VectorMemory
from core.orchestrator import ToolRegistry, GrugRouter

app = App(token=os.environ.get("SLACK_BOT_TOKEN", "mock_token"))

# 1. Initialize Components
storage = GrugStorage(base_dir="/app/brain" if os.environ.get("DOCKER") else "./brain")
vector_memory = VectorMemory(db_path="/app/brain/memory.db" if os.environ.get("DOCKER") else "./brain/memory.db")
registry = ToolRegistry()

# 2. Register Python Tools mapping to Storage layer
registry.register_python_tool(
    name="save_insight",
    schema={"properties": {"insight": {"type": "string"}}, "required": ["insight"]},
    func=lambda insight: storage.append_log("insight", {"insight": insight})
)
registry.register_python_tool(
    name="add_task",
    schema={"properties": {"description": {"type": "string"}}},
    func=lambda description: storage.append_log("task", {"description": description})
)
registry.register_python_tool(
    name="query_memory",
    schema={"properties": {"query": {"type": "string"}}, "required": ["query"]},
    func=lambda query: vector_memory.query_memory(query)
)

# 3. Mount Router
router = GrugRouter(registry)

# Read base system prompt
with open("prompts/system.md", "r", encoding="utf-8") as f:
    base_prompt = f.read()

@app.event("message")
def handle_message(event, say):
    text = event.get("text")
    if not text: return
    
    # Intercept message and route through Grug
    result = router.route_message(
        user_message=text, 
        context="Slack integration.", 
        compression_mode="ULTRA",  # Force caveman mode
        base_system_prompt=base_prompt
    )
    
    say(result.output)

if __name__ == "__main__":
    print("Grug is awakening...")
    vector_memory.index_markdown_directory() # Trigger a background cache generation block
    try:
        SocketModeHandler(app, os.environ.get("SLACK_APP_TOKEN", "mock_app_token")).start()
    except Exception as e:
        print("Missing valid Slack tokens to boot websocket listener. Grug sleeps.")
