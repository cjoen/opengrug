import os
import json
import subprocess
import requests
from typing import Dict, Callable, Optional
from pydantic import BaseModel
from datetime import datetime
import threading
import jsonschema
from core.config import config

def load_prompt_files(prompts_dir: str) -> str:
    """Concatenate system.md, rules.md, schema_examples.md with headers."""
    filenames = ["system.md", "rules.md", "schema_examples.md"]
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

    def register_python_tool(self, name: str, schema: dict, func: Callable,
                              destructive: bool = False, friendly_name: str = None):
        self._python_tools[name] = (schema, func, destructive, friendly_name or name)

    def register_cli_tool(self, name: str, schema: dict, base_command: list,
                           destructive: bool = True, friendly_name: str = None):
        self._cli_tools[name] = (schema, base_command, destructive, friendly_name or name)

    def get_all_schemas(self):
        schemas = []
        for name, data in self._python_tools.items():
            schemas.append({"name": name, "schema": data[0]})
        for name, data in self._cli_tools.items():
            schemas.append({"name": name, "schema": data[0]})
        return schemas

    def execute(self, tool_name: str, arguments: dict, skip_hitl=False) -> ToolExecutionResult:
        if tool_name in self._python_tools:
            schema, func, is_destructive, _friendly = self._python_tools[tool_name]

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
            except subprocess.CalledProcessError as e:
                stderr_output = e.output or ""
                return ToolExecutionResult(
                    success=False,
                    output=f"Command failed (exit {e.returncode}): {e}\n---stderr---\n{stderr_output}"
                )
            except Exception as e:
                return ToolExecutionResult(success=False, output=str(e))

        elif tool_name in self._cli_tools:
            schema, base_command, is_destructive, _friendly = self._cli_tools[tool_name]

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
                    str_val = str(val)
                    if str_val.startswith("--"):
                        return ToolExecutionResult(
                            success=False,
                            output=f"Invalid arg value for '{key}': values must not start with '--'"
                        )
                    cmd.append(f"--{key}")
                    cmd.append(str_val)
            # Separator to prevent flag injection from positional values
            cmd.append("--")

            _timeout = int(os.environ.get("GRUG_SUBPROCESS_TIMEOUT", "30"))
            try:
                output = subprocess.check_output(cmd, stderr=subprocess.STDOUT, text=True, timeout=_timeout)
                return ToolExecutionResult(success=True, output=output)
            except subprocess.CalledProcessError as e:
                return ToolExecutionResult(success=False, output=e.output)
            except subprocess.TimeoutExpired:
                return ToolExecutionResult(
                    success=False,
                    output=f"Command timed out after {_timeout}s"
                )
            except Exception as e:
                return ToolExecutionResult(success=False, output=str(e))
        else:
            return ToolExecutionResult(success=False, output=f"Tool {tool_name} not found in registry.")

