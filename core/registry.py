"""Tool registry for Grug.

Manages registered tools (Python callables and CLI subprocesses),
validates arguments with JSON Schema, and enforces HITL gating.
"""

import os
import subprocess
from typing import Dict, Callable, Optional
from pydantic import BaseModel
import jsonschema


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

    def get_all_schemas(self):
        schemas = []
        for name, data in self._python_tools.items():
            schemas.append({"name": name, "schema": data[0]})
        for name, data in self._cli_tools.items():
            schemas.append({"name": name, "schema": data[0]})
        return schemas

    def execute(self, tool_name: str, arguments: dict, skip_hitl=False) -> ToolExecutionResult:
        if tool_name in self._python_tools:
            schema, func, is_destructive, _friendly, _cat = self._python_tools[tool_name]

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
            schema, base_command, is_destructive, _friendly, _cat = self._cli_tools[tool_name]

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
            cmd.append("--")

            from core.config import config as _cfg
            _timeout = int(os.environ.get("GRUG_SUBPROCESS_TIMEOUT", _cfg.storage.subprocess_timeout))
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
