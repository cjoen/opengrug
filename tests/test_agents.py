"""Tests for AgentFactory, AgentContainer, and scoped registries."""

import io
import pytest
from contextlib import redirect_stdout
from types import SimpleNamespace
from unittest.mock import MagicMock

from core.agents import AgentFactory, AgentContainer
from core.registry import ToolRegistry
from core.interfaces import ChatWorker, EmbeddingWorker, LLMResponse
from core.utils import load_agent_prompt


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------

class _FakeChatWorker(ChatWorker):
    def __init__(self, name="fake"):
        super().__init__(concurrency=1)
        self._name = name

    @property
    def model_name(self):
        return self._name

    @property
    def backend_name(self):
        return "fake"

    def chat(self, system_prompt, messages, tools=None):
        return LLMResponse(content="", tool_calls=[])

    def generate(self, prompt):
        return ""


class _FakeEmbeddingWorker(EmbeddingWorker):
    def __init__(self, name="fake-emb"):
        super().__init__(concurrency=1)
        self._name = name

    @property
    def model_name(self):
        return self._name

    @property
    def backend_name(self):
        return "fake"

    def embed(self, text):
        return [0.0]


def _make_config(agents_dict, rag_sources_dict=None):
    """Build a minimal config namespace with agents + rag_sources."""
    agents_ns = SimpleNamespace()
    for name, cfg in agents_dict.items():
        agents_ns_entry = SimpleNamespace(**cfg)
        setattr(agents_ns, name, agents_ns_entry)

    rag_ns = SimpleNamespace()
    for name, cfg in (rag_sources_dict or {}).items():
        setattr(rag_ns, name, SimpleNamespace(**cfg))

    return SimpleNamespace(agents=agents_ns, rag_sources=rag_ns)


