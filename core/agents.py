"""Agent containers — isolated execution environments per persona.

An AgentContainer bundles everything an agent needs at runtime:
its ChatWorker reference, its rendered system prompt (base.md +
agent-specific prompt), its scoped ToolRegistry, and its scoped
RAG sources. The Orchestrator picks an AgentContainer by name and
runs its StepLoop without ever touching global tool state.
"""

from dataclasses import dataclass, field
from typing import Dict

from core.interfaces import ChatWorker
from core.registry import ToolRegistry
from core.utils import load_agent_prompt
from core.vectors import VectorMemory


@dataclass
class AgentContainer:
    name: str
    worker: ChatWorker
    base_prompt: str
    registry: ToolRegistry
    rag_sources: Dict[str, VectorMemory] = field(default_factory=dict)
    max_run_time: float = 300.0  # seconds; consumed by Phase 5.3 watchdog


class AgentFactory:
    """Builds AgentContainers from config + worker pool + global registry + RAG pool."""

    @staticmethod
    def create_all(config, worker_pool, global_registry, rag_pool) -> Dict[str, AgentContainer]:
        agents: Dict[str, AgentContainer] = {}
        agents_ns = getattr(config, "agents", None)
        if agents_ns is None:
            return agents

        base_path = "prompts/base.md"

        for name in vars(agents_ns):
            cfg = getattr(agents_ns, name)

            worker_tier = cfg.required_worker_tier
            if worker_tier not in worker_pool:
                raise ValueError(
                    f"agent '{name}': required_worker_tier '{worker_tier}' not in worker pool"
                )
            worker = worker_pool[worker_tier]

            tools_spec = getattr(cfg, "tools", "all")
            scoped = global_registry.create_scoped(tools_spec)

            rag_for_agent: Dict[str, VectorMemory] = {}
            for rs_name in getattr(cfg, "rag_sources", []):
                if rs_name not in rag_pool:
                    raise ValueError(
                        f"agent '{name}': unknown rag_source '{rs_name}'"
                    )
                rag_for_agent[rs_name] = rag_pool[rs_name]

            agent_prompt_path = getattr(cfg, "prompt", None)
            base_prompt = load_agent_prompt(base_path, agent_prompt_path)

            agents[name] = AgentContainer(
                name=name,
                worker=worker,
                base_prompt=base_prompt,
                registry=scoped,
                rag_sources=rag_for_agent,
            )

        return agents
