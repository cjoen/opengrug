# Dispatch Rules, Response Rules, and Dispatch Eval Harness — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Co-locate dispatch routing rules and response rules with tool registration, and add a dispatch eval harness for model tuning.

**Architecture:** Extend the `ToolRegistry` tuple from 5 to 8 elements to carry `dispatch_to`, `dispatcher_direct`, and `response_rules` per tool. The `Dispatcher` reads these to build a dynamic routing prompt and support direct tool execution. The `GrugRouter` injects `response_rules` into the system prompt for the reply step when a matching tool fires.

**Tech Stack:** Python 3, pytest, existing `core/registry.py`, `core/dispatcher.py`, `core/router.py`, `core/task.py`, `core/orchestrator.py`

**Spec:** `docs/superpowers/specs/2026-09-22-dispatch-response-rules-design.md`

## Global Constraints

- All new parameters on `register_python_tool` and `register_cli_tool` are optional with safe defaults (`dispatch_to=None`, `dispatcher_direct=False`, `response_rules=None`). No existing `register_tools()` call requires modification.
- `Dispatcher` must fall back to static `prompts/dispatcher.md` behavior when `registry=None`. No existing test may break.
- `response_rules` injection applies only to the reply step — it must not persist into `session["messages"]` or session history.
- Run tests with `python3 -m pytest tests/` after each task.

## Review Focus

- **Tool with no `dispatch_to`** — routing block omits the tool; LLM falls back to general rules. Expected: `fallback_agent` returned, no KeyError. → Test added in Task 2.
- **`dispatcher_direct=True` tool called with invalid arguments** — direct path bypasses agent but still hits `registry.execute()` validation. Expected: `MessageReply` with error text, not an uncaught exception. → Test added in Task 3.
- **`response_rules` tool fires when `max_steps=1`** — extra reply step must not re-invoke tools. Expected: reply step returns text output, not another `tool_output`. → Test added in Task 5.
- **Multiple tools fire in one step; only some have `response_rules`** — all matching rules collected, none silently dropped. Expected: augmented prompt contains rules from every matched tool. → Test added in Task 5.
- **`registry=None` in Dispatcher** — dynamic block skipped entirely. Expected: prompt identical to current `_load_prompt()` output. → Test added in Task 2.

---

## Task 1: Expand ToolRegistry tuple and add accessors

**Files:**
- Modify: `core/registry.py`
- Test: `tests/test_registry.py`

**Interfaces:**
- Produces:
  - `ToolRegistry.register_python_tool(..., dispatch_to: str = None, dispatcher_direct: bool = False, response_rules: str = None)`
  - `ToolRegistry.register_cli_tool(..., dispatch_to: str = None, dispatcher_direct: bool = False, response_rules: str = None)`
  - `ToolRegistry.get_dispatch_to(tool_name: str) -> str | None`
  - `ToolRegistry.get_response_rules(tool_name: str) -> str | None`
  - `ToolRegistry.get_dispatcher_direct_tools() -> list[str]`
  - `ToolRegistry.get_direct_tool_schemas() -> list[dict]`
  - `ToolRegistry._to_openai_schema(name: str, data: tuple) -> dict` (extracted from `get_all_schemas`)

- [ ] **Step 1: Write failing tests**

```python
# tests/test_registry.py  — add these test functions

from core.registry import ToolRegistry

def _make_registry():
    r = ToolRegistry()
    r.register_python_tool(
        name="add_note",
        schema={"description": "save a note", "type": "object", "properties": {"content": {"type": "string"}}},
        func=lambda content: "saved",
        dispatch_to="chat_agent",
        dispatcher_direct=False,
        response_rules="Confirm in one short line.",
    )
    r.register_python_tool(
        name="get_health",
        schema={"description": "check health", "type": "object", "properties": {}},
        func=lambda: "ok",
        dispatcher_direct=True,
    )
    r.register_python_tool(
        name="no_rules_tool",
        schema={"description": "plain tool", "type": "object", "properties": {}},
        func=lambda: "done",
    )
    return r


def test_get_dispatch_to_returns_value():
    r = _make_registry()
    assert r.get_dispatch_to("add_note") == "chat_agent"


def test_get_dispatch_to_returns_none_when_unset():
    r = _make_registry()
    assert r.get_dispatch_to("no_rules_tool") is None


def test_get_dispatch_to_returns_none_for_unknown_tool():
    r = _make_registry()
    assert r.get_dispatch_to("ghost") is None


def test_get_response_rules_returns_value():
    r = _make_registry()
    assert r.get_response_rules("add_note") == "Confirm in one short line."


def test_get_response_rules_returns_none_when_unset():
    r = _make_registry()
    assert r.get_response_rules("no_rules_tool") is None


def test_get_dispatcher_direct_tools_lists_flagged():
    r = _make_registry()
    assert "get_health" in r.get_dispatcher_direct_tools()
    assert "add_note" not in r.get_dispatcher_direct_tools()


def test_get_direct_tool_schemas_returns_only_direct():
    r = _make_registry()
    schemas = r.get_direct_tool_schemas()
    names = [s["function"]["name"] for s in schemas]
    assert "get_health" in names
    assert "add_note" not in names


def test_create_scoped_copies_new_fields():
    r = _make_registry()
    scoped = r.create_scoped(["add_note", "get_health"])
    assert scoped.get_dispatch_to("add_note") == "chat_agent"
    assert scoped.get_response_rules("add_note") == "Confirm in one short line."
    assert "get_health" in scoped.get_dispatcher_direct_tools()


def test_register_without_new_fields_uses_safe_defaults():
    r = ToolRegistry()
    r.register_python_tool(
        name="plain",
        schema={"type": "object", "properties": {}},
        func=lambda: "ok",
    )
    assert r.get_dispatch_to("plain") is None
    assert r.get_response_rules("plain") is None
    assert "plain" not in r.get_dispatcher_direct_tools()
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
python3 -m pytest tests/test_registry.py -k "dispatch_to or response_rules or dispatcher_direct or direct_tool_schemas or create_scoped_copies or without_new_fields" -v
```

