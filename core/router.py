"""GrugRouter — the core routing engine.

Shortcut check → build prompt → call LLM → parse JSON → dispatch to registry.
"""

import os
import re
import json
import threading
from typing import Optional
from datetime import datetime, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
from core.config import config
from core.registry import ToolRegistry, ToolExecutionResult, load_prompt_files
from tools.system import ask_for_clarification, reply_to_user, list_capabilities


class GrugRouter:

    def __init__(self, registry: ToolRegistry, storage=None, llm_client=None):
        self.registry = registry
        self.storage = storage
        self.llm_client = llm_client
        self._request_state = threading.local()

        # Prompt hot-reload
        self._prompt_dir = "prompts"
        self._prompt_mtimes = {}
        self._cached_base_prompt = ""
        self._reload_prompts()

        # Register built-in tools and category descriptions
        self.register_core_tools()

    def _reload_prompts(self):
        try:
            self._cached_base_prompt = load_prompt_files(self._prompt_dir)
        except FileNotFoundError:
            pass
        for name in ["system.md", "rules.md", "schema_examples.md"]:
            path = os.path.join(self._prompt_dir, name)
            try:
                self._prompt_mtimes[name] = os.stat(path).st_mtime
            except OSError:
                self._prompt_mtimes[name] = 0

    def _check_prompt_reload(self):
        for name, old_mtime in self._prompt_mtimes.items():
            path = os.path.join(self._prompt_dir, name)
            try:
                if os.stat(path).st_mtime > old_mtime:
                    self._reload_prompts()
                    return
            except OSError:
                continue

    def register_core_tools(self):
        # Category descriptions
        self.registry.register_category_description("NOTES", "save a note, or search old notes")
        self.registry.register_category_description("TASKS", "add a task, edit a task, list tasks, or get a board summary")
        self.registry.register_category_description("SYSTEM", "chat, ask for help, or see what Grug can do")
        self.registry.register_category_description("SCHEDULE", "add, list, or cancel a scheduled task or reminder")

        self.registry.register_python_tool(
            name="ask_for_clarification",
            schema={
                "description": "[CHAT] Output ONLY when you need more details from the user to act on a board/note/task request (e.g. missing title, unclear which task to edit, ambiguous date). Do NOT use for factual trivia or chit-chat — those go to reply_to_user. The `reason_for_confusion` MUST be written in warm caveman voice (e.g. 'Grug need more. Which task you mean?').",
                "type": "object",
                "properties": {
                    "reason_for_confusion": {"type": "string"}
                },
                "required": ["reason_for_confusion"]
            },
            func=ask_for_clarification,
            category="SYSTEM",
            friendly_name="Ask for clarification"
        )

        self.registry.register_python_tool(
            name="list_capabilities",
            schema={
                "description": "[META] Output ONLY when the user explicitly asks what tools/commands are available or what Grug can do (e.g. 'what can you do?', 'list your commands', 'help'). Do NOT use for greetings like 'hi' or 'hey grug' — those go to reply_to_user.",
                "type": "object",
                "properties": {},
                "required": []
            },
            func=lambda: list_capabilities(self.registry),
            category="SYSTEM",
            friendly_name="List capabilities"
        )

        self.registry.register_python_tool(
            name="reply_to_user",
            schema={
                "description": "[CHAT] Output this when holding conversations, brainstorming, providing analysis, or chatting with the user when no concrete action is requested.",
                "type": "object",
                "properties": {
                    "message": {"type": "string"}
                },
                "required": ["message"]
            },
            func=reply_to_user,
            category="SYSTEM",
            friendly_name="Chat with Grug"
        )


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
            # New think-then-act format
            if not isinstance(actions, list):
                actions = [actions]
        else:
            # Legacy single-tool format — wrap it
            actions = [call_data]

        # Routing trace (log thinking + all actions)
        try:
            trace_entry = json.dumps({
                "ts": datetime.now().isoformat(),
                "user_msg": user_message[:200],
                "thinking": thinking[:500] if thinking else "",
                "actions": [{"tool": a.get("tool"), "args": a.get("arguments", {}),
                             "confidence": a.get("confidence_score", 0)} for a in actions],
            })
            trace_path = os.path.join("brain", "routing_trace.jsonl")
            os.makedirs(os.path.dirname(trace_path), exist_ok=True)
            with open(trace_path, "a", encoding="utf-8") as tf:
                tf.write(trace_entry + "\n")
        except Exception:
            pass

        # Execute each action sequentially, collect results
        outputs = []
        for action in actions:
            tool_name = action.get("tool")
            args = action.get("arguments", {})
            confidence_score = action.get("confidence_score", 0)

            # Low confidence gate
            if confidence_score <= config.llm.low_confidence_threshold and tool_name not in ("ask_for_clarification", "reply_to_user"):
                category = self.registry.get_category(tool_name)
                options = self.registry.get_category_description(category)
                outputs.append(f"Grug not sure what you mean. You want Grug to: {options}? Tell Grug which.")
                continue

            result = self.registry.execute(tool_name, args)

            # If any action needs HITL approval, return it immediately
            # (remaining actions won't execute until approval)
            if result.requires_approval:
                return result

            outputs.append(result.output)

        # Combine all outputs — for single actions this is just the one output
        combined = "\n".join(outputs) if len(outputs) > 1 else (outputs[0] if outputs else "")
        return ToolExecutionResult(success=True, output=combined)

    def _try_shortcut(self, user_message: str) -> Optional[ToolExecutionResult]:
        prefix = config.shortcuts.prefix
        if not user_message.startswith(prefix):
            return None

        rest = user_message[len(prefix):]
        parts = rest.split(None, 1)
        if not parts:
            return None

        alias = parts[0].lower()
        aliases_dict = vars(config.shortcuts.aliases)
        tool_name = aliases_dict.get(alias)

        if tool_name is None:
            return None

        user_text = parts[1] if len(parts) > 1 else ""
        if not user_text.strip():
            return ToolExecutionResult(
                success=True,
                output=f"Grug need words after {prefix}{alias}. What Grug do?"
            )

        # Load argument extraction prompt
        prompt_path = os.path.join(self._prompt_dir, "argument_extraction.md")
        with open(prompt_path, "r", encoding="utf-8") as f:
            extraction_prompt = f.read()

        tool_schema = None
        for s in self.registry.get_all_schemas():
            if s["name"] == tool_name:
                tool_schema = json.dumps(s["schema"], indent=2)
                break

        extraction_prompt = extraction_prompt.replace("{{TOOL_NAME}}", tool_name)
        extraction_prompt = extraction_prompt.replace("{{TOOL_SCHEMA}}", tool_schema or "{}")
        extraction_prompt = extraction_prompt.replace("{{USER_TEXT}}", user_text)

        messages = [{"role": "user", "content": user_text}]
        response_text = self.invoke_chat(extraction_prompt, messages)

        return self._parse_and_execute(response_text, user_message)

    @staticmethod
    def build_system_prompt(base_system_prompt: str, compression_mode: str = "ULTRA") -> str:
        """Interpolate placeholders in the base system prompt.

        Kept for backward compatibility with existing tests.
        """
        try:
            tz = ZoneInfo(config.scheduler.timezone)
        except ZoneInfoNotFoundError:
            tz = ZoneInfo("UTC")
        now_local = datetime.now(tz=tz)
        today = now_local.strftime("%Y-%m-%d")
        current_time = now_local.strftime("%H:%M %Z")
        prompt = base_system_prompt.replace("{{COMPRESSION_MODE}}", compression_mode)
        prompt = prompt.replace("{{CURRENT_DATE}}", today)
        prompt = prompt.replace("{{CURRENT_TIME}}", current_time)
        return prompt

    def route_message(self, user_message: str, system_prompt: str = "",
                      message_history: list = None,
                      context: str = None, compression_mode: str = "ULTRA",
                      base_system_prompt: str = None):
        """Route a user message through the LLM and execute the resulting tool call."""
        self._check_prompt_reload()

        # Handle legacy callers
        if base_system_prompt is not None or context is not None:
            if base_system_prompt is not None:
                system_prompt = self.build_system_prompt(base_system_prompt, compression_mode)
            if message_history is None:
                message_history = [{"role": "user", "content": user_message}]
        elif message_history is None:
            message_history = [{"role": "user", "content": user_message}]

        self._request_state.user_message = user_message

        try:
            # Fast path: shortcut prefix routing
            shortcut_result = self._try_shortcut(user_message)
            if shortcut_result is not None:
                return shortcut_result

            # Normal routing: build tools block and invoke LLM
            tools_str = json.dumps(self.registry.get_all_schemas(), indent=2)
            augmented_system = (
                f"{system_prompt}\n\n"
                f"TOOLS:\n{tools_str}\n\n"
                f'OUTPUT VALID JSON ONLY. Use the format: {{"thinking": "your reasoning", "actions": [{{"tool": "tool_name", "arguments": {{}}, "confidence_score": N}}]}}'
            )

            response_text = self.invoke_chat(augmented_system, message_history)
            return self._parse_and_execute(response_text, user_message)
        finally:
            self._request_state.user_message = None
