import json
import subprocess
from typing import Dict, Callable
from pydantic import BaseModel

class ToolExecutionResult(BaseModel):
    success: bool
    output: str
    requires_approval: bool = False

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
            _, func, is_destructive = self._python_tools[tool_name]
            
            if is_destructive and not skip_hitl:
                return ToolExecutionResult(
                    success=True, 
                    output=f"Waiting for human approval to mutate state with {tool_name}.", 
                    requires_approval=True
                )
            
            try:
                res = func(**arguments)
                return ToolExecutionResult(success=True, output=str(res))
            except Exception as e:
                return ToolExecutionResult(success=False, output=str(e))
                
        elif tool_name in self._cli_tools:
            _, base_command, is_destructive = self._cli_tools[tool_name]
            
            if is_destructive and not skip_hitl:
                 return ToolExecutionResult(
                     success=True, 
                     output=f"Waiting for human approval to run CLI tool {tool_name}.", 
                     requires_approval=True
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
                # shell=False guarantees no pipe/bash injection attacks
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
        # Phase 4: Configure Frontier offline safety check
        self.frontier_available = bool(os.getenv("CLAUDE_API_KEY", ""))
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
            destructive=False
        )

    def execute_frontier_escalation(self, reason_for_escalation: str):
        if not self.frontier_available:
            return "ERROR_OFFLINE: Frontier model is down or missing API key."
        return "Escalation to Claude successful. Handing over processing..."

    def build_system_prompt(self, base_system_prompt: str, compression_mode: str = "ULTRA") -> str:
        # Phase 5: Dynamic Caveman injection
        return base_system_prompt.replace("{{COMPRESSION_MODE}}", compression_mode)

    def invoke_gemma(self, prompt: str) -> str:
        # Placeholder for local Ollama/LMStudio call
        return '{"tool": "save_insight", "arguments": {"insight": "Example Local LLM Response"}}'

    def route_message(self, user_message: str, context: str, compression_mode="ULTRA", base_system_prompt=""):
        system_prompt = self.build_system_prompt(base_system_prompt, compression_mode)
        tools_str = json.dumps(self.registry.get_all_schemas(), indent=2)
        prompt = f"SYSTEM:\n{system_prompt}\n\nCONTEXT:\n{context}\n\nTOOLS:\n{tools_str}\n\nUSER MESSAGE:\n{user_message}\n\nOUTPUT VALID JSON ONLY."
        
        # Invoke local Edge router (Gemma e4b)
        response_text = self.invoke_gemma(prompt)
        
        # Parse & Sandbox
        try:
            call_data = json.loads(response_text)
            tool_name = call_data.get("tool")
            args = call_data.get("arguments", {})
            
            result = self.registry.execute(tool_name, args)
            
            # Phase 4: Graceful Degradation Trap
            if tool_name == "escalate_to_frontier" and "ERROR_OFFLINE" in result.output:
                # Re-run Gemma with offline warning
                fallback_prompt = prompt + "\n\nSYSTEM WARNING: The frontier model is OFFLINE. Cannot escalate. Provide your best-effort local response."
                fallback_response_text = self.invoke_gemma(fallback_prompt)
                return ToolExecutionResult(success=True, output=f"Degraded Response: {fallback_response_text}")
                
            return result
        except json.JSONDecodeError:
            return ToolExecutionResult(success=False, output="Edge model failed to emit valid JSON.")