Expected: `AttributeError` or `TypeError` — new methods and parameters do not exist yet.

- [ ] **Step 3: Implement the changes in `core/registry.py`**

Extract `_to_openai_schema` from the nested function inside `get_all_schemas` to a regular method. Expand the tuple. Add new parameters with defaults. Add four new methods.

The tuple index map after expansion:
```
index 0: schema
index 1: func / base_command
index 2: destructive
index 3: friendly_name
index 4: category
index 5: dispatch_to       ← new
index 6: dispatcher_direct ← new
index 7: response_rules    ← new
```

Change `register_python_tool` signature and body:

```python
def register_python_tool(self, name: str, schema: dict, func: Callable,
                          destructive: bool = False, friendly_name: str = None,
                          category: str = "SYSTEM", dispatch_to: str = None,
                          dispatcher_direct: bool = False,
                          response_rules: str = None):
    self._python_tools[name] = (
        schema, func, destructive, friendly_name or name, category,
        dispatch_to, dispatcher_direct, response_rules,
    )
```

Change `register_cli_tool` signature and body the same way (same new parameters at the same positions).

Change `execute` to unpack only the first 5 elements (indices 0-4) since it already uses positional unpacking:

```python
# in execute():
if tool_name in self._python_tools:
    schema, handler, is_destructive, _, _ = self._python_tools[tool_name][:5]
    is_cli = False
elif tool_name in self._cli_tools:
    schema, handler, is_destructive, _, _ = self._cli_tools[tool_name][:5]
    is_cli = True
```

Also fix `is_destructive`, `get_category`, and `get_category_description` to use `[:5]` or explicit index access if they unpack the full tuple.

Extract `_to_openai_schema` as a method:

```python
def _to_openai_schema(self, name: str, data: tuple) -> dict:
    schema = data[0]
    func_def = {
        "name": name,
        "description": schema.get("description", ""),
        "parameters": {
            "type": "object",
            "properties": schema.get("properties", {}),
        },
    }
    if "required" in schema:
        func_def["parameters"]["required"] = schema["required"]
    return {"type": "function", "function": func_def}
```

Update `get_all_schemas` to call `self._to_openai_schema(name, data)` instead of the nested function.

Add the four new methods:

```python
def get_dispatch_to(self, tool_name: str) -> Optional[str]:
    if tool_name in self._python_tools:
        return self._python_tools[tool_name][5]
    if tool_name in self._cli_tools:
        return self._cli_tools[tool_name][5]
    return None

def get_response_rules(self, tool_name: str) -> Optional[str]:
    if tool_name in self._python_tools:
        return self._python_tools[tool_name][7]
    if tool_name in self._cli_tools:
        return self._cli_tools[tool_name][7]
    return None

def get_dispatcher_direct_tools(self) -> list:
    result = []
    for name, data in self._python_tools.items():
        if data[6]:
            result.append(name)
    for name, data in self._cli_tools.items():
        if data[6]:
            result.append(name)
    return result

def get_direct_tool_schemas(self) -> list:
    schemas = []
    for name, data in self._python_tools.items():
        if data[6]:
            schemas.append(self._to_openai_schema(name, data))
    for name, data in self._cli_tools.items():
        if data[6]:
            schemas.append(self._to_openai_schema(name, data))
    return schemas
```

- [ ] **Step 4: Run all tests**

```bash
python3 -m pytest tests/ -v
```

Expected: all tests pass, including the new registry tests.

- [ ] **Step 5: Commit**

```bash
git add core/registry.py tests/test_registry.py
git commit -m "feat: expand ToolRegistry tuple with dispatch_to, dispatcher_direct, response_rules"
```

---

## Task 2: Extend Dispatcher with dynamic routing prompt and direct tool support

**Files:**
- Modify: `core/dispatcher.py`
- Test: `tests/test_dispatcher.py`

**Interfaces:**
- Consumes:
  - `ToolRegistry.get_dispatcher_direct_tools() -> list[str]`
  - `ToolRegistry.get_direct_tool_schemas() -> list[dict]`
  - `ToolRegistry.get_dispatch_to(tool_name: str) -> str | None`
  - `ToolRegistry.get_all_schemas() -> list[dict]` (for routing hint tool names)
- Produces:
  - `DispatchDecision.direct_tool: str | None`
  - `DispatchDecision.direct_args: dict | None`
  - `Dispatcher.__init__(chat_worker, registry=None, prompt_path=..., fallback_agent=...)`
  - `Dispatcher._build_prompt(available_agents: list[str]) -> str`
  - `Dispatcher.classify(...)` — unchanged signature, returns extended `DispatchDecision`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_dispatcher.py  — add these test functions

from core.registry import ToolRegistry
from core.interfaces import LLMResponse


def _make_registry_with_rules():
    r = ToolRegistry()
    r.register_python_tool(
        name="add_note",
        schema={"description": "save a note", "type": "object", "properties": {}},
        func=lambda: "ok",
        dispatch_to="chat_agent",
    )
    r.register_python_tool(
        name="get_health",
        schema={"description": "check health", "type": "object", "properties": {}},
        func=lambda: "ok",
        dispatcher_direct=True,
    )
    return r


def test_build_prompt_appends_routing_block(dispatcher_prompt):
    r = _make_registry_with_rules()
    worker = _FakeWorker('{"agent": "chat_agent", "context": "x"}')
    d = Dispatcher(worker, registry=r, prompt_path=dispatcher_prompt)
    prompt = d._build_prompt(["chat_agent"])
    assert "chat_agent" in prompt
    assert "add_note" in prompt
    assert "get_health" in prompt
    assert "Direct tools" in prompt


def test_build_prompt_without_registry_returns_static_file(dispatcher_prompt):
    worker = _FakeWorker('{"agent": "chat_agent", "context": "x"}')
    d = Dispatcher(worker, prompt_path=dispatcher_prompt)
    prompt = d._build_prompt(["chat_agent"])
    # Static file contents only — no routing block appended
    assert "Direct tools" not in prompt


