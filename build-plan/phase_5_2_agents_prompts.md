# Phase 5.2: Agents, Prompts & Scoped Registries

## Objective
Build the Agent abstraction layer: isolated containers with scoped tools, scoped RAG, and per-agent prompts. After this phase, the system has all the pieces for multi-agent routing but still runs single-agent (wired in Phase 5.3).

## Design Patterns
- **Liskov Substitution (LSP)** — All `AgentContainer` instances are interchangeable to the Orchestrator. It doesn't know or care which agent it's running.
- **Dependency Injection** — Tools, VectorMemory connections, and prompts are injected at construction, never accessed globally.
- **Interface Segregation (ISP)** — Each agent only sees the tools in its scoped registry. No global tool list.
- **Factory Pattern** — `AgentFactory` builds fully-wired containers from config + worker pool.

## Changes

### Prompt Architecture

#### [MODIFY] `prompts/system.md` → [RENAME] `prompts/base.md`
Extract universal content: personality, injection resistance rules, date/time interpolation, untrusted input handling. Remove tool categories and tool usage instructions (those become agent-specific).

#### [MODIFY] `prompts/rules.md`
Merge into `prompts/base.md`. The rules are universal (date formatting, ownership assumptions, tag constraints, prompt injection resistance). Delete `rules.md` after merge.

#### [NEW] `prompts/dispatcher.md`
Dispatcher-specific prompt: intent classification instructions, when to generate a To-Do List vs. direct-route to `chat_agent`, output format for routing decisions (target agent enum + distilled context + optional plan).

#### [NEW] `prompts/agents/chat_agent.md`
Conversational agent and system generalist. Inherits **all** registered tools (notes, tasks, scheduling, instructions, health, etc.) plus `dispatch_task`. Prompt includes: tool usage instructions for its full registry, tool categories, memory context section, schema examples, and instructions for using `dispatch_task` to escalate complex tasks to Expert Agents when the user's intent is clear. Most of the current `system.md` content lands here.

#### [NEW] `prompts/agents/researcher.md`
Research agent: methodology framing, summarization style, tool usage for `search_web`, `read_url`, `fetch_rss`.

#### [MODIFY] `prompts/schema_examples.md`
Split into per-agent example files or make the context builder agent-aware (only inject examples for tools in the agent's registry). Simplest approach: move into each agent's prompt file directly.

### Agent Containers

#### [NEW] `core/agents.py`
```python
class AgentContainer:
    """Isolated execution environment for a single agent persona."""
    def __init__(self, name, worker, prompt_path, registry, rag_sources):
        self.name = name
        self.worker = worker          # ChatWorker reference
        self.prompt_path = prompt_path
        self.registry = registry      # Scoped ToolRegistry
        self.rag_sources = rag_sources # dict[str, VectorMemory]

class AgentFactory:
    """Builds AgentContainers from config + worker pool."""
    @staticmethod
    def create_all(config, worker_pool, global_registry, rag_pool) -> dict[str, AgentContainer]:
        """Returns dict keyed by agent name."""
```

### Scoped Registries

#### [MODIFY] `core/registry.py`
Add a `create_scoped(tool_names: list[str]) -> ToolRegistry` method. Returns a new `ToolRegistry` containing only the specified tools (shallow copy of schema + handler references). The global registry remains the source of truth for registration; scoped registries are read-only views. The special value `"all"` returns a full copy of the global registry.

### Dispatch Tool (Interface Only)

#### [NEW] `tools/dispatch.py`
Define the `dispatch_task` tool schema and stub handler. The actual implementation (creating and enqueuing a `Task`) is wired in Phase 5.3 when the `TaskQueue` exists.

```python
def dispatch_task(agent: str, context: str, plan: list[str] = None) -> str:
    """Route a task to an Expert Agent. Called by chat_agent when intent is clear."""
    # Stub: returns "dispatch not yet wired" until Phase 5.3
```

Registered on the global registry with `category="SYSTEM"`. Only the `chat_agent` (via `"tools": "all"`) sees it; Expert Agents' scoped registries exclude it.

### Multi-DB Vector Memory

#### [MODIFY] `core/vectors.py`
Refactor `VectorMemory` to be per-database (it already is, constructor takes `db_path`). No class-level changes needed — the multi-DB pattern is achieved by creating multiple `VectorMemory` instances, one per `rag_source` config entry. Add a factory function:
```python
def create_rag_pool(config, worker_pool) -> dict[str, VectorMemory]:
    """Create one VectorMemory per rag_source, each with its own DB and embedding worker."""
```

### Prompt Loading

#### [MODIFY] `core/utils.py`
Replace `load_prompt_files(prompts_dir)` with:
```python
def load_agent_prompt(base_path="prompts/base.md", agent_path=None) -> str:
    """Load base prompt + agent-specific prompt. Returns concatenated string."""
```

### Wiring

#### [MODIFY] `app.py`
- Create `rag_pool = create_rag_pool(config, worker_pool)`.
- Create `agents = AgentFactory.create_all(config, worker_pool, registry, rag_pool)`.
- Pass `agents` dict to `Orchestrator` (consumed in Phase 5.3).
- Background indexer runs on all RAG source databases.

## Verification
1. `pytest tests/` — existing tests pass.
2. New `tests/test_agents.py` — verify `AgentFactory` creates containers with correct tools, correct worker, correct RAG sources. Verify scoped registry only exposes declared tools.
3. Manual: boot app, confirm `chat_agent` container has the right tools and prompt loads correctly.
