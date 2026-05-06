"""dispatch_task — schema and stub.

Wired in Phase 5.3 when TaskQueue is built. For now it returns a
placeholder string so the chat_agent can reference it without crashing.
"""


def register_tools(registry):
    """Register dispatch_task on the global registry under SYSTEM."""
    registry.register_python_tool(
        name="dispatch_task",
        schema={
            "description": "[SYSTEM] Route a task to a specialized Expert Agent. Use when the user asks for something that should be handled autonomously by a domain expert (e.g. research, deep analysis). Pass the agent name, a distilled context block, and an optional ordered plan.",
            "type": "object",
            "properties": {
                "agent": {"type": "string", "description": "Target agent name."},
                "context": {"type": "string", "description": "Distilled context for the agent — only what they need."},
                "plan": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Optional ordered to-do list for the agent.",
                },
            },
            "required": ["agent", "context"],
        },
        func=_dispatch_stub,
        category="SYSTEM",
        friendly_name="Dispatch task to expert agent",
    )


def _dispatch_stub(agent, context, plan=None):
    return f"dispatch_task stub: queue not yet wired (Phase 5.3). Would route to '{agent}'."
