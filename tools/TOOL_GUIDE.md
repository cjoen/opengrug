# Adding Tools to OpenGrug

This guide covers how to add new tools that Grug can use. Every tool follows the same pattern: write a function, register it, and tell the LLM about it.

## Quick Reference

```
1. Create your function in tools/<module>.py
2. Register it in app.py with registry.register_python_tool()
3. Add it to prompts/system.md tool list
4. Add it to ai-context.md tool categories
```

## Step 1: Write the Tool Function

Create a new file in `tools/` or add to an existing one. Your function:
- Accepts keyword arguments matching the JSON schema properties
- Accepts `**_kwargs` to ignore extra arguments the registry may pass
- Returns a string (displayed to the user via Slack)
- Raises exceptions on error (caught by the registry, returned as failure)

### Template

```python
"""Description of what this tool module does."""


def my_tool(dep1, dep2, arg_from_user="default", **_kwargs):
    """One-line description of what this tool does.

    Parameters before **_kwargs that don't come from the LLM are
    dependencies injected via functools.partial in app.py.
    Parameters that DO come from the LLM must match the JSON schema.
    """
    # Do the work
    result = dep1.some_method(arg_from_user)

    # Return a string — this is what the user sees
    return f"Done: {result}"
```

### Example: No-argument tool (health check style)

```python
def list_things(storage, **_kwargs):
    items = storage.get_all()
    if not items:
        return "No items found."
    return "\n".join(f"- {item}" for item in items)
```

### Example: Tool with user arguments

```python
def add_thing(storage, llm_client, title, description="", priority="medium", **_kwargs):
    # title and priority come from the LLM (defined in schema)
    # storage and llm_client are injected via partial
    storage.save(title, description, priority)
    return f"Added: {title} ({priority})"
```

## Step 2: Register in app.py

Import your function and register it with the tool registry.

```python
from functools import partial
from tools.my_module import my_tool

registry.register_python_tool(
    name="my_tool",                    # unique name the LLM uses to call it
    schema={
        "description": "[CATEGORY] When to use this tool. Be specific so the LLM routes correctly.",
        "type": "object",
        "properties": {
            "title": {"type": "string"},
            "priority": {
                "type": "string",
                "enum": ["high", "medium", "low"],
                "description": "Task priority level"
            }
        },
        "required": ["title"]          # omit for no-argument tools
    },
    func=partial(my_tool, storage, llm_client),  # inject dependencies
    category="CATEGORY",               # NOTES, TASKS, SYSTEM, SCHEDULE
    friendly_name="Human-readable name"  # shown in list_capabilities
)
```

### Registration parameters

| Parameter | Required | Description |
|---|---|---|
| `name` | yes | Snake_case identifier the LLM uses. Must be unique. |
| `schema` | yes | JSON Schema object. `description` is critical — this is what the LLM reads to decide when to use the tool. |
| `func` | yes | Python callable. Use `functools.partial` to inject dependencies. |
| `category` | no | Tool category: `NOTES`, `TASKS`, `SYSTEM`, `SCHEDULE`. Default: `SYSTEM`. Used for low-confidence routing. |
| `destructive` | no | If `True`, triggers HITL approval before execution. Default: `False`. |
| `friendly_name` | no | Human-readable name shown in `list_capabilities`. Default: same as `name`. |

### Schema description tips

The `description` field is the most important part — it tells the LLM when to use your tool vs others. Good descriptions:
- Start with `[CATEGORY]` tag matching the registered category
- Say **when** to use the tool, not just what it does
- Distinguish from similar tools (e.g., "Use for keyword search" vs "Use for semantic/fuzzy search")
- Keep it under ~50 words

### Dependency injection pattern

Dependencies the function needs (database handles, clients, config) are injected via `functools.partial` at registration time. The LLM never sees or provides these — only the `properties` in the schema are LLM-provided arguments.

```python
# Function signature:
def my_tool(storage, llm_client, user_arg, **_kwargs):
#           ^^^^^^^ ^^^^^^^^^^ ^^^^^^^^
#           injected via partial  from LLM schema

# Registration:
func=partial(my_tool, storage, llm_client)
#                     ^^^^^^^ ^^^^^^^^^^
#                     these get baked in
```

Available dependencies in app.py:
- `llm_client` — OllamaClient for LLM calls
- `storage` — GrugStorage for reading/writing daily notes
- `vector_memory` — VectorMemory for semantic search
- `session_store` — SessionStore for conversation sessions
- `schedule_store` — ScheduleStore for scheduled tasks
- `message_queue` — GrugMessageQueue
- `registry` — ToolRegistry (for tools that need to inspect other tools)
- `task_board` — TaskBoard for task CRUD
- `config` — GrugConfig singleton

## Step 3: Update prompts/system.md

Add your tool to the appropriate category in the `## Tool Categories` section:

```markdown
- [CATEGORY]: existing_tool, my_tool — updated description
```

## Step 4: Update ai-context.md

Add your tool to the matching category in the `### Tool Categories` section so future AI agents know about it.

## Checklist

- [ ] Function in `tools/<module>.py`, returns string, accepts `**_kwargs`
- [ ] Registered in `app.py` with schema, category, and friendly_name
- [ ] Schema `description` clearly tells the LLM when to use it
- [ ] Dependencies injected via `partial`, not imported globally
- [ ] Added to `prompts/system.md` tool category list
- [ ] Added to `ai-context.md` tool category list
- [ ] If destructive: `destructive=True` set in registration