def test_tool_with_no_dispatch_to_omitted_from_routing_block(dispatcher_prompt):
    r = ToolRegistry()
    r.register_python_tool(
        name="orphan_tool",
        schema={"description": "no rules", "type": "object", "properties": {}},
        func=lambda: "ok",
    )
    worker = _FakeWorker('{"agent": "chat_agent", "context": "x"}')
    d = Dispatcher(worker, registry=r, prompt_path=dispatcher_prompt)
    prompt = d._build_prompt(["chat_agent"])
    # orphan_tool has no dispatch_to so it must not appear under any agent
    # (it may appear in direct tools section only if dispatcher_direct=True)
    assert "orphan_tool" not in prompt


def test_classify_returns_direct_tool_on_tool_call(dispatcher_prompt):
    r = _make_registry_with_rules()

    class _DirectWorker:
        def chat(self, system_prompt, messages, tools=None):
            return LLMResponse(
                content="",
                tool_calls=[{"tool": "get_health", "arguments": {}}],
            )

    d = Dispatcher(_DirectWorker(), registry=r, prompt_path=dispatcher_prompt)
    decision = d.classify("how are you", history=[], available_agents=["chat_agent"])
    assert decision.direct_tool == "get_health"
    assert decision.direct_args == {}
    assert decision.agent == "chat_agent"  # defaults to fallback_agent


def test_classify_direct_tool_falls_back_on_worker_exception(dispatcher_prompt):
    r = _make_registry_with_rules()

    class Boom:
        def chat(self, *a, **kw):
            raise RuntimeError("boom")

    d = Dispatcher(Boom(), registry=r, prompt_path=dispatcher_prompt)
    decision = d.classify("hi", history=[], available_agents=["chat_agent"])
    assert decision.agent == "chat_agent"
    assert decision.direct_tool is None
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
python3 -m pytest tests/test_dispatcher.py -k "build_prompt or direct_tool or routing_block or no_dispatch_to" -v
```

Expected: `AttributeError` — `_build_prompt`, `direct_tool`, `direct_args` do not exist yet.

- [ ] **Step 3: Implement the changes in `core/dispatcher.py`**

Extend `DispatchDecision`:

```python
@dataclass
class DispatchDecision:
    agent: str
    context: str
    plan: Optional[list[str]] = None
    direct_tool: Optional[str] = None
    direct_args: Optional[dict] = None
```

Change `Dispatcher.__init__` to accept `registry`:

```python
def __init__(self, chat_worker, registry=None,
             prompt_path: str = "prompts/dispatcher.md",
             fallback_agent: str = "chat_agent"):
    self.chat_worker = chat_worker
    self._registry = registry
    self.prompt_path = prompt_path
    self.fallback_agent = fallback_agent
```

Replace `_load_prompt` with `_build_prompt`:

```python
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
```

Update `classify` to use `_build_prompt`, pass direct tool schemas, and handle tool call responses:

```python
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
```

- [ ] **Step 4: Run all tests**

```bash
python3 -m pytest tests/ -v
```

Expected: all tests pass. The existing dispatcher tests must still pass because `registry` defaults to `None`.

- [ ] **Step 5: Commit**

```bash
git add core/dispatcher.py tests/test_dispatcher.py
git commit -m "feat: extend Dispatcher with dynamic routing prompt and direct tool execution"
```

---

## Task 3: Add `direct_tool`/`direct_args` to Task and handle them in Orchestrator

**Files:**
- Modify: `core/task.py`
- Modify: `core/orchestrator.py`
- Test: `tests/test_orchestrator_queue.py`

**Interfaces:**
- Consumes:
  - `DispatchDecision.direct_tool: str | None`
  - `DispatchDecision.direct_args: dict | None`
  - `ToolRegistry.execute(tool_name, arguments, skip_hitl) -> ToolExecutionResult`
- Produces:
  - `Task.direct_tool: str | None`
  - `Task.direct_args: Optional[dict]`
  - `Orchestrator._run_direct_tool(task: Task) -> MessageReply`
  - `Orchestrator._classify` returns 5-tuple: `(agent_name, context, plan, direct_tool, direct_args)`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_orchestrator_queue.py  — add these test functions

from core.task import Task, TaskPriority
from core.orchestrator import Orchestrator, MessageReply, ErrorReply


def _make_orchestrator(registry=None):
    """Build a minimal Orchestrator with a fake session store and storage."""
    from core.registry import ToolRegistry
    from core.router import GrugRouter
    from types import SimpleNamespace

    reg = registry or ToolRegistry()
    router = GrugRouter(registry=reg)

    cfg = SimpleNamespace(
        memory=SimpleNamespace(thread_history_limit=10, rag_result_limit=3),
        dispatcher=SimpleNamespace(worker_tier="local-fast"),
        workers=SimpleNamespace(),
        queue=SimpleNamespace(),
    )

    return Orchestrator(
        router=router,
        registry=reg,
        session_store=_FakeSessionStore(),
        storage=_FakeStorage(),
        summarizer=None,
        vector_memory=_FakeVectorMemory(),
        config=cfg,
        build_system_prompt=lambda base, tail, **kw: base,
        find_turn_boundary=lambda msgs: 1,
        auto_offload_pruned_turns=lambda *a: None,
        base_prompt="You are Grug.",
        worker_count=1,
    )


def test_task_has_direct_tool_field():
    t = Task(session_id="s", user_id="u", agent_name="chat_agent", context="hi")
    assert t.direct_tool is None
    assert t.direct_args is None


def test_task_direct_tool_can_be_set():
    t = Task(
        session_id="s",
        user_id="u",
        agent_name="chat_agent",
        context="hi",
        direct_tool="get_health",
        direct_args={"verbose": False},
    )
    assert t.direct_tool == "get_health"
    assert t.direct_args == {"verbose": False}


def test_run_direct_tool_returns_message_reply():
    from core.registry import ToolRegistry

    reg = ToolRegistry()
    reg.register_python_tool(
        name="get_health",
        schema={"type": "object", "properties": {}},
        func=lambda: "all good",
        dispatcher_direct=True,
    )
    orch = _make_orchestrator(registry=reg)
    task = Task(
        session_id="s",
        user_id="u",
        agent_name="chat_agent",
        context="hi",
        direct_tool="get_health",
        direct_args={},
    )
    result = orch._run_direct_tool(task)
    assert isinstance(result, MessageReply)
    assert "all good" in result.text


def test_run_direct_tool_with_invalid_args_returns_error_reply():
    from core.registry import ToolRegistry

    reg = ToolRegistry()
    reg.register_python_tool(
        name="get_health",
        schema={
            "type": "object",
            "properties": {"required_field": {"type": "string"}},
            "required": ["required_field"],
        },
        func=lambda required_field: "ok",
        dispatcher_direct=True,
    )
    orch = _make_orchestrator(registry=reg)
    task = Task(
        session_id="s",
        user_id="u",
        agent_name="chat_agent",
        context="hi",
        direct_tool="get_health",
        direct_args={},  # missing required_field
    )
    result = orch._run_direct_tool(task)
    assert isinstance(result, MessageReply)
    # execute() returns ToolExecutionResult with success=False — still a MessageReply, not a crash
    assert result.text is not None
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
python3 -m pytest tests/test_orchestrator_queue.py -k "direct_tool" -v
```