def _populate_global_registry():
    """Build a registry with a few representative tools."""
    reg = ToolRegistry()
    reg.register_python_tool(
        name="add_note",
        schema={"type": "object", "properties": {"content": {"type": "string"}}, "required": ["content"]},
        func=lambda content: "saved",
        category="NOTES",
    )
    reg.register_python_tool(
        name="search_web",
        schema={"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]},
        func=lambda query: "results",
        category="RESEARCH",
    )
    reg.register_python_tool(
        name="reply_to_user",
        schema={"type": "object", "properties": {"message": {"type": "string"}}, "required": ["message"]},
        func=lambda message: message,
        category="SYSTEM",
    )
    reg.register_category_description("NOTES", "save a note")
    reg.register_category_description("SYSTEM", "chat tools")
    reg.register_category_description("RESEARCH", "external lookups")
    return reg


# ---------------------------------------------------------------------------
# Scoped registry
# ---------------------------------------------------------------------------

def test_scoped_registry_all_returns_full_copy():
    reg = _populate_global_registry()
    scoped = reg.create_scoped("all")
    names = [s["function"]["name"] for s in scoped.get_all_schemas()]
    assert set(names) == {"add_note", "search_web", "reply_to_user"}
    assert scoped is not reg
    assert "NOTES" in scoped._category_descriptions


def test_scoped_registry_with_tool_list():
    reg = _populate_global_registry()
    scoped = reg.create_scoped(["add_note", "reply_to_user"])
    names = [s["function"]["name"] for s in scoped.get_all_schemas()]
    assert set(names) == {"add_note", "reply_to_user"}
    # Category descriptions only kept for surviving tool categories
    assert "NOTES" in scoped._category_descriptions
    assert "SYSTEM" in scoped._category_descriptions
    assert "RESEARCH" not in scoped._category_descriptions


def test_scoped_registry_warns_on_missing_tool():
    reg = _populate_global_registry()
    buf = io.StringIO()
    with redirect_stdout(buf):
        scoped = reg.create_scoped(["add_note", "nonexistent_tool"])
    out = buf.getvalue()
    assert "missing tools" in out
    assert "nonexistent_tool" in out
    names = [s["function"]["name"] for s in scoped.get_all_schemas()]
    assert names == ["add_note"]


def test_scoped_registry_handlers_share_with_global():
    """Handlers in the scoped registry are the same callables as in global."""
    reg = _populate_global_registry()
    scoped = reg.create_scoped(["add_note"])
    res_global = reg.execute("add_note", {"content": "hi"})
    res_scoped = scoped.execute("add_note", {"content": "hi"})
    assert res_global.output == res_scoped.output == "saved"


# ---------------------------------------------------------------------------
# Prompt loader
# ---------------------------------------------------------------------------

def test_load_agent_prompt_concatenates(tmp_path):
    base = tmp_path / "base.md"
    base.write_text("BASE CONTENT")
    agent = tmp_path / "agent.md"
    agent.write_text("AGENT CONTENT")
    out = load_agent_prompt(str(base), str(agent))
    assert "BASE CONTENT" in out
    assert "AGENT CONTENT" in out
    # Base must appear before agent prompt
    assert out.index("BASE CONTENT") < out.index("AGENT CONTENT")


def test_load_agent_prompt_base_only(tmp_path):
    base = tmp_path / "base.md"
    base.write_text("BASE ONLY")
    out = load_agent_prompt(str(base), None)
    assert out == "BASE ONLY"


# ---------------------------------------------------------------------------
# AgentFactory
# ---------------------------------------------------------------------------

def test_factory_creates_chat_agent(tmp_path):
    base = tmp_path / "base.md"
    base.write_text("BASE")
    chat_md = tmp_path / "chat_agent.md"
    chat_md.write_text("CHAT")

    cfg = _make_config(
        agents_dict={
            "chat_agent": {
                "required_worker_tier": "local-fast",
                "prompt": str(chat_md),
                "tools": "all",
                "rag_sources": ["core_memory"],
            }
        },
        rag_sources_dict={
            "core_memory": {"db_path": "memory.db", "embedding_worker": "embedder"}
        },
    )
    worker_pool = {
        "local-fast": _FakeChatWorker("local-fast"),
        "embedder": _FakeEmbeddingWorker(),
    }
    rag_pool = {"core_memory": MagicMock(name="VectorMemory")}
    registry = _populate_global_registry()

    # Patch the base prompt path used inside AgentFactory
    import core.agents as agents_module
    real_loader = agents_module.load_agent_prompt
    agents_module.load_agent_prompt = lambda b, a: real_loader(str(base), a)
    try:
        agents = AgentFactory.create_all(cfg, worker_pool, registry, rag_pool)
    finally:
        agents_module.load_agent_prompt = real_loader

    assert "chat_agent" in agents
    container = agents["chat_agent"]
    assert isinstance(container, AgentContainer)
    assert container.worker is worker_pool["local-fast"]
    assert "BASE" in container.base_prompt
    assert "CHAT" in container.base_prompt
    assert container.rag_sources == {"core_memory": rag_pool["core_memory"]}
    # "tools": "all" → scoped registry has every tool
    names = [s["function"]["name"] for s in container.registry.get_all_schemas()]
    assert set(names) == {"add_note", "search_web", "reply_to_user"}


def test_factory_scopes_tools_when_list_provided(tmp_path):
    base = tmp_path / "base.md"
    base.write_text("BASE")
    researcher_md = tmp_path / "researcher.md"
    researcher_md.write_text("RESEARCHER")

    cfg = _make_config(
        agents_dict={
            "researcher": {
                "required_worker_tier": "cloud-smart",
                "prompt": str(researcher_md),
                "tools": ["search_web"],
                "rag_sources": [],
            }
        }
    )
    worker_pool = {"cloud-smart": _FakeChatWorker("cloud-smart")}
    registry = _populate_global_registry()

    import core.agents as agents_module
    real_loader = agents_module.load_agent_prompt
    agents_module.load_agent_prompt = lambda b, a: real_loader(str(base), a)
    try:
        agents = AgentFactory.create_all(cfg, worker_pool, registry, {})
    finally:
        agents_module.load_agent_prompt = real_loader

    container = agents["researcher"]
    names = [s["function"]["name"] for s in container.registry.get_all_schemas()]
    assert names == ["search_web"]


def test_factory_rejects_unknown_worker_tier(tmp_path):
    base = tmp_path / "base.md"
    base.write_text("BASE")
    cfg = _make_config(
        agents_dict={
            "chat_agent": {
                "required_worker_tier": "missing-tier",
                "prompt": str(base),
                "tools": "all",
                "rag_sources": [],
            }
        }
    )
    with pytest.raises(ValueError, match="missing-tier"):
        AgentFactory.create_all(cfg, {"local-fast": _FakeChatWorker()}, ToolRegistry(), {})


def test_factory_rejects_unknown_rag_source(tmp_path):
    base = tmp_path / "base.md"
    base.write_text("BASE")
    cfg = _make_config(
        agents_dict={
            "chat_agent": {
                "required_worker_tier": "local-fast",
                "prompt": str(base),
                "tools": "all",
                "rag_sources": ["nonexistent"],
            }
        }
    )
    with pytest.raises(ValueError, match="nonexistent"):
        AgentFactory.create_all(
            cfg, {"local-fast": _FakeChatWorker()}, ToolRegistry(), {}
        )
