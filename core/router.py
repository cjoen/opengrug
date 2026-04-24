"""GrugRouter — the core routing engine.

Build prompt → call LLM (native tools) → dispatch tool_calls to registry.
"""

import threading
from core.registry import ToolRegistry, ToolExecutionResult
from core.interfaces import LLMResponse
from core.utils import load_prompt_files


class GrugRouter:

    def __init__(self, registry: ToolRegistry, storage=None, llm_client=None):
        self.registry = registry
        self.storage = storage
        self.llm_client = llm_client
        self._request_state = threading.local()

        # Cache base prompt (reloaded via reload_prompts tool)
        self._prompt_dir = "prompts"
        self._cached_base_prompt = ""
        try:
            self._cached_base_prompt = load_prompt_files(self._prompt_dir)
        except FileNotFoundError:
            pass

    # ------------------------------------------------------------------
    # LLM delegation (methods kept so tests can mock them)
    # ------------------------------------------------------------------

    def invoke_chat(self, system_prompt: str, messages: list, tools: list = None) -> LLMResponse:
        if self.llm_client:
            return self.llm_client.chat(system_prompt, messages, tools=tools)
        print("[router] error: LLM client not configured")
        return LLMResponse(
            content="LLM client not configured",
            tool_calls=[]
        )

    def invoke_gemma_text(self, prompt: str) -> str:
        if self.llm_client:
            return self.llm_client.generate(prompt)
        return ""

    # ------------------------------------------------------------------
    # Routing
    # ------------------------------------------------------------------

    def _parse_and_execute(self, llm_response: LLMResponse, user_message: str) -> ToolExecutionResult:
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

            result = self.registry.execute(tool_name, args)

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
                      message_history: list = None, max_steps: int = 1):
        """Route a user message through the LLM and execute tool calls.

        When max_steps > 1, loops: LLM → tool → LLM until the LLM replies
        to the user, hits the step limit, or triggers a circuit breaker.
        """
        if message_history is None:
            message_history = [{"role": "user", "content": user_message}]

        self._request_state.user_message = user_message

        try:
            schemas = self.registry.get_all_schemas()
            recent_calls = []  # circuit breaker: track (tool_name, args_hash) tuples

            for step in range(max_steps):
                llm_response = self.invoke_chat(system_prompt, message_history, tools=schemas)
                result = self._parse_and_execute(llm_response, user_message)

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