class GrugRouter:
    def __init__(self, registry: ToolRegistry):
        self.registry = registry
        self._request_state = threading.local()
        # M6: Hot-reload prompts on mtime change
        self._prompt_dir = "prompts"
        self._prompt_mtimes: Dict[str, float] = {}
        self._cached_base_prompt = ""
        self._reload_prompts()
        self.register_core_tools()

    def _reload_prompts(self):
        """Reload prompt files and update mtime cache."""
        try:
            self._cached_base_prompt = load_prompt_files(self._prompt_dir)
        except FileNotFoundError:
            pass  # Prompts not available (e.g. in tests)
        for name in ["system.md", "rules.md", "schema_examples.md"]:
            path = os.path.join(self._prompt_dir, name)
            try:
                self._prompt_mtimes[name] = os.stat(path).st_mtime
            except OSError:
                self._prompt_mtimes[name] = 0

    def _check_prompt_reload(self):
        """Stat prompt files; reload if any changed."""
        for name, old_mtime in self._prompt_mtimes.items():
            path = os.path.join(self._prompt_dir, name)
            try:
                if os.stat(path).st_mtime > old_mtime:
                    self._reload_prompts()
                    return
            except OSError:
                continue

    def register_core_tools(self):
        # Clarification tool
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
            func=self.execute_ask_for_clarification,
            destructive=False,
            friendly_name="Ask for clarification"
        )

        # Help / CLI Capabilities tool
        self.registry.register_python_tool(
            name="list_capabilities",
            schema={
                "description": "[META] Output ONLY when the user explicitly asks what tools/commands are available or what Grug can do (e.g. 'what can you do?', 'list your commands', 'help'). Do NOT use for greetings like 'hi' or 'hey grug' — those go to reply_to_user.",
                "type": "object",
                "properties": {},
                "required": []
            },
            func=self.execute_list_capabilities,
            destructive=False,
            friendly_name="List capabilities"
        )

        # Conversational tool
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
            func=self.execute_reply_to_user,
            destructive=False,
            friendly_name="Chat with Grug"
        )

        # Board summary tool — fetches tasks and asks Gemma to summarize them
        self.registry.register_python_tool(
            name="summarize_board",
            schema={
                "description": "[BOARD] Give a short natural-language summary of tasks on the project board. Use when the user asks for an overview, summary, or 'state of the board'. Optionally filter by status.",
                "type": "object",
                "properties": {
                    "status": {"type": "string", "description": "Filter by task status (e.g. 'Todo', 'In Progress', 'Done')"}
                }
            },
            func=self.execute_summarize_board,
            destructive=False,
            friendly_name="Summarize the board"
        )

    def execute_reply_to_user(self, message: str):
        return message

    def execute_summarize_board(self, status=None):
        args = {}
        if status:
            args["status"] = status

        result = self.registry.execute("list_tasks", args)
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
        hidden_tools = {"ask_for_clarification", "list_capabilities", "reply_to_user"}
        lines = ["I can help you with the following things:"]
        for name, data in self.registry._python_tools.items():
            if name in hidden_tools:
                continue
            friendly = data[3]  # friendly_name
            lines.append(f"• {friendly}")
        for name, data in self.registry._cli_tools.items():
            if name in hidden_tools:
                continue
            friendly = data[3]
            lines.append(f"• {friendly}")
        return "\n".join(lines)

    def execute_ask_for_clarification(self, reason_for_confusion: str):
        return f"Grug confused! {reason_for_confusion}"

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
            response = requests.post(url, json=payload, timeout=config.llm.ollama_timeout)
            response.raise_for_status()
            return response.json().get("message", {}).get("content", "")
        except Exception as e:
            # Return a graceful fallback if the LLM is unreachable
            return json.dumps({
                "tool": "ask_for_clarification",
                "arguments": {"reason_for_confusion": f"Grug brain foggy. Ollama not responding: {e}"},
                "confidence_score": 0
            })

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
            response = requests.post(url, json=payload, timeout=config.llm.ollama_timeout)
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
        # M6: Hot-reload prompts if any file changed
        self._check_prompt_reload()

        # Handle legacy callers that pass context/base_system_prompt
        if base_system_prompt is not None or context is not None:
            if base_system_prompt is not None:
                system_prompt = self.build_system_prompt(base_system_prompt, compression_mode)
            if message_history is None:
                message_history = [{"role": "user", "content": user_message}]
        elif message_history is None:
            message_history = [{"role": "user", "content": user_message}]

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
                confidence_score = call_data.get("confidence_score", 0)

                # M5: Append routing trace
                try:
                    trace_entry = json.dumps({
                        "ts": datetime.now().isoformat(),
                        "user_msg": user_message[:200],
                        "tool": tool_name,
                        "args": args,
                        "confidence": confidence_score,
                    })
                    trace_path = os.path.join("brain", "routing_trace.jsonl")
                    os.makedirs(os.path.dirname(trace_path), exist_ok=True)
                    with open(trace_path, "a", encoding="utf-8") as tf:
                        tf.write(trace_entry + "\n")
                except Exception:
                    pass  # tracing must never break routing

                # Low confidence: ask the user for clarification instead of guessing
                if confidence_score < 8 and tool_name not in ("ask_for_clarification", "reply_to_user"):
                    return ToolExecutionResult(
                        success=True,
                        output=f"Grug not very sure (confidence {confidence_score}/10). Grug need more detail to pick right tool. What you want Grug do?"
                    )

                result = self.registry.execute(tool_name, args)

                return result
            except json.JSONDecodeError:
                return ToolExecutionResult(success=False, output="Edge model failed to emit valid JSON.")
        finally:
            self._request_state.user_message = None