Expected: `TypeError` — `Task` does not accept `direct_tool` yet.

- [ ] **Step 3: Add `direct_tool` and `direct_args` fields to `Task` in `core/task.py`**

Add two fields after `plan`:

```python
direct_tool: Optional[str] = None
direct_args: Optional[dict] = None
```

The `Optional` import is already present in `core/task.py`.

- [ ] **Step 4: Add `_run_direct_tool` and update `_classify` and `_run_task` in `core/orchestrator.py`**

Update `_classify` to return a 5-tuple:

```python
def _classify(self, session_id, text):
    if self.dispatcher is None or not self.agents:
        return "chat_agent", text, None, None, None
    try:
        session = self.session_store.get_or_create(session_id, "")
        history = session["messages"][-self.config.memory.thread_history_limit:]
        decision = self.dispatcher.classify(
            user_message=text,
            history=history,
            available_agents=list(self.agents.keys()),
        )
        return (
            decision.agent,
            decision.context or text,
            decision.plan,
            decision.direct_tool,
            decision.direct_args,
        )
    except Exception as e:
        print(f"[orchestrator] dispatcher error, defaulting to chat_agent: {e}")
        return "chat_agent", text, None, None, None
```

Update `_classify_and_enqueue` to unpack 5 values and set new fields:

```python
def _classify_and_enqueue(self, item: dict) -> Task:
    agent_name, context, plan, direct_tool, direct_args = self._classify(
        item["session_id"], item["text"]
    )
    task = Task(
        session_id=item["session_id"],
        user_id=item["user_id"],
        agent_name=agent_name,
        context=context,
        priority=item["priority"],
        plan=plan,
        direct_tool=direct_tool,
        direct_args=direct_args,
        metadata={"raw_text": item["text"], **item["metadata"]},
        on_result=item["on_result"],
    )
    self._queue.enqueue(task)
    return task
```

Add `_run_direct_tool` method:

```python
def _run_direct_tool(self, task: Task) -> MessageReply:
    result = self.registry.execute(task.direct_tool, task.direct_args or {})
    return MessageReply(text=result.output or "(no output)")
```

Add the direct-tool branch in `_run_task`, immediately after the existing `scheduled_tool` check (around line 184):

```python
# Direct dispatch: Dispatcher resolved the tool without routing to an agent.
if task.direct_tool:
    result_event = self._run_direct_tool(task)
    task.transition(TaskState.COMPLETED)
    return
```

- [ ] **Step 5: Run all tests**

```bash
python3 -m pytest tests/ -v
```

Expected: all tests pass.

- [ ] **Step 6: Commit**

```bash
git add core/task.py core/orchestrator.py tests/test_orchestrator_queue.py
git commit -m "feat: add direct_tool to Task and Orchestrator direct-execution branch"
```

---

## Task 4: Wire `registry` into `Dispatcher` in `app.py`

**Files:**
- Modify: `app.py`

**Interfaces:**
- Consumes: `Dispatcher.__init__(chat_worker, registry=None, ...)` from Task 2

- [ ] **Step 1: Update the `Dispatcher` instantiation in `app.py`**

Find the line (around line 82):

```python
dispatcher = Dispatcher(chat_worker=chat_worker)
```

Change it to:

```python
dispatcher = Dispatcher(chat_worker=chat_worker, registry=registry)
```

The `registry` object is already created earlier in `app.py` (line 59: `registry = ToolRegistry()`) and is fully populated with tools before `dispatcher` is constructed.

- [ ] **Step 2: Run all tests**

```bash
python3 -m pytest tests/ -v
```

Expected: all tests pass. No new tests needed — this is a one-line wiring change covered by the existing dispatcher and orchestrator tests.

- [ ] **Step 3: Commit**

```bash
git add app.py
git commit -m "feat: pass registry to Dispatcher for dynamic routing prompt"
```

---

## Task 5: Inject `response_rules` in `GrugRouter.route_message`

**Files:**
- Modify: `core/router.py`
- Test: `tests/test_router.py`

**Interfaces:**
- Consumes:
  - `ToolRegistry.get_response_rules(tool_name: str) -> str | None` from Task 1

- [ ] **Step 1: Write failing tests**

