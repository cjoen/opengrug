"""Tool registry for Grug.

Manages registered tools (Python callables and CLI subprocesses),
validates arguments with JSON Schema, and enforces HITL gating.
"""

import os
import subprocess
from dataclasses import dataclass, field
from typing import Dict, Callable, Optional
import jsonschema

# Re-export from core.utils for backward compatibility
from core.utils import _sanitize_untrusted


@dataclass
class ToolExecutionResult:
    success: bool
    output: str
    requires_approval: bool = False
    tool_name: Optional[str] = None
    arguments: Optional[dict] = None
    tool_output: Optional[str] = None


class ToolRegistry:

    def __init__(self):
        self._python_tools: Dict[str, tuple] = {}
        self._cli_tools: Dict[str, tuple] = {}
        self._category_descriptions: Dict[str, str] = {}

    def register_python_tool(self, name: str, schema: dict, func: Callable,
                              destructive: bool = False, friendly_name: str = None,
                              category: str = "SYSTEM"):
        self._python_tools[name] = (schema, func, destructive, friendly_name or name, category)

    def register_cli_tool(self, name: str, schema: dict, base_command: list,
                           destructive: bool = True, friendly_name: str = None,
                           category: str = "SYSTEM"):
        self._cli_tools[name] = (schema, base_command, destructive, friendly_name or name, category)

    def register_category_description(self, category: str, description: str):
        self._category_descriptions[category] = description

    def get_category(self, tool_name: str) -> str:
        if tool_name in self._python_tools:
            return self._python_tools[tool_name][4]
        if tool_name in self._cli_tools:
            return self._cli_tools[tool_name][4]
        return "SYSTEM"

    def get_category_description(self, category: str) -> str:
        return self._category_descriptions.get(category, "help Grug figure out what you need")

    def create_scoped(self, tool_names) -> "ToolRegistry":
        """Return a new ToolRegistry exposing only the named tools.

        Special value "all" returns a full shallow copy of the registry.
        Tool data tuples are shared by reference — handlers, schemas, and
        HITL flags remain consistent with the global registry.
        """
        scoped = ToolRegistry()
        if tool_names == "all":
            scoped._python_tools = dict(self._python_tools)
            scoped._cli_tools = dict(self._cli_tools)
            scoped._category_descriptions = dict(self._category_descriptions)
            return scoped

        keep = set(tool_names)
        scoped._python_tools = {n: d for n, d in self._python_tools.items() if n in keep}
        scoped._cli_tools = {n: d for n, d in self._cli_tools.items() if n in keep}
        used_cats = (
            {d[4] for d in scoped._python_tools.values()}
            | {d[4] for d in scoped._cli_tools.values()}
        )
        scoped._category_descriptions = {
            c: v for c, v in self._category_descriptions.items() if c in used_cats
        }
        missing = keep - set(self._python_tools) - set(self._cli_tools)
        if missing:
            print(f"[registry] scoped registry missing tools: {sorted(missing)}")
        return scoped

    def get_all_schemas(self):
        schemas = []
        
        def _to_openai_schema(name, data):
            schema = data[0]
            # Construct standard OpenAI function schema
            func_def = {
                "name": name,
                "description": schema.get("description", ""),
                "parameters": {
                    "type": "object",
                    "properties": schema.get("properties", {}),
                }
            }
            if "required" in schema:
                func_def["parameters"]["required"] = schema["required"]
                
            return {
                "type": "function",
                "function": func_def
            }

        for name, data in self._python_tools.items():
            schemas.append(_to_openai_schema(name, data))
        for name, data in self._cli_tools.items():
            schemas.append(_to_openai_schema(name, data))
            
        return schemas

    def execute(self, tool_name: str, arguments: dict, skip_hitl=False) -> ToolExecutionResult:
        # Lookup
        if tool_name in self._python_tools:
            schema, handler, is_destructive, _, _ = self._python_tools[tool_name]
            is_cli = False
        elif tool_name in self._cli_tools:
            schema, handler, is_destructive, _, _ = self._cli_tools[tool_name]
            is_cli = True
        else:
            return ToolExecutionResult(success=False, output=f"Tool {tool_name} not found in registry.")

        # Validate
        try:
            jsonschema.Draft7Validator(schema).validate(arguments)
        except jsonschema.ValidationError as e:
            return ToolExecutionResult(success=False, output=f"Invalid args for {tool_name}: {e.message}")

        # HITL gate
        if is_destructive and not skip_hitl:
            return ToolExecutionResult(
                success=True,
                output=f"Waiting for human approval to run {tool_name}.",
                requires_approval=True,
                tool_name=tool_name,
                arguments=arguments,
            )

        # Execute
        if is_cli:
            return self._execute_cli(tool_name, handler, arguments)
        return self._execute_python(tool_name, handler, arguments)

    def _execute_python(self, tool_name: str, func: Callable, arguments: dict) -> ToolExecutionResult:
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

    def _execute_cli(self, tool_name: str, base_command: list, arguments: dict) -> ToolExecutionResult:
        cmd = base_command.copy()
        for key, val in arguments.items():
            if isinstance(val, bool):
                if val:
                    cmd.append(f"--{key}")
            else:
                str_val = str(val)
                if str_val.startswith("--"):
                    return ToolExecutionResult(
                        success=False,
                        output=f"Invalid arg value for '{key}': values must not start with '--'"
                    )
                cmd.append(f"--{key}")
                cmd.append(str_val)
        cmd.append("--")

        from core.config import config as _cfg
        _timeout = int(os.environ.get("GRUG_SUBPROCESS_TIMEOUT", _cfg.storage.subprocess_timeout))
        try:
            output = subprocess.check_output(cmd, stderr=subprocess.STDOUT, text=True, timeout=_timeout)
            return ToolExecutionResult(success=True, output=output)
        except subprocess.CalledProcessError as e:
            return ToolExecutionResult(success=False, output=e.output)
        except subprocess.TimeoutExpired:
            return ToolExecutionResult(success=False, output=f"Command timed out after {_timeout}s")
        except Exception as e:
            return ToolExecutionResult(success=False, output=str(e))
