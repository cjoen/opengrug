"""Dispatcher — intent classifier that routes a user message to an agent.

Runs a fast LLM call against `prompts/dispatcher.md` and parses the JSON output
into a `DispatchDecision`. On any failure (timeout, malformed JSON, unknown
agent) it falls back to `chat_agent` with the raw user message — the system
must always make forward progress.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Optional


@dataclass
class DispatchDecision:
    agent: str
    context: str
    plan: Optional[list[str]] = None
    direct_tool: Optional[str] = None
    direct_args: Optional[dict] = None


class Dispatcher:
    """Classifies user intent → (agent, context, plan)."""

    def __init__(self, chat_worker, registry=None,
                 prompt_path: str = "prompts/dispatcher.md",
                 fallback_agent: str = "chat_agent"):
        self.chat_worker = chat_worker
        self._registry = registry
        self.prompt_path = prompt_path
        self.fallback_agent = fallback_agent

    def _build_prompt(self, available_agents: list[str]) -> str:
        with open(self.prompt_path, "r", encoding="utf-8") as f:
            txt = f.read()
        txt = txt.replace("{{AVAILABLE_AGENTS}}", ", ".join(available_agents))

        if self._registry is None:
            return txt

        # Build routing hints from registry dispatch_to fields
        lines = ["", "## Dispatch Routing Hints", ""]

        agent_tools: dict[str, list[str]] = {a: [] for a in available_agents}
        for name in (
            list(self._registry._python_tools.keys())
            + list(self._registry._cli_tools.keys())
        ):
            target = self._registry.get_dispatch_to(name)
            if target and target in agent_tools:
                agent_tools[target].append(name)

        for agent, tools in agent_tools.items():
            if tools:
                lines.append(f"{agent}: {', '.join(sorted(tools))}")

        direct = self._registry.get_dispatcher_direct_tools()
        if direct:
            lines.append("")
            lines.append("Direct tools (call these yourself, no agent needed):")
            for name in sorted(direct):
                data = (
                    self._registry._python_tools.get(name)
                    or self._registry._cli_tools.get(name)
                )
                desc = data[0].get("description", "") if data else ""
                lines.append(f"- {name}() — {desc}")

        return txt + "\n".join(lines)

    # Keep _load_prompt as an alias for backward compatibility
    def _load_prompt(self, available_agents: list[str]) -> str:
        return self._build_prompt(available_agents)

    def classify(self, user_message: str, history: list[dict],
                 available_agents: list[str]) -> DispatchDecision:
        if not available_agents:
            return DispatchDecision(agent=self.fallback_agent, context=user_message)

        try:
            sys_prompt = self._build_prompt(available_agents)
            messages = list(history) + [{"role": "user", "content": user_message}]
            direct_schemas = (
                self._registry.get_direct_tool_schemas() if self._registry else []
            )
            response = self.chat_worker.chat(
                sys_prompt, messages, tools=direct_schemas or None
            )

            if response.tool_calls:
                call = response.tool_calls[0]
                return DispatchDecision(
                    agent=self.fallback_agent,
                    context=user_message,
                    direct_tool=call.get("tool"),
                    direct_args=call.get("arguments", {}),
                )

            decision = self._parse(response.content or "", available_agents)
            if decision is None:
                return DispatchDecision(agent=self.fallback_agent, context=user_message)
            return decision
        except Exception as e:
            print(f"[dispatcher] classification failed, falling back to {self.fallback_agent}: {e}")
            return DispatchDecision(agent=self.fallback_agent, context=user_message)

    def _parse(self, raw: str, available_agents: list[str]) -> Optional[DispatchDecision]:
        # Strip code fences if present
        raw = raw.strip()
        m = re.search(r"\{.*\}", raw, re.DOTALL)
        if not m:
            return None
        try:
            data = json.loads(m.group(0))
        except json.JSONDecodeError:
            return None
        agent = data.get("agent")
        context = data.get("context") or ""
        plan = data.get("plan")
        if not isinstance(agent, str) or agent not in available_agents:
            return None
        if plan is not None and not (isinstance(plan, list) and all(isinstance(s, str) for s in plan)):
            plan = None
        return DispatchDecision(agent=agent, context=context, plan=plan or None)
