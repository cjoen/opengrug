"""dispatch_task — route work from chat_agent to an Expert Agent via the queue.

Per-request context (`session_id`, `user_id`, `on_result` callback) is read off
the router's threadlocal `_request_state`, mirroring how scheduler_tools works.
"""

from core.task import Task, TaskPriority


def register_tools(registry, task_queue=None, agents=None, router=None, holder=None):
    """Register dispatch_task.

    ``holder`` is an optional mutable dict used for late binding so that the
    handler — captured by scoped registries snapshotted at AgentFactory time —
    can pick up the live ``task_queue`` / ``agents`` / ``router`` once wiring
    completes. Pass the same holder to subsequent ``register_tools`` calls and
    they will mutate it in place rather than re-registering.
    """
    if holder is None:
        holder = {"task_queue": task_queue, "agents": agents, "router": router}
    else:
        if task_queue is not None:
            holder["task_queue"] = task_queue
        if agents is not None:
            holder["agents"] = agents
        if router is not None:
            holder["router"] = router
        # Already registered — just mutate the holder.
        if "dispatch_task" in registry._python_tools:
            return holder

    def _dispatch_handler(agent, context, plan=None):
        tq = holder.get("task_queue")
        ags = holder.get("agents")
        rtr = holder.get("router")
        if tq is None:
            return f"dispatch_task stub: queue not wired. Would route to '{agent}'."
        if ags is not None and agent not in ags:
            available = ", ".join(sorted(ags)) if ags else "(none)"
            return f"unknown agent '{agent}'. Available: {available}"

        session_id = ""
        user_id = ""
        on_result = None
        if rtr is not None:
            session_id = getattr(rtr._request_state, "_dispatch_session_id", "") or ""
            user_id = getattr(rtr._request_state, "_dispatch_user_id", "") or ""
            on_result = getattr(rtr._request_state, "_dispatch_on_result", None)

        task = Task(
            session_id=session_id,
            user_id=user_id,
            agent_name=agent,
            context=context,
            priority=TaskPriority.URGENT,
            plan=list(plan) if plan else None,
            on_result=on_result,
        )
        tq.enqueue(task)
        return f"Dispatched to {agent}. Task {task.id[:8]} queued; result will follow."

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
        func=_dispatch_handler,
        category="SYSTEM",
        friendly_name="Dispatch task to expert agent",
    )
    return holder
