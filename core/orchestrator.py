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
                "description": "Output when you absolutely do not understand the user's intent or need more information.",
                "type": "object",
                "properties": {
                    "reason_for_confusion": {"type": "string"}
                },
                "required": ["reason_for_confusion"]
            },
            func=self.execute_ask_for_clarification,
            destructive=False
        )

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

    def build_system_prompt(self, base_system_prompt: str, compression_mode: str = "ULTRA") -> str:
        today = datetime.now().strftime("%Y-%m-%d")
        prompt = base_system_prompt.replace("{{COMPRESSION_MODE}}", compression_mode)
        prompt = prompt.replace("{{CURRENT_DATE}}", today)
        return prompt

    def invoke_gemma(self, prompt: str) -> str:
        ollama_host = os.environ.get("OLLAMA_HOST", "http://localhost:11434")
        url = f"{ollama_host.rstrip('/')}/api/generate"
        model = os.environ.get("OLLAMA_MODEL", "gemma")
        payload = {
            "model": model,
            "prompt": prompt,
            "format": "json",
            "stream": False
        }
        try:
            response = requests.post(url, json=payload, timeout=30)
            response.raise_for_status()
            return response.json().get("response", "")
        except Exception as e:
            # Return a graceful fallback if the LLM is unreachable
            return f'{{"tool": "escalate_to_frontier", "arguments": {{"reason_for_escalation": "Ollama error: {str(e)}"}}}}'

    def route_message(self, user_message: str, context: str, compression_mode="ULTRA", base_system_prompt=""):
        system_prompt = self.build_system_prompt(base_system_prompt, compression_mode)
        self._base_system_prompt = system_prompt
        self._request_state.user_message = user_message
        self._request_state.context = context

        try:
            tools_str = json.dumps(self.registry.get_all_schemas(), indent=2)
            prompt = f"SYSTEM:\n{system_prompt}\n\nCONTEXT:\n{context}\n\nTOOLS:\n{tools_str}\n\nUSER MESSAGE:\n{user_message}\n\nOUTPUT VALID JSON ONLY."

            response_text = self.invoke_gemma(prompt)

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
                        fallback_prompt = prompt + "\n\nSYSTEM WARNING: The frontier model is OFFLINE. Cannot escalate. Provide your best-effort local response."
                        fallback_response_text = self.invoke_gemma(fallback_prompt)
                        return ToolExecutionResult(success=True, output=f"Degraded Response: {fallback_response_text}")
                    return ToolExecutionResult(success=True, output=escalation_output)

                result = self.registry.execute(tool_name, args)

                # Phase 4: Graceful Degradation Trap
                if tool_name == "escalate_to_frontier" and "ERROR_OFFLINE" in result.output:
                    fallback_prompt = prompt + "\n\nSYSTEM WARNING: The frontier model is OFFLINE. Cannot escalate. Provide your best-effort local response."
                    fallback_response_text = self.invoke_gemma(fallback_prompt)
                    return ToolExecutionResult(success=True, output=f"Degraded Response: {fallback_response_text}")

                return result
            except json.JSONDecodeError:
                return ToolExecutionResult(success=False, output="Edge model failed to emit valid JSON.")
        finally:
            self._request_state.user_message = None
            self._request_state.context = None