```python
# tests/test_router.py  — add these test functions

from core.registry import ToolRegistry
from core.router import GrugRouter
from core.interfaces import LLMResponse


def _router_with_tool(response_rules=None):
    """Build a GrugRouter with a single tool that optionally has response_rules."""
    registry = ToolRegistry()
    registry.register_python_tool(
        name="save_thing",
        schema={"type": "object", "properties": {"item": {"type": "string"}}, "required": ["item"]},
        func=lambda item: f"saved: {item}",
        response_rules=response_rules,
    )
    registry.register_python_tool(
        name="reply_to_user",
        schema={"type": "object", "properties": {"message": {"type": "string"}}},
        func=lambda message: message,
    )
    return GrugRouter(registry=registry)


def test_response_rules_appended_to_system_prompt_on_reply_step():
    router = _router_with_tool(response_rules="Confirm in one short line.")

    calls = []

    def mock_chat(sys_prompt, msgs, tools=None):
        calls.append(sys_prompt)
        if len(calls) == 1:
            # Step 1: tool call
            return LLMResponse(
                content="",
                tool_calls=[{"tool": "save_thing", "arguments": {"item": "foo"}}],
            )
        # Step 2: reply
        return LLMResponse(
            content="",
            tool_calls=[{"tool": "reply_to_user", "arguments": {"message": "saved!"}}],
        )

    router.invoke_chat = mock_chat
    result = router.route_message("save foo", max_steps=2)

    assert len(calls) == 2
    assert "Response Guidance" in calls[1]
    assert "Confirm in one short line." in calls[1]
    assert "Response Guidance" not in calls[0]


def test_response_rules_not_appended_when_tool_has_none():
    router = _router_with_tool(response_rules=None)

    calls = []

    def mock_chat(sys_prompt, msgs, tools=None):
        calls.append(sys_prompt)
        if len(calls) == 1:
            return LLMResponse(
                content="",
                tool_calls=[{"tool": "save_thing", "arguments": {"item": "foo"}}],
            )
        return LLMResponse(
            content="",
            tool_calls=[{"tool": "reply_to_user", "arguments": {"message": "done"}}],
        )

    router.invoke_chat = mock_chat
    router.route_message("save foo", max_steps=2)

    assert "Response Guidance" not in calls[1]


def test_response_rules_trigger_extra_reply_step_when_max_steps_1():
    router = _router_with_tool(response_rules="Say done in one word.")

    calls = []

    def mock_chat(sys_prompt, msgs, tools=None):
        calls.append(sys_prompt)
        if len(calls) == 1:
            return LLMResponse(
                content="",
                tool_calls=[{"tool": "save_thing", "arguments": {"item": "bar"}}],
            )
        # Extra reply step
        return LLMResponse(
            content="",
            tool_calls=[{"tool": "reply_to_user", "arguments": {"message": "done"}}],
        )

    router.invoke_chat = mock_chat
    result = router.route_message("save bar", max_steps=1)

    # Two LLM calls occurred even though max_steps=1
    assert len(calls) == 2
    assert "Response Guidance" in calls[1]
    assert result.output == "done"


def test_multiple_tools_rules_all_collected():
    registry = ToolRegistry()
    registry.register_python_tool(
        name="tool_a",
        schema={"type": "object", "properties": {"x": {"type": "string"}}, "required": ["x"]},
        func=lambda x: f"a:{x}",
        response_rules="Rule A.",
    )
    registry.register_python_tool(
        name="tool_b",
        schema={"type": "object", "properties": {"y": {"type": "string"}}, "required": ["y"]},
        func=lambda y: f"b:{y}",
        response_rules="Rule B.",
    )
    registry.register_python_tool(
        name="reply_to_user",
        schema={"type": "object", "properties": {"message": {"type": "string"}}},
        func=lambda message: message,
    )
    router = GrugRouter(registry=registry)

    calls = []

    def mock_chat(sys_prompt, msgs, tools=None):
        calls.append(sys_prompt)
        if len(calls) == 1:
            return LLMResponse(
                content="",
                tool_calls=[
                    {"tool": "tool_a", "arguments": {"x": "1"}},
                    {"tool": "tool_b", "arguments": {"y": "2"}},
                ],
            )
        return LLMResponse(
            content="",
            tool_calls=[{"tool": "reply_to_user", "arguments": {"message": "ok"}}],
        )

    router.invoke_chat = mock_chat
    router.route_message("do both", max_steps=2)

    assert "Rule A." in calls[1]
    assert "Rule B." in calls[1]
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
python3 -m pytest tests/test_router.py -k "response_rules or extra_reply_step or multiple_tools_rules" -v
```

Expected: FAIL — response rules logic does not exist yet; the second call's system prompt does not contain "Response Guidance".

- [ ] **Step 3: Implement response rules injection in `core/router.py`**

In `route_message`, after the `_parse_and_execute` call and before the "last step" early return, insert response rules collection and apply them to the next `_invoke` call. The key change is tracking `current_system_prompt` (which may be augmented) separately from the original `system_prompt`.

Replace the loop body with:

```python
try:
    schemas = active_registry.get_all_schemas()
    recent_calls = []
    current_system_prompt = system_prompt

    for step in range(max_steps):
        if cancel_event is not None and cancel_event.is_set():
            return ToolExecutionResult(
                success=False, output="Task cancelled", tool_output=None,
            )
        llm_response = _invoke(current_system_prompt, message_history, schemas)
        result = self._parse_and_execute(llm_response, user_message,
                                         registry=active_registry)

        if result.requires_approval:
            return result
        if result.tool_output is None:
            return result

        # Collect response_rules for every tool that fired this step
        fired_names = [a.get("tool") for a in (llm_response.tool_calls or [])]
        rules = [
            r for n in fired_names
            if (r := active_registry.get_response_rules(n))
        ]
        next_prompt = (
            system_prompt + "\n\n## Response Guidance\n" + "\n".join(rules)
            if rules else system_prompt
        )

        if step == max_steps - 1:
            if not rules:
                return result
            # Extra reply step: tool fired on the last allowed step and has rules
            message_history = list(message_history)
            message_history.append({"role": "assistant",
                                     "content": llm_response.content or ""})
            message_history.append({"role": "tool", "content": result.tool_output})
            llm_response2 = _invoke(next_prompt, message_history, schemas)
            return self._parse_and_execute(llm_response2, user_message,
                                           registry=active_registry)

        # Circuit breaker
        call_sig = str(llm_response.tool_calls)
        if call_sig in recent_calls:
            print(f"[router] circuit breaker: repeated tool call, stopping step loop")
            return result
        recent_calls.append(call_sig)

        message_history = list(message_history)
        message_history.append({"role": "assistant",
                                 "content": llm_response.content or ""})
        message_history.append({"role": "tool", "content": result.tool_output})
        current_system_prompt = next_prompt  # augmented prompt for reply step

    return result
finally:
    self._request_state.user_message = None
```

