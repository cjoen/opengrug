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
    name="add_note",
    schema={
        "properties": {
            "content": {"type": "string"},
            "tags": {"type": "array", "items": {"type": "string"}}
        },
        "required": ["content"]
    },
    func=storage.add_note
)
registry.register_python_tool(
    name="add_task",
    schema={
        "properties": {
            "description": {"type": "string"},
            "due_date": {"type": "string"},
            "assignee": {"type": "string"}
        },
        "required": ["description"]
    },
    func=storage.add_task
)
registry.register_python_tool(
    name="get_recent_notes",
    schema={"properties": {"limit": {"type": "integer"}}},
    func=storage.get_recent_notes
)
registry.register_python_tool(
    name="query_memory",
    schema={"properties": {"query": {"type": "string"}}, "required": ["query"]},
    func=vector_memory.query_memory
)

# 3. Mount Router
router = GrugRouter(registry)

# Read base system prompt
with open("prompts/system.md", "r", encoding="utf-8") as f:
    base_prompt = f.read()

@app.event("message")
def handle_message(event, say, client):
    # Ignore message edits, deletions, or bot messages to prevent loops
    if event.get("subtype"):
        return
        
    text = event.get("text")
    if not text: 
        return
        
    channel_id = event.get("channel")
    ts = event.get("ts")
    
    # 1. Add "thinking" reaction
    try:
        client.reactions_add(
            channel=channel_id,
            timestamp=ts,
            name="thought_balloon"
        )
    except Exception as e:
        print(f"Failed to add reaction: {e}")
        
    # 2. Inject recent context from storage
    recent_context = storage.get_recent_notes(limit=10)
    if not recent_context:
        recent_context = "No recent memory. The cave is empty."
    
    # 3. Intercept message and route through Grug
    result = router.route_message(
        user_message=text, 
        context=recent_context, 
        compression_mode="ULTRA",  # Force caveman mode
        base_system_prompt=base_prompt
    )
    
    # 4. Remove thinking reaction
    try:
        client.reactions_remove(
            channel=channel_id,
            timestamp=ts,
            name="thought_balloon"
        )
    except Exception as e:
        pass
    
    # 5. Post response
    say(result.output)

if __name__ == "__main__":
    print("Grug is awakening...")
    vector_memory.index_markdown_directory() # Trigger a background cache generation block
    try:
        SocketModeHandler(app, os.environ.get("SLACK_APP_TOKEN", "mock_app_token")).start()
    except Exception as e:
        print("Missing valid Slack tokens to boot websocket listener. Grug sleeps.")
