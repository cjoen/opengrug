import os
import json
import subprocess
import requests
from typing import Dict, Callable, Optional
from pydantic import BaseModel
from datetime import datetime
import threading
import anthropic
import jsonschema

def load_prompt_files(prompts_dir: str) -> str:
    """Concatenate system.md, rules.md, memory.md, schema_examples.md with headers."""
    filenames = ["system.md", "rules.md", "memory.md", "schema_examples.md"]
    parts = []
    for name in filenames:
        path = os.path.join(prompts_dir, name)
        with open(path, "r", encoding="utf-8") as f:
            parts.append(f"## {name}\n\n{f.read()}")
    return "\n\n".join(parts)

def _sanitize_untrusted(text: str, tag_name: str) -> str:
    """Strip any literal close-tag that would break XML-style delimiter framing."""
    close_tag = f"</{tag_name}>"
    if close_tag in text:
        text = text.replace(close_tag, f"[{tag_name}_tag_stripped]")
    return text

class ToolExecutionResult(BaseModel):
    success: bool
    output: str
    requires_approval: bool = False
    tool_name: Optional[str] = None
    arguments: Optional[dict] = None

class ToolRegistry:
    def __init__(self):
        # Maps tool name to python tuple: (schema_dict, python_callable, is_destructive)
        self._python_tools: Dict[str, tuple] = {}
        # Maps tool name to CLI tuple: (schema_dict, cli_base_command, is_destructive)
        self._cli_tools: Dict[str, tuple] = {}

    def register_python_tool(self, name: str, schema: dict, func: Callable, destructive: bool = False):
        self._python_tools[name] = (schema, func, destructive)

    def register_cli_tool(self, name: str, schema: dict, base_command: list, destructive: bool = True):
        self._cli_tools[name] = (schema, base_command, destructive)

    def get_all_schemas(self):
        schemas = []
        for name, data in self._python_tools.items():
            schemas.append({"name": name, "schema": data[0]})
        for name, data in self._cli_tools.items():
            schemas.append({"name": name, "schema": data[0]})
        return schemas

    def execute(self, tool_name: str, arguments: dict, skip_hitl=False) -> ToolExecutionResult:
        if tool_name in self._python_tools:
            schema, func, is_destructive = self._python_tools[tool_name]

            # Validate arguments against registered schema
            try:
                jsonschema.Draft7Validator(schema).validate(arguments)
            except jsonschema.ValidationError as e:
                return ToolExecutionResult(
                    success=False,
                    output=f"Invalid args for {tool_name}: {e.message}"
                )

            if is_destructive and not skip_hitl:
                return ToolExecutionResult(
                    success=True,
                    output=f"Waiting for human approval to mutate state with {tool_name}.",
                    requires_approval=True,
                    tool_name=tool_name,
                    arguments=arguments
                )

            try:
                res = func(**arguments)
                return ToolExecutionResult(success=True, output=str(res))
            except Exception as e:
                return ToolExecutionResult(success=False, output=str(e))

        elif tool_name in self._cli_tools:
            schema, base_command, is_destructive = self._cli_tools[tool_name]

            try:
                jsonschema.Draft7Validator(schema).validate(arguments)
            except jsonschema.ValidationError as e:
                return ToolExecutionResult(
                    success=False,
                    output=f"Invalid args for {tool_name}: {e.message}"
                )

            if is_destructive and not skip_hitl:
                 return ToolExecutionResult(
                     success=True,
                     output=f"Waiting for human approval to run CLI tool {tool_name}.",
                     requires_approval=True,
                     tool_name=tool_name,
                     arguments=arguments
                 )

            # Sandboxed Subprocess Builder
            cmd = base_command.copy()
            for key, val in arguments.items():
                if isinstance(val, bool):
                    if val: cmd.append(f"--{key}")
                else:
                    cmd.append(f"--{key}")
                    cmd.append(str(val))

            try:
                output = subprocess.check_output(cmd, stderr=subprocess.STDOUT, text=True)
                return ToolExecutionResult(success=True, output=output)
            except subprocess.CalledProcessError as e:
                return ToolExecutionResult(success=False, output=e.output)
            except Exception as e:
                return ToolExecutionResult(success=False, output=str(e))
        else:
            return ToolExecutionResult(success=False, output=f"Tool {tool_name} not found in registry.")