- [ ] **Step 4: Run all tests**

```bash
python3 -m pytest tests/ -v
```

Expected: all tests pass.

- [ ] **Step 5: Commit**

```bash
git add core/router.py tests/test_router.py
git commit -m "feat: inject response_rules into system prompt on reply step"
```

---

## Task 6: Add dispatch and response fields to tool registrations

**Files:**
- Modify: `tools/notes.py`, `tools/tasks.py`, `tools/health.py`, `tools/scheduler_tools.py`, `tools/system.py`, `tools/instructions.py`, `tools/operator.py`, `tools/dispatch.py`, `tools/grug_tasks.py`

**Interfaces:**
- Consumes: all new optional params from Task 1

This task annotates existing tool registrations. All fields are optional — this task does not change runtime behavior, only adds metadata for the Dispatcher and router.

- [ ] **Step 1: Add `dispatch_to` and `response_rules` to `tools/notes.py`**

```python
# add_note
registry.register_python_tool(
    name="add_note",
    ...
    dispatch_to="chat_agent",
    response_rules="Confirm in one short line that the note was saved.",
)

# get_recent_notes
registry.register_python_tool(
    name="get_recent_notes",
    ...
    dispatch_to="chat_agent",
    response_rules="Present the notes directly. Do not add preamble.",
)

# query_memory
registry.register_python_tool(
    name="query_memory",
    ...
    dispatch_to="chat_agent",
)

# search
registry.register_python_tool(
    name="search",
    ...
    dispatch_to="chat_agent",
)
```

- [ ] **Step 2: Add fields to `tools/tasks.py`**

```python
# add_task
registry.register_python_tool(
    name="add_task",
    ...
    dispatch_to="chat_agent",
    response_rules="Confirm in one short line that the task was added, include its ID.",
)

# list_tasks
registry.register_python_tool(
    name="list_tasks",
    ...
    dispatch_to="chat_agent",
    response_rules="Present the task list directly. Do not add preamble.",
)

# complete_task
registry.register_python_tool(
    name="complete_task",
    ...
    dispatch_to="chat_agent",
    response_rules="Confirm in one short line that the task was marked complete.",
)
```

- [ ] **Step 3: Add fields to `tools/health.py`**

`tools/health.py` registers `grug_health` and `system_health`. Both are read-only status queries — good candidates for `dispatcher_direct=True`.

```python
# grug_health
registry.register_python_tool(
    name="grug_health",
    ...
    dispatcher_direct=True,
    response_rules="Give a brief status summary. Use bullet points.",
)

# system_health
registry.register_python_tool(
    name="system_health",
    ...
    dispatcher_direct=True,
    response_rules="Give a brief status summary. Use bullet points.",
)
```

- [ ] **Step 4: Add fields to remaining tool files**

For each of `tools/scheduler_tools.py`, `tools/system.py`, `tools/instructions.py`, `tools/operator.py`, `tools/dispatch.py`, `tools/grug_tasks.py`:

- Open the file and read the tool descriptions.
- Add `dispatch_to="chat_agent"` to every tool that is conversational (saves data, reads data, responds to user requests).
- Add `dispatcher_direct=True` to every tool that is a pure read-only status or lookup tool (no side effects, no agent reasoning needed).
- Add `response_rules="..."` to any tool where the reply format matters (confirmations, listings, status outputs).

- [ ] **Step 5: Run all tests**

```bash
python3 -m pytest tests/ -v
```

Expected: all tests pass. No behavior changes — all new fields are optional.

- [ ] **Step 6: Commit**

```bash
git add tools/
git commit -m "feat: add dispatch_to, dispatcher_direct, and response_rules to tool registrations"
```

---

## Task 7: Dispatch eval harness

**Files:**
- Create: `evals/run_dispatch_evals.py`
- Create: `evals/dispatch_golden_dataset.jsonl`

**Interfaces:**
- Consumes:
  - `Dispatcher.__init__(chat_worker, registry=None, ...)` from Task 2
  - `Dispatcher.classify(user_message, history, available_agents) -> DispatchDecision`
  - `WorkerFactory.create_all(config)` (existing)
  - `ToolRegistry` and all `register_tools()` functions (existing, same pattern as `run_evals.py`)

- [ ] **Step 1: Create the golden dataset**

Create `evals/dispatch_golden_dataset.jsonl` with at least one case per agent and one case per `dispatcher_direct` tool. Seed it now with concrete cases based on the tool registrations from Task 6.

```jsonl
# =============================================================================
# Dispatch Golden Dataset — Dispatcher Intent Classification Evals
#
# Format: One JSON object per line.
#   session_id:           Unique identifier
#   category:             Tag for filtering (NOTES, TASKS, HEALTH, SYSTEM)
#   messages:             Conversation history (Ollama chat format)
#   expected_agent:       The agent the Dispatcher should route to (or null)
#   expected_direct_tool: The direct tool the Dispatcher should call (or null)
# =============================================================================

{"session_id": "dispatch-001", "category": "NOTES", "messages": [{"role": "user", "content": "save a note about the standup"}], "expected_agent": "chat_agent", "expected_direct_tool": null}
{"session_id": "dispatch-002", "category": "NOTES", "messages": [{"role": "user", "content": "what did I say about the database migration?"}], "expected_agent": "chat_agent", "expected_direct_tool": null}
{"session_id": "dispatch-003", "category": "TASKS", "messages": [{"role": "user", "content": "add a task to fix the login bug"}], "expected_agent": "chat_agent", "expected_direct_tool": null}
{"session_id": "dispatch-004", "category": "TASKS", "messages": [{"role": "user", "content": "show me my tasks"}], "expected_agent": "chat_agent", "expected_direct_tool": null}
{"session_id": "dispatch-005", "category": "TASKS", "messages": [{"role": "user", "content": "mark task 3 complete"}], "expected_agent": "chat_agent", "expected_direct_tool": null}
{"session_id": "dispatch-006", "category": "HEALTH", "messages": [{"role": "user", "content": "how are you doing?"}], "expected_agent": null, "expected_direct_tool": "grug_health"}
{"session_id": "dispatch-007", "category": "HEALTH", "messages": [{"role": "user", "content": "what's your health status?"}], "expected_agent": null, "expected_direct_tool": "system_health"}
{"session_id": "dispatch-008", "category": "SYSTEM", "messages": [{"role": "user", "content": "hey grug"}], "expected_agent": "chat_agent", "expected_direct_tool": null}
{"session_id": "dispatch-009", "category": "SYSTEM", "messages": [{"role": "user", "content": "what time is it?"}], "expected_agent": "chat_agent", "expected_direct_tool": null}
```

