"""GrugRouter — the core routing engine.

Build prompt → call LLM → parse JSON → dispatch to registry.
"""

import re
import json
import threading
from core.config import config
from core.registry import ToolRegistry, ToolExecutionResult
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

    def invoke_chat(self, system_prompt: str, messages: list) -> str:
        if self.llm_client:
            return self.llm_client.chat(system_prompt, messages)
        # Fallback for tests that don't inject a client
        return json.dumps({
            "tool": "ask_for_clarification",
            "arguments": {"reason_for_confusion": "Grug brain foggy. No LLM client."},
            "confidence_score": 0
        })

    def invoke_gemma_text(self, prompt: str) -> str:
        if self.llm_client:
            return self.llm_client.generate(prompt)
        return ""

    # ------------------------------------------------------------------
    # Routing
    # ------------------------------------------------------------------

    def _parse_and_execute(self, response_text: str, user_message: str) -> ToolExecutionResult:
        # Strip Gemma 4 thinking channel block if present
        response_text = re.sub(r"<\|channel>.*?<channel\|>", "", response_text, flags=re.DOTALL).strip()

        try:
            call_data = json.loads(response_text)
        except json.JSONDecodeError:
            return ToolExecutionResult(success=False, output="Edge model failed to emit valid JSON.")

        # Support both new format {"thinking": ..., "actions": [...]}
        # and legacy flat format {"tool": ..., "arguments": ...}
        thinking = call_data.get("thinking", "")
        actions = call_data.get("actions")

        if actions is not None:
            if not isinstance(actions, list):
                actions = [actions]
        else:
            actions = [call_data]

        # Delegate trace logging to storage
        if self.storage:
            self.storage.log_routing_trace(user_message, thinking, actions)

        # Execute each action sequentially, collect results
        _chat_tools = {"ask_for_clarification", "reply_to_user"}
        outputs = []
        tool_error = False
        for action in actions:
            tool_name = action.get("tool")
            args = action.get("arguments", {})
            confidence_score = action.get("confidence_score", 0)

            # Low confidence gate
            if confidence_score <= config.llm.low_confidence_threshold and tool_name not in _chat_tools:
                category = self.registry.get_category(tool_name)
                options = self.registry.get_category_description(category)
                outputs.append(f"Grug not sure what you mean. You want Grug to: {options}? Tell Grug which.")
                tool_error = True
                continue

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
                outputs.append(result.output)

        combined = "\n".join(outputs) if len(outputs) > 1 else (outputs[0] if outputs else "")
        return ToolExecutionResult(success=True, output=combined)

    def route_message(self, user_message: str, system_prompt: str = "",
                      message_history: list = None):
        """Route a user message through the LLM and execute the resulting tool call."""
        if message_history is None:
            message_history = [{"role": "user", "content": user_message}]

        self._request_state.user_message = user_message

        try:
            tools_str = json.dumps(self.registry.get_all_schemas(), indent=2)
            augmented_system = (
                f"{system_prompt}\n\n"
                f"TOOLS:\n{tools_str}\n\n"
                f'OUTPUT VALID JSON ONLY. Use the format: {{"thinking": "your reasoning", "actions": [{{"tool": "tool_name", "arguments": {{}}, "confidence_score": N}}]}}'
            )

            response_text = self.invoke_chat(augmented_system, message_history)
            result = self._parse_and_execute(response_text, user_message)
            if result.success:
                result.llm_response = response_text
            return result
        finally:
            self._request_state.user_message = None
