"""GrugRouter — the core routing engine.

Build prompt → call LLM (native tools) → dispatch tool_calls to registry.
"""

import threading
from contextlib import contextmanager
from core.registry import ToolRegistry, ToolExecutionResult
from core.interfaces import LLMResponse


_REQUEST_FIELDS = (
    "_schedule_channel", "_schedule_user", "_schedule_thread_ts",
    "_dispatch_session_id", "_dispatch_user_id", "_dispatch_on_result",
)


class GrugRouter:

    def __init__(self, registry: ToolRegistry, storage=None, chat_worker=None):
        self.registry = registry
        self.storage = storage
        self.chat_worker = chat_worker
        self._request_state = threading.local()

    @contextmanager
    def request_state(self, *, session_id=None, user_id=None, channel_id=None,
                      on_result=None):
        """Bind per-request threadlocals so tool closures can read them.

        The fields are unconditionally cleared on exit, even on exception, so
        leaks don't bleed across queue worker iterations.
        """
        rs = self._request_state
        rs._schedule_channel = channel_id
        rs._schedule_user = user_id
        rs._schedule_thread_ts = session_id
        rs._dispatch_session_id = session_id
        rs._dispatch_user_id = user_id
        rs._dispatch_on_result = on_result
        try:
            yield
        finally:
            for f in _REQUEST_FIELDS:
                setattr(rs, f, None)

    # ------------------------------------------------------------------
    # LLM delegation (methods kept so tests can mock them)
    # ------------------------------------------------------------------

    def invoke_chat(self, system_prompt: str, messages: list, tools: list = None) -> LLMResponse:
        if self.chat_worker:
            return self.chat_worker.chat(system_prompt, messages, tools=tools)
        print("[router] error: chat worker not configured")
        return LLMResponse(
            content="Chat worker not configured",
            tool_calls=[]
        )

    def invoke_generate(self, prompt: str) -> str:
        if self.chat_worker:
            return self.chat_worker.generate(prompt)
        return ""

    # ------------------------------------------------------------------
    # Routing
    # ------------------------------------------------------------------

    def _parse_and_execute(self, llm_response: LLMResponse, user_message: str,
                           registry: ToolRegistry = None) -> ToolExecutionResult:
        if registry is None:
            registry = self.registry
        # Delegate trace logging to storage
        if self.storage:
            self.storage.log_routing_trace(user_message, llm_response.content, llm_response.tool_calls)

        # Execute each action sequentially, collect results
        _chat_tools = {"ask_for_clarification", "reply_to_user"}
        tool_outputs = []   # results from non-chat tools
        reply_outputs = []  # results from reply_to_user / ask_for_clarification
        tool_error = False

        for action in llm_response.tool_calls:
            tool_name = action.get("tool")
            args = action.get("arguments", {})

            # Skip reply_to_user when a real tool already returned an error
            if tool_name in _chat_tools and tool_error:
                continue

            result = registry.execute(tool_name, args)

            # If any action needs HITL approval, return it immediately
            if result.requires_approval:
                return result

            if not result.success:
                tool_error = True

            if result.output:
                if tool_name in _chat_tools:
                    reply_outputs.append(result.output)
                else:
                    tool_outputs.append(result.output)

        # Build combined output — tool output wins over reply output
        if tool_outputs:
            combined = "\n".join(tool_outputs)
        else:
            combined = "\n".join(reply_outputs) if reply_outputs else ""

        return ToolExecutionResult(
            success=True,
            output=combined,
            tool_output=combined if tool_outputs else None
        )

    def route_message(self, user_message: str, system_prompt: str = "",
                      message_history: list = None, max_steps: int = 1,
                      agent_container=None, cancel_event=None):
        """Route a user message through the LLM and execute tool calls.

        When max_steps > 1, loops: LLM → tool → LLM until the LLM replies
        to the user, hits the step limit, or triggers a circuit breaker.

        If ``agent_container`` is provided, its scoped registry and worker are
        used in place of the router's defaults. If ``cancel_event`` is set
        between iterations, the loop exits early with a cancellation marker.
        """
        if message_history is None:
            message_history = [{"role": "user", "content": user_message}]

        self._request_state.user_message = user_message

        active_registry = agent_container.registry if agent_container else self.registry
        active_worker = agent_container.worker if agent_container else self.chat_worker

        def _invoke(sys_prompt, msgs, tools):
            # Use container's worker when provided; otherwise default behavior.
            if active_worker is not None and agent_container is not None:
                return active_worker.chat(sys_prompt, msgs, tools=tools)
            return self.invoke_chat(sys_prompt, msgs, tools=tools)

        try:
            schemas = active_registry.get_all_schemas()
            recent_calls = []  # circuit breaker: track (tool_name, args_hash) tuples

            for step in range(max_steps):
                if cancel_event is not None and cancel_event.is_set():
                    return ToolExecutionResult(
                        success=False,
                        output="Task cancelled",
                        tool_output=None,
                    )
                llm_response = _invoke(system_prompt, message_history, schemas)
                result = self._parse_and_execute(llm_response, user_message,
                                                 registry=active_registry)

                # Always return immediately on: HITL approval, no tool output, or last step
                if result.requires_approval:
                    return result
                if result.tool_output is None:
                    return result
                if step == max_steps - 1:
                    return result

                # Circuit breaker: detect repeated identical tool calls
                call_sig = str(llm_response.tool_calls)
                if call_sig in recent_calls:
                    print(f"[router] circuit breaker: repeated tool call, stopping step loop")
                    return result
                recent_calls.append(call_sig)

                # Feed tool result back into history for next iteration
                message_history = list(message_history)  # don't mutate caller's list
                message_history.append({"role": "assistant", "content": llm_response.content or ""})
                message_history.append({"role": "tool", "content": result.tool_output})

            return result
        finally:
            self._request_state.user_message = None