- [ ] **Step 2: Create `evals/run_dispatch_evals.py`**

```python
#!/usr/bin/env python3
"""Dispatch Eval Harness for OpenGrug.

Tests dispatcher.classify() decisions against a golden dataset.
Mirrors run_evals.py in structure and CLI flags.

Usage:
    python evals/run_dispatch_evals.py
    python evals/run_dispatch_evals.py --filter dispatch-001
    python evals/run_dispatch_evals.py --category HEALTH
    python evals/run_dispatch_evals.py --repeat 3 --output results.json
"""
import os
import sys
import json
import time
import warnings
import argparse

warnings.filterwarnings("ignore", message="urllib3 v2 only supports OpenSSL")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.registry import ToolRegistry
from core.dispatcher import Dispatcher
from core.backends.factory import WorkerFactory
from core.config import config
from core.router import GrugRouter


# ---------------------------------------------------------------------------
# Mock dependencies (same as run_evals.py)
# ---------------------------------------------------------------------------

class _MockStorage:
    def add_note(self, **kw): return "stub"
    def get_raw_notes(self, **kw): return ""
    def get_capped_tail(self, *a): return ""
    def append_log(self, *a): pass

class _MockChatWorker:
    def generate(self, prompt): return "stub title"
    def chat(self, *a, **kw): return None

class _MockVectorMemory:
    def query_memory(self, query, **kw): return "stub"
    def query_memory_raw(self, *a, **kw): return []
    def stats(self): return {"enabled": False, "block_count": 0, "db_size": 0}

class _MockSessionStore:
    def session_count(self): return 0

class _MockMessageQueue:
    worker_count = 1

class _MockScheduleStore:
    from datetime import timezone
    tz = timezone.utc
    def add_schedule(self, **kw): return 1
    def list_schedules(self, **kw): return []
    def delete(self, *a): pass

class _MockTaskList:
    def add_task(self, **kw): return "stub"
    def list_tasks(self, **kw): return "stub"
    def complete_task(self, **kw): return "stub"


def _register_production_schemas(registry, router):
    mock_storage = _MockStorage()
    mock_chat_worker = _MockChatWorker()
    mock_vectors = _MockVectorMemory()
    mock_sessions = _MockSessionStore()
    mock_queue = _MockMessageQueue()
    mock_schedule_store = _MockScheduleStore()
    mock_task_list = _MockTaskList()
    mock_brain_dir = "/tmp/grug_eval_brain"
    mock_worker_pool = {"eval-worker": mock_chat_worker}

    from tools.system import register_tools as register_system_tools
    from tools.notes import register_tools as register_note_tools
    from tools.tasks import register_tools as register_task_tools
    from tools.scheduler_tools import register_tools as register_scheduler_tools
    from tools.health import register_tools as register_health_tools

    register_system_tools(registry, router)
    register_note_tools(registry, mock_storage, mock_chat_worker, mock_vectors, mock_brain_dir)
    register_task_tools(registry, mock_task_list, mock_storage)
    register_scheduler_tools(registry, mock_schedule_store, router, config)
    register_health_tools(registry, mock_vectors, mock_sessions, mock_queue,
                          mock_schedule_store, mock_worker_pool, mock_brain_dir)


# ---------------------------------------------------------------------------
# Main harness
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="OpenGrug Dispatch Eval Harness")
    parser.add_argument("--filter", help="Run only cases matching this session_id prefix")
    parser.add_argument("--category", help="Run only cases matching this category tag")
    parser.add_argument("--output", help="Write JSON results to this file")
    parser.add_argument("--repeat", type=int, default=1,
                        help="Run each case N times for flake detection")
    args = parser.parse_args()

    dataset_path = os.path.join(os.path.dirname(__file__), "dispatch_golden_dataset.jsonl")
    if not os.path.exists(dataset_path):
        print(f"Dataset not found at {dataset_path}")
        sys.exit(1)

    # 1. Setup workers
    worker_pool = WorkerFactory.create_all(config)
    chat_worker = worker_pool[config.dispatcher.worker_tier]

    print(f"Dispatch Evals — Worker: {chat_worker.model_name} ({chat_worker.backend_name})")
    print(f"{'='*60}")

    # 2. Build registry with production schemas
    registry = ToolRegistry()
    router = GrugRouter(registry=registry, storage=None, chat_worker=chat_worker)
    _register_production_schemas(registry, router)

    # 3. Build dispatcher with registry for dynamic routing hints
    available_agents = list(getattr(config, "agents", None) and vars(config.agents) or {"chat_agent": None})
    dispatcher = Dispatcher(
        chat_worker=chat_worker,
        registry=registry,
        prompt_path="prompts/dispatcher.md",
    )

    tool_count = len(registry.get_all_schemas())
    direct_count = len(registry.get_dispatcher_direct_tools())
    print(f"   Registered {tool_count} tools ({direct_count} dispatcher-direct)")
    print(f"   Available agents: {', '.join(available_agents)}")
    print(f"{'='*60}\n")

    # 4. Load and run cases
    passed, failed, errors = 0, 0, 0
    results = []

    with open(dataset_path, "r", encoding="utf-8") as f:
        for line_no, line in enumerate(f, 1):
            if not line.strip() or line.startswith("#"):
                continue

            try:
                case = json.loads(line)
            except json.JSONDecodeError as e:
                print(f"  WARNING  PARSE ERROR on line {line_no}: {e}")
                errors += 1
                continue

            session_id = case.get("session_id", f"line-{line_no}")
            category = case.get("category", "")
            messages = case.get("messages", [])
            expected_agent = case.get("expected_agent")
            expected_direct = case.get("expected_direct_tool")

            if args.filter and not session_id.startswith(args.filter):
                continue
            if args.category and category.upper() != args.category.upper():
                continue

            label = messages[-1]["content"][:55] if messages else "empty"
            repeat = args.repeat

            for run in range(repeat):
                run_label = (
                    f"  [{session_id}]" if repeat == 1
                    else f"  [{session_id} run {run+1}/{repeat}]"
                )
                print(f"{run_label} {label}...")

                try:
                    start_time = time.time()
                    decision = dispatcher.classify(
                        user_message=messages[-1]["content"],
                        history=messages[:-1],
                        available_agents=available_agents,
                    )
                    duration = time.time() - start_time

                    case_passed = True
                    failure_reason = ""

                    if expected_direct is not None:
                        if decision.direct_tool != expected_direct:
                            case_passed = False
                            failure_reason = (
                                f"expected direct_tool='{expected_direct}', "
                                f"got direct_tool='{decision.direct_tool}' "
                                f"(agent='{decision.agent}')"
                            )
                    elif expected_agent is not None:
                        if decision.agent != expected_agent or decision.direct_tool is not None:
                            case_passed = False
                            failure_reason = (
                                f"expected agent='{expected_agent}', "
                                f"got agent='{decision.agent}' "
                                f"direct_tool='{decision.direct_tool}'"
                            )

                    if case_passed:
                        summary = (
                            f"direct:{decision.direct_tool}"
                            if decision.direct_tool
                            else f"agent:{decision.agent}"
                        )
                        print(f"    PASS ({duration:.2f}s) -> {summary}")
                        passed += 1
                    else:
                        print(f"    FAIL: {failure_reason}")
                        failed += 1

                    results.append({
                        "session_id": session_id,
                        "run": run + 1 if repeat > 1 else None,
                        "passed": case_passed,
                        "duration": round(duration, 2),
                        "expected_agent": expected_agent,
                        "expected_direct_tool": expected_direct,
                        "actual_agent": decision.agent,
                        "actual_direct_tool": decision.direct_tool,
                        "failure_reason": failure_reason,
                    })

                except Exception as e:
                    print(f"    WARNING  ERROR: {e}")
                    errors += 1
                    results.append({
                        "session_id": session_id,
                        "run": run + 1 if repeat > 1 else None,
                        "passed": False,
                        "error": str(e),
                    })

    # 5. Summary
    total = passed + failed + errors
    print(f"\n{'='*60}")
    print(f"Results: {passed} passed, {failed} failed, {errors} errors ({total} total)")

    failed_cases = [r for r in results if not r.get("passed")]
    if failed_cases:
        print(f"\n{'─'*60}")
        print("FAILED CASES:")
        print(f"{'─'*60}")
        for r in failed_cases:
            sid = r["session_id"]
            run_suffix = f" (run {r['run']})" if r.get("run") else ""
            if "error" in r:
                print(f"  {sid}{run_suffix}: ERROR — {r['error']}")
            else:
                print(
                    f"  {sid}{run_suffix}: "
                    f"expected agent={r['expected_agent']} direct={r['expected_direct_tool']} "
                    f"→ got agent={r['actual_agent']} direct={r['actual_direct_tool']}"
                )
                if r.get("failure_reason"):
                    print(f"    -- {r['failure_reason']}")

    if args.repeat > 1:
        from collections import defaultdict
        by_case = defaultdict(lambda: {"passed": 0, "failed": 0})
        for r in results:
            key = r["session_id"]
            by_case[key]["passed" if r.get("passed") else "failed"] += 1
        flaky = {k: v for k, v in by_case.items() if v["passed"] > 0 and v["failed"] > 0}
        if flaky:
            print(f"\n{'─'*60}")
            print(f"FLAKY CASES ({len(flaky)}):")
            print(f"{'─'*60}")
            for sid, counts in flaky.items():
                total_runs = counts["passed"] + counts["failed"]
                pct = counts["passed"] / total_runs * 100
                print(f"  {sid}: {counts['passed']}/{total_runs} passed ({pct:.0f}%)")

    if args.output:
        dispatcher_tier = config.dispatcher.worker_tier
        worker_cfg = getattr(config.workers, dispatcher_tier)
        with open(args.output, "w", encoding="utf-8") as f:
            json.dump({
                "model": worker_cfg.model,
                "provider": worker_cfg.provider,
                "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
                "repeat": args.repeat,
                "summary": {"passed": passed, "failed": failed, "errors": errors},
                "cases": results,
            }, f, indent=2)
        print(f"\nResults written to {args.output}")

    sys.exit(1 if (failed + errors) > 0 else 0)


if __name__ == "__main__":
    main()
```