class GrugRouter:
    def __init__(self, registry: ToolRegistry):
        self.registry = registry
        self.frontier_available = bool(os.getenv("CLAUDE_API_KEY", ""))
        self._request_state = threading.local()
        self._base_system_prompt = ""
        self.register_core_tools()

    def register_core_tools(self):
        # The primary fallback tool
        self.registry.register_python_tool(
            name="escalate_to_frontier",
            schema={
                "description": "Route complex requests to Claude Opus.",
                "type": "object",
                "properties": {
                    "reason_for_escalation": {"type": "string"}
                },
                "required": ["reason_for_escalation"]
            },
            func=self.execute_frontier_escalation,
        )

        # Clarification tool
        self.registry.register_python_tool(
            name="ask_for_clarification",
            schema={
                "description": "Output ONLY when you need more details from the user to act on a board/note/task request (e.g. missing title, unclear which task to edit, ambiguous date). Do NOT use for factual trivia or chit-chat — those go to reply_to_user. The `reason_for_confusion` MUST be written in warm caveman voice (e.g. 'Grug need more. Which task you mean?').",
                "type": "object",
                "properties": {
                    "reason_for_confusion": {"type": "string"}
                },
                "required": ["reason_for_confusion"]
            },
            func=self.execute_ask_for_clarification,
            destructive=False
        )

        # Help / CLI Capabilities tool
        self.registry.register_python_tool(
            name="list_capabilities",
            schema={
                "description": "Output ONLY when the user explicitly asks what tools/commands are available or what Grug can do (e.g. 'what can you do?', 'list your commands', 'help'). Do NOT use for greetings like 'hi' or 'hey grug' — those go to reply_to_user.",
                "type": "object",
                "properties": {},
                "required": []
            },
            func=self.execute_list_capabilities,
            destructive=False
        )

        # Conversational tool
        self.registry.register_python_tool(
            name="reply_to_user",
            schema={
                "description": "Output this when holding conversations, brainstorming, providing analysis, or chatting with the user when no concrete action is requested.",
                "type": "object",
                "properties": {
                    "message": {"type": "string"}
                },
                "required": ["message"]
            },
            func=self.execute_reply_to_user,
            destructive=False
        )

        # Board summary tool — fetches tasks and asks Gemma to summarize them
        self.registry.register_python_tool(
            name="summarize_board",
            schema={
                "description": "Give a short natural-language summary of tasks on the project board. Use when the user asks for an overview, summary, or 'state of the board'. Optionally filter by status.",
                "type": "object",
                "properties": {
                    "status": {"type": "string", "description": "Filter by task status (e.g. 'Todo', 'In Progress', 'Done')"}
                }
            },
            func=self.execute_summarize_board,
            destructive=False
        )

    def execute_reply_to_user(self, message: str):
        return message

    def execute_summarize_board(self, status=None):
        args = {}
        if status:
            args["status"] = status

        result = self.registry.execute("backlog_list_tasks", args)
        if not result.success:
            return f"Grug cannot see board: {result.output}"

        raw = (result.output or "").strip()
        if not raw:
            return "Grug see empty board. No tasks here."

        summary_prompt = (
            "You are Grug, a friendly caveman. Read the task list below and write a short "
            "2-3 sentence summary in caveman voice. Mention total count and any patterns you see "
            "(lots in progress, many done, urgent items, etc). Plain text only — no JSON, no lists.\n\n"
            f"TASKS:\n{raw}\n\nGRUG SUMMARY:"
        )
        summary = self.invoke_gemma_text(summary_prompt)
        if not summary:
            summary = "Grug brain foggy. Here what Grug see:"

        return f"{summary}\n\n--- Full list ---\n{raw}"

    def execute_list_capabilities(self):
        hidden_tools = {"escalate_to_frontier", "ask_for_clarification", "list_capabilities", "reply_to_user"}
        
        friendly_names = {
            "add_note": "Save a note",
            "get_recent_notes": "Read recent notes",
            "query_memory": "Search memory",
            "backlog_start_browser": "Open the task dashboard",
            "backlog_list_tasks": "List tasks",
            "backlog_search_tasks": "Search tasks",
            "backlog_create_task": "Create a task",
            "backlog_edit_task": "Update a task",
            "summarize_board": "Summarize the board",
        }

        lines = ["I can help you with the following things:"]
        for s in self.registry.get_all_schemas():
            name = s.get("name")
            if name in hidden_tools:
                continue
            
            display_text = friendly_names.get(name, f"Execute operations for {name}")
            lines.append(f"• {display_text}")
            
        return "\n".join(lines)

    def execute_ask_for_clarification(self, reason_for_confusion: str):
        return f"Grug confused! {reason_for_confusion}"

    def execute_frontier_escalation(self, reason_for_escalation: str):
        if not self.frontier_available:
            return "ERROR_OFFLINE: Frontier model is down or missing API key."

        try:
            client = anthropic.Anthropic(api_key=os.getenv("CLAUDE_API_KEY"))
            model = os.getenv("CLAUDE_MODEL", "claude-opus-4-6")

            user_message = getattr(self._request_state, "user_message", "")
            context = getattr(self._request_state, "context", "")

            response = client.messages.create(
                model=model,
                max_tokens=1024,
                system=[{
                    "type": "text",
                    "text": self._base_system_prompt or "You are a helpful assistant.",
                    "cache_control": {"type": "ephemeral"}
                }],
                messages=[{
                    "role": "user",
                    "content": f"{user_message}\n\nContext: {context}\n\nEscalation reason: {reason_for_escalation}"
                }]
            )
            return response.content[0].text
        except (anthropic.APIError, anthropic.APIConnectionError) as e:
            return f"ERROR_OFFLINE: {e}"
        except Exception as e:
            return f"ERROR_OFFLINE: {e}"

    @staticmethod
    def build_system_prompt(base_system_prompt: str, compression_mode: str = "ULTRA") -> str:
        """Interpolate placeholders in the base system prompt.

        Kept as a static helper so callers (app.py, tests) can continue using it
        to build the system prompt before passing it to ``route_message``.
        """
        today = datetime.now().strftime("%Y-%m-%d")
        prompt = base_system_prompt.replace("{{COMPRESSION_MODE}}", compression_mode)
        prompt = prompt.replace("{{CURRENT_DATE}}", today)
        return prompt

    def invoke_chat(self, system_prompt: str, messages: list) -> str:
        """POST to Ollama ``/api/chat`` with multi-turn message history.

        The messages list should contain ``{"role": "user"|"assistant", "content": ...}``
        dicts. A system-role message is prepended automatically.

        Returns the assistant's response content string.
        Falls back to an escalation JSON string on error.
        """
        ollama_host = os.environ.get("OLLAMA_HOST", "http://localhost:11434")
        url = f"{ollama_host.rstrip('/')}/api/chat"
        model = os.environ.get("OLLAMA_MODEL", "gemma")

        chat_messages = [{"role": "system", "content": system_prompt}] + messages
        payload = {
            "model": model,
            "messages": chat_messages,
            "format": "json",
            "stream": False,
        }
        try:
            response = requests.post(url, json=payload, timeout=30)
            response.raise_for_status()
            return response.json().get("message", {}).get("content", "")
        except Exception as e:
            # Return a graceful fallback if the LLM is unreachable
            return f'{{"tool": "escalate_to_frontier", "arguments": {{"reason_for_escalation": "Ollama error: {str(e)}"}}}}'

    def invoke_gemma_text(self, prompt: str) -> str:
        """Plain-text (non-JSON) LLM call via ``/api/generate``.

        Used by ``execute_summarize_board`` and other non-routing calls.
        """
        ollama_host = os.environ.get("OLLAMA_HOST", "http://localhost:11434")
        url = f"{ollama_host.rstrip('/')}/api/generate"
        model = os.environ.get("OLLAMA_MODEL", "gemma")
        payload = {
            "model": model,
            "prompt": prompt,
            "stream": False
        }
        try:
            response = requests.post(url, json=payload, timeout=30)
            response.raise_for_status()
            return response.json().get("response", "").strip()
        except Exception:
            return ""

    def route_message(self, user_message: str, system_prompt: str = "",
                      message_history: list = None,
                      # Legacy parameters for backward compatibility with existing tests
                      context: str = None, compression_mode: str = "ULTRA",
                      base_system_prompt: str = None):
        """Route a user message through the LLM and execute the resulting tool call.

        New API (preferred):
            ``route_message(user_message, system_prompt, message_history)``

        Legacy API (backward-compatible, used by existing tests):
            ``route_message(user_message, context=..., base_system_prompt=...)``
            Internally converts to the new API by building the system prompt and
            a single-message history.
        """
        # Handle legacy callers that pass context/base_system_prompt
        if base_system_prompt is not None or context is not None:
            if base_system_prompt is not None:
                system_prompt = self.build_system_prompt(base_system_prompt, compression_mode)
            if message_history is None:
                message_history = [{"role": "user", "content": user_message}]
            # Store context for frontier escalation
            self._request_state.context = context or ""
        elif message_history is None:
            message_history = [{"role": "user", "content": user_message}]

        self._base_system_prompt = system_prompt
        self._request_state.user_message = user_message

        # Build the tools block and inject it into the system prompt for JSON-mode routing
        tools_str = json.dumps(self.registry.get_all_schemas(), indent=2)
        augmented_system = (
            f"{system_prompt}\n\n"
            f"TOOLS:\n{tools_str}\n\n"
            f"OUTPUT VALID JSON ONLY."
        )

        try:
            response_text = self.invoke_chat(augmented_system, message_history)

            try:
                call_data = json.loads(response_text)
                tool_name = call_data.get("tool")
                args = call_data.get("arguments", {})
                confidence_score = call_data.get("confidence_score", 10)

                # Phase 5: honor confidence score — force escalation if Gemma is uncertain
                if confidence_score < 8 and tool_name not in ("escalate_to_frontier", "ask_for_clarification"):
                    escalation_output = self.execute_frontier_escalation(
                        f"low confidence ({confidence_score}) on tool '{tool_name}'"
                    )
                    if "ERROR_OFFLINE" in escalation_output:
                        fallback_messages = message_history + [{
                            "role": "user",
                            "content": "SYSTEM WARNING: The frontier model is OFFLINE. Cannot escalate. Provide your best-effort local response."
                        }]
                        fallback_response_text = self.invoke_chat(augmented_system, fallback_messages)
                        return ToolExecutionResult(success=True, output=f"Degraded Response: {fallback_response_text}")
                    return ToolExecutionResult(success=True, output=escalation_output)

                result = self.registry.execute(tool_name, args)

                # Phase 4: Graceful Degradation Trap
                if tool_name == "escalate_to_frontier" and "ERROR_OFFLINE" in result.output:
                    fallback_messages = message_history + [{
                        "role": "user",
                        "content": "SYSTEM WARNING: The frontier model is OFFLINE. Cannot escalate. Provide your best-effort local response."
                    }]
                    fallback_response_text = self.invoke_chat(augmented_system, fallback_messages)
                    return ToolExecutionResult(success=True, output=f"Degraded Response: {fallback_response_text}")

                return result
            except json.JSONDecodeError:
                return ToolExecutionResult(success=False, output="Edge model failed to emit valid JSON.")
        finally:
            self._request_state.user_message = None
            if hasattr(self._request_state, 'context'):
                self._request_state.context = None