- [ ] **Step 3: Run all unit tests**

```bash
python3 -m pytest tests/ -v
```

Expected: all unit tests pass. The eval harness does not have pytest tests — it is validated by running it against a live model.

- [ ] **Step 4: Commit**

```bash
git add evals/run_dispatch_evals.py evals/dispatch_golden_dataset.jsonl
git commit -m "feat: add dispatch eval harness and golden dataset"
```

---

## Summary of Files Changed

| File | Task | Change |
|------|------|--------|
| `core/registry.py` | 1 | Tuple expanded to 8 elements; new accessor methods; `_to_openai_schema` extracted |
| `core/dispatcher.py` | 2 | `DispatchDecision` extended; `_build_prompt`; direct tool parsing |
| `core/task.py` | 3 | `direct_tool` and `direct_args` fields |
| `core/orchestrator.py` | 3 | `_classify` 5-tuple; `_run_direct_tool`; direct-tool branch in `_run_task` |
| `app.py` | 4 | Pass `registry` to `Dispatcher` |
| `core/router.py` | 5 | Response rules injection in `route_message` |
| `tools/*.py` | 6 | Add `dispatch_to`, `dispatcher_direct`, `response_rules` to registrations |
| `evals/run_dispatch_evals.py` | 7 | New dispatch eval harness |
| `evals/dispatch_golden_dataset.jsonl` | 7 | New golden dataset |
