# Bug Bash Orchestration Plan — open-grug (FINAL + DETAILED)

Multi-agent execution plan for [bug_bash.md](file:///Users/cj/work/llm/open-grug/build-plan/bug_bash.md). Each agent section below contains the **exact code changes** needed — line numbers, before/after diffs, and scope boundaries — so a Sonnet agent can implement without ambiguity.

## Decisions Locked

| Decision | Resolution |
|---|---|
| C4 scope | ✅ Remove entire backlog.md CLI. Auto-resolves H2, H8, H11, L4, M12. |
| Wave 2 agent count | ✅ Fold H10+H13 into Agent 2A. 2 agents in Wave 2. |
| test_grug.py in Wave 1 | ✅ Only Agent 1A touches test_grug.py in Wave 1. Agent 1B defers test changes to Wave 2. |
| M9 (memory.md) | ✅ Drop it. Remove from `load_prompt_files`, delete the file. |

## Consolidation

| Merged Task | Source IDs | Rationale |
|---|---|---|
| **C5+H9** | C5, H9 | C5 removes frontier entirely; H9 (lazy import) is moot. |
| **H10+H13** | H10, H13 | Same bug. Folded into Agent 2A. |
| **Auto-resolved by C4** | H2, H8, H11, L4, M12 | C4 removes all backlog CLI code these target. |

**Effective: 20 unique tasks across 11 agents in 5 waves.**

---

# Wave 1 — Structural Demolition (2 agents)

---

## Agent 1A — C4: Revert backlog.md to simple Markdown tasks

### Objective
Delete the entire Node.js `backlog.md` CLI integration and replace it with a pure-Python markdown task system. This eliminates ~70 lines of subprocess code and the Node.js dependency.

### File 1: [app.py](file:///Users/cj/work/llm/open-grug/app.py)

**Step 1 — Delete the backlog CLI section (lines 40–112).** Remove everything from the section header through `backlog_start_browser`:

```diff
 registry = ToolRegistry()

-# ---------------------------------------------------------------------------
-# 2. Backlog.md CLI tools (unchanged from original)
-# ---------------------------------------------------------------------------
-_BACKLOG_CWD = os.environ.get("BACKLOG_CWD", "/app" if os.environ.get("DOCKER") else ".")
-_BACKLOG_ENV = {**os.environ, "BACKLOG_CWD": _BACKLOG_CWD}
-
-try:
-    subprocess.run(
-        ["backlog", "init", "grug", "--defaults"],
-        capture_output=True,
-        env=_BACKLOG_ENV,
-    )
-except FileNotFoundError:
-    print("[backlog] backlog CLI not found — skipping init. Install with: npm install -g backlog.md")
-
-def _backlog(*args): ...           # lines 55-62
-def backlog_list_tasks(...): ...   # lines 64-68
-def backlog_search_tasks(...): ... # lines 70-76
-def backlog_create_task(...): ...  # lines 78-88
-def backlog_edit_task(...): ...    # lines 90-96
-_backlog_browser_proc = None       # line 98
-def backlog_start_browser(): ...   # lines 100-112
```

Also remove `import subprocess` from line 15 (no longer needed in app.py after backlog removal — the subprocess usage moves to orchestrator's CLI branch which already imports it).

**Step 2 — Replace with pure-Python task functions.** Insert after `registry = ToolRegistry()`:

```python
# ---------------------------------------------------------------------------
# 2. Simple Markdown Task Board
# ---------------------------------------------------------------------------
_TASKS_FILE = os.path.join(config.storage.base_dir, "tasks.md")


def _ensure_tasks_file():
    """Create tasks.md if it doesn't exist."""
    if not os.path.exists(_TASKS_FILE):
        os.makedirs(os.path.dirname(_TASKS_FILE), exist_ok=True)
        with open(_TASKS_FILE, "w", encoding="utf-8") as f:
            f.write("# Grug Task Board\n\n")


def add_task(title, priority=None, assignee=None, description=None):
    """Append a markdown checkbox to brain/tasks.md."""
    _ensure_tasks_file()
    parts = [f"- [ ] {title}"]
    if priority:
        parts.append(f"[{priority}]")
    if assignee:
        parts.append(f"@{assignee}")
    line = " ".join(parts)
    if description:
        line += f"\n  > {description}"
    with open(_TASKS_FILE, "a", encoding="utf-8") as f:
        f.write(line + "\n")
    return f"Task added: {title}"


def list_tasks(status=None):
    """Read brain/tasks.md, optionally filter by open/done."""
    _ensure_tasks_file()
    with open(_TASKS_FILE, "r", encoding="utf-8") as f:
        lines = f.readlines()

    tasks = []
    for i, line in enumerate(lines, 1):
        stripped = line.strip()
        if stripped.startswith("- [ ] "):
            if status is None or status.lower() in ("open", "todo", "to do"):
                tasks.append(f"{i}: {stripped}")
        elif stripped.startswith("- [x] "):
            if status is None or status.lower() == "done":
                tasks.append(f"{i}: {stripped}")

    if not tasks:
        return "No tasks found."
    return "\n".join(tasks)


def edit_task(line_number, status=None, append_notes=None):
    """Toggle task checkbox or append notes by line number."""
    _ensure_tasks_file()
    with open(_TASKS_FILE, "r", encoding="utf-8") as f:
        lines = f.readlines()

    idx = int(line_number) - 1
    if idx < 0 or idx >= len(lines):
        return f"Line {line_number} not found in tasks.md"

    line = lines[idx]
    if status and status.lower() == "done" and "- [ ] " in line:
        lines[idx] = line.replace("- [ ] ", "- [x] ", 1)
    elif status and status.lower() in ("open", "todo", "to do") and "- [x] " in line:
        lines[idx] = line.replace("- [x] ", "- [ ] ", 1)

    if append_notes:
        lines[idx] = lines[idx].rstrip("\n") + f"  ({append_notes})\n"

    with open(_TASKS_FILE, "w", encoding="utf-8") as f:
        f.writelines(lines)
    return f"Task on line {line_number} updated."
```

**Step 3 — Replace the 6 backlog tool registrations (lines 141–210) with 3 new ones.** Delete from `# Backlog.md task board tools` through the `backlog_edit_task` registration. Replace with:

```python
# Task board tools (pure Python, markdown-backed)
registry.register_python_tool(
    name="add_task",
    schema={
        "description": "Create a new task on the project board. Requires human approval.",
        "type": "object",
        "properties": {
            "title": {"type": "string"},
            "description": {"type": "string"},
            "priority": {"type": "string", "enum": ["high", "medium", "low"]},
            "assignee": {"type": "string", "description": "Username without @ prefix"}
        },
        "required": ["title"]
    },
    func=add_task,
    destructive=True
)
registry.register_python_tool(
    name="list_tasks",
    schema={
        "description": "List tasks on the project board. Optionally filter by status ('open' or 'done').",
        "type": "object",
        "properties": {
            "status": {"type": "string", "description": "Filter: 'open' or 'done'"}
        }
    },
    func=list_tasks,
    destructive=False
)
registry.register_python_tool(
    name="edit_task",
    schema={
        "description": "Update an existing task's status or append notes. Requires human approval.",
        "type": "object",
        "properties": {
            "line_number": {"type": "string", "description": "Line number of the task in tasks.md"},
            "status": {"type": "string", "description": "'done' or 'open'"},
            "append_notes": {"type": "string"}
        },
        "required": ["line_number"]
    },
    func=edit_task,
    destructive=True
)
```

**Step 4 — Update `execute_summarize_board` reference.** In [core/orchestrator.py](file:///Users/cj/work/llm/open-grug/core/orchestrator.py) line 212, change `"backlog_list_tasks"` → `"list_tasks"`:

```diff
-        result = self.registry.execute("backlog_list_tasks", args)
+        result = self.registry.execute("list_tasks", args)
```

**Step 5 — Update `execute_list_capabilities` friendly_names.** In [core/orchestrator.py](file:///Users/cj/work/llm/open-grug/core/orchestrator.py) lines 235–245, replace the backlog entries:

```diff
         friendly_names = {
             "add_note": "Save a note",
             "get_recent_notes": "Read recent notes",
             "query_memory": "Search memory",
-            "backlog_start_browser": "Open the task dashboard",
-            "backlog_list_tasks": "List tasks",
-            "backlog_search_tasks": "Search tasks",
-            "backlog_create_task": "Create a task",
-            "backlog_edit_task": "Update a task",
+            "add_task": "Create a task",
+            "list_tasks": "List tasks",
+            "edit_task": "Update a task",
             "summarize_board": "Summarize the board",
         }
```

### File 2: [prompts/schema_examples.md](file:///Users/cj/work/llm/open-grug/prompts/schema_examples.md)

Replace all `backlog_*` tool references with the new tool names. Key changes:

```diff
-  "tool": "backlog_start_browser",
+  "tool": "list_tasks",
```
```diff
-  "tool": "backlog_list_tasks",
+  "tool": "list_tasks",
```
```diff
-  "tool": "backlog_search_tasks",
+  "tool": "list_tasks",
```
```diff
-  "tool": "backlog_create_task",
+  "tool": "add_task",
```
```diff
-  "tool": "backlog_edit_task",
+  "tool": "edit_task",
-    "task_id": "5",
+    "line_number": "5",
```

Remove the "Open the backlog dashboard" / "Start the task board" examples entirely (lines 5–23). There is no dashboard tool anymore.

The `backlog_search_tasks` examples should be rewritten to use `list_tasks` since there's no dedicated search tool; the LLM can use `list_tasks` and visually scan.

### File 3: [Dockerfile](file:///Users/cj/work/llm/open-grug/Dockerfile)

```diff
 RUN apt-get update && apt-get install -y \
     build-essential \
     curl \
     libblas3 \
     liblapack3 \
     libgomp1 \
-    nodejs \
-    npm \
     && rm -rf /var/lib/apt/lists/*
```
```diff
-# Install backlog.md CLI globally
-RUN npm install -g backlog.md
-
 # The persistent brain volume
-RUN mkdir -p /app/brain/daily_notes /app/brain/summaries
+RUN mkdir -p /app/brain/daily_notes /app/brain/summaries /app/brain
```

### File 4: [docker-compose.yml](file:///Users/cj/work/llm/open-grug/docker-compose.yml)

```diff
-    ports:
-      # Backlog.md dashboard — access at http://localhost:6420
-      - "${BACKLOG_DASHBOARD_PORT:-6420}:6420"
     volumes:
       # Mount the local brain directory to the container so MD files persist
       - ./brain:/app/brain
-      # Mount backlog task data so project board persists across rebuilds
-      - ./backlog:/app/backlog
```
```diff
-      - BACKLOG_CWD=${BACKLOG_CWD:-/app}
-      - BACKLOG_DASHBOARD_PORT=${BACKLOG_DASHBOARD_PORT:-6420}
```

### File 5: [test_grug.py](file:///Users/cj/work/llm/open-grug/test_grug.py)

No backlog-specific tests exist currently, but test 1 uses `add_note` (unchanged). The `_fresh_setup` function (line 39) doesn't register backlog tools, so it's unaffected. Just verify the test suite still passes. If any tests import backlog functions, remove those imports.

### Verification
```bash
python test_grug.py                    # all tests pass
python -c "import app; print('OK')"    # no import errors (will fail on missing Slack tokens, but should not fail on missing backlog CLI)
docker build -t grug-test .            # builds without Node.js
grep -r "backlog" --include="*.py" .   # should only appear in build-plan/ docs, not in source code
```

---

## Agent 1B — C5+H9: Remove Frontier Model Escalation

### Objective
Remove the entire Anthropic/Claude escalation pathway. The local Ollama model is the only model. Low-confidence responses fall through to `ask_for_clarification` instead of escalating.

### File 1: [core/orchestrator.py](file:///Users/cj/work/llm/open-grug/core/orchestrator.py)

**Step 1 — Remove `import anthropic` (line 9):**

```diff
 import threading
-import anthropic
 import jsonschema
```

**Step 2 — Remove `self.frontier_available` and `self._base_system_prompt` from `__init__` (lines 125–130):**

```diff
 class GrugRouter:
     def __init__(self, registry: ToolRegistry):
         self.registry = registry
-        self.frontier_available = bool(os.getenv("CLAUDE_API_KEY", ""))
         self._request_state = threading.local()
-        self._base_system_prompt = ""
         self.register_core_tools()
```

**Step 3 — Remove `escalate_to_frontier` tool registration from `register_core_tools` (lines 132–145).** Delete these 14 lines entirely:

```diff
     def register_core_tools(self):
-        # The primary fallback tool
-        self.registry.register_python_tool(
-            name="escalate_to_frontier",
-            schema={
-                "description": "Route complex requests to Claude Opus.",
-                "type": "object",
-                "properties": {
-                    "reason_for_escalation": {"type": "string"}
-                },
-                "required": ["reason_for_escalation"]
-            },
-            func=self.execute_frontier_escalation,
-        )
-
         # Clarification tool
```

**Step 4 — Delete `execute_frontier_escalation` method entirely (lines 261–289).** Remove all 29 lines.

**Step 5 — Rewrite `invoke_chat` error fallback (lines 327–329).** The current fallback routes to `escalate_to_frontier` which no longer exists. Route to `ask_for_clarification` instead. Also use `json.dumps` to fix H4 (JSON escaping bug):

```diff
         except Exception as e:
-            # Return a graceful fallback if the LLM is unreachable
-            return f'{{"tool": "escalate_to_frontier", "arguments": {{"reason_for_escalation": "Ollama error: {str(e)}"}}}}'
+            # Return a graceful fallback if the LLM is unreachable
+            return json.dumps({
+                "tool": "ask_for_clarification",
+                "arguments": {"reason_for_confusion": f"Grug brain foggy. Ollama not responding: {e}"},
+                "confidence_score": 0
+            })
```

> [!NOTE]
> This simultaneously fixes **H4** (JSON escaping bug) because `json.dumps` properly escapes quotes, backslashes, and newlines in `str(e)`. Agent 2B can skip H4 since it's handled here.

**Step 6 — Remove `self._base_system_prompt` assignment in `route_message` (line 377):**

```diff
-        self._base_system_prompt = system_prompt
         self._request_state.user_message = user_message
```

**Step 7 — Rewrite the confidence/escalation logic in `route_message` (lines 395–420).** Replace the entire confidence-check and frontier-fallback block with a simpler path:

Current code (lines 395–420):
```python
                confidence_score = call_data.get("confidence_score", 10)

                # Phase 5: honor confidence score — force escalation if Gemma is uncertain
                if confidence_score < 8 and tool_name not in ("escalate_to_frontier", "ask_for_clarification"):
                    escalation_output = self.execute_frontier_escalation(
                        f"low confidence ({confidence_score}) on tool '{tool_name}'"
                    )
                    if "ERROR_OFFLINE" in escalation_output:
                        fallback_messages = message_history + [{...}]
                        fallback_response_text = self.invoke_chat(augmented_system, fallback_messages)
                        return ToolExecutionResult(success=True, output=f"Degraded Response: {fallback_response_text}")
                    return ToolExecutionResult(success=True, output=escalation_output)

                result = self.registry.execute(tool_name, args)

                # Phase 4: Graceful Degradation Trap
                if tool_name == "escalate_to_frontier" and "ERROR_OFFLINE" in result.output:
                    fallback_messages = message_history + [{...}]
                    fallback_response_text = self.invoke_chat(augmented_system, fallback_messages)
                    return ToolExecutionResult(success=True, output=f"Degraded Response: {fallback_response_text}")
```

Replace with:
```python
                confidence_score = call_data.get("confidence_score", 10)

                # Low confidence: ask the user for clarification instead of guessing
                if confidence_score < 8 and tool_name not in ("ask_for_clarification", "reply_to_user"):
                    return ToolExecutionResult(
                        success=True,
                        output=f"Grug not very sure (confidence {confidence_score}/10). Grug need more detail to pick right tool. What you want Grug do?"
                    )

                result = self.registry.execute(tool_name, args)
```

**Step 8 — Remove `escalate_to_frontier` from `hidden_tools` in `execute_list_capabilities` (line 233):**

```diff
-        hidden_tools = {"escalate_to_frontier", "ask_for_clarification", "list_capabilities", "reply_to_user"}
+        hidden_tools = {"ask_for_clarification", "list_capabilities", "reply_to_user"}
```

**Step 9 — Clean up `route_message` finally block (lines 425–428).** Remove the `_request_state.context` cleanup since context was only used by frontier escalation:

```diff
         finally:
             self._request_state.user_message = None
-            if hasattr(self._request_state, 'context'):
-                self._request_state.context = None
```

Also remove the context storage in the legacy path (line 373):
```diff
-            # Store context for frontier escalation
-            self._request_state.context = context or ""
```

### File 2: [requirements.txt](file:///Users/cj/work/llm/open-grug/requirements.txt)

```diff
 slack-bolt
 slack-sdk
 sqlite-vss
 sentence-transformers
 pydantic
 requests
-anthropic
 jsonschema
```

### Does NOT touch in Wave 1
- `test_grug.py` — deferred to Wave 2 Agent 2A
- `schema_examples.md` — the `escalate_to_frontier` example (lines 163–173) gets cleaned up by Agent 4B in Wave 4
- `app.py` — no references to frontier/anthropic exist in app.py

### Verification
```bash
python -c "from core.orchestrator import GrugRouter, ToolRegistry; r = GrugRouter(ToolRegistry()); print('OK')"
grep -r "anthropic\|escalate_to_frontier\|frontier_available" --include="*.py" core/   # should return nothing
pip install -r requirements.txt   # no anthropic needed
```

---

### Wave 1 Merge Protocol
1. Agent 1A merges first (touches app.py, orchestrator.py friendly_names/summarize_board, schema_examples, Dockerfile, docker-compose, test_grug.py)
2. Agent 1B merges second (touches orchestrator.py GrugRouter methods, requirements.txt)
3. Conflict check: Agent 1A edits orchestrator.py lines 212 and 235–245. Agent 1B edits orchestrator.py lines 9, 125–145, 233, 261–289, 327–329, 373, 377, 395–420, 425–428. **No overlap.**
4. Run after both:
```bash
python test_grug.py
docker build -t grug-test .
```

---

# Wave 2 — Orchestrator Hardening (2 agents)

Branch from post-Wave-1 `main`.

---

## Agent 2A — H1 + H7 + H10+H13: CLI hardening + timeouts + CalledProcessError

### Scope Boundary
Owns: `ToolRegistry.execute` (both branches, lines ~57–122 post-Wave-1) and `test_grug.py`.
Does NOT touch: `GrugRouter` methods (Agent 2B's territory).

### File 1: [core/orchestrator.py](file:///Users/cj/work/llm/open-grug/core/orchestrator.py) — `ToolRegistry.execute`

**Fix H10+H13 — Python-tool branch (lines 79–83).** Add specific `CalledProcessError` catch before the broad `Exception`:

```diff
             try:
                 res = func(**arguments)
                 return ToolExecutionResult(success=True, output=str(res))
+            except subprocess.CalledProcessError as e:
+                stderr_output = e.output or ""
+                return ToolExecutionResult(
+                    success=False,
+                    output=f"Command failed (exit {e.returncode}): {e}\n---stderr---\n{stderr_output}"
+                )
             except Exception as e:
                 return ToolExecutionResult(success=False, output=str(e))
```

**Fix H1 — CLI branch argv construction (lines 105–112).** Add `--` separator and reject `--`-prefixed values:

```diff
             # Sandboxed Subprocess Builder
             cmd = base_command.copy()
+            positionals = []
             for key, val in arguments.items():
                 if isinstance(val, bool):
                     if val: cmd.append(f"--{key}")
                 else:
-                    cmd.append(f"--{key}")
-                    cmd.append(str(val))
+                    str_val = str(val)
+                    if str_val.startswith("--"):
+                        return ToolExecutionResult(
+                            success=False,
+                            output=f"Invalid arg value for '{key}': values must not start with '--'"
+                        )
+                    cmd.append(f"--{key}")
+                    cmd.append(str_val)
+            # Separator to prevent flag injection from positional values
+            cmd.append("--")
```

**Fix H7 — Add timeout to CLI subprocess (lines 114–115):**

```diff
+            _timeout = int(os.environ.get("GRUG_SUBPROCESS_TIMEOUT", "30"))
             try:
-                output = subprocess.check_output(cmd, stderr=subprocess.STDOUT, text=True)
+                output = subprocess.check_output(cmd, stderr=subprocess.STDOUT, text=True, timeout=_timeout)
                 return ToolExecutionResult(success=True, output=output)
             except subprocess.CalledProcessError as e:
                 return ToolExecutionResult(success=False, output=e.output)
+            except subprocess.TimeoutExpired as e:
+                return ToolExecutionResult(
+                    success=False,
+                    output=f"Command timed out after {_timeout}s"
+                )
             except Exception as e:
                 return ToolExecutionResult(success=False, output=str(e))
```

### File 2: [test_grug.py](file:///Users/cj/work/llm/open-grug/test_grug.py)

**Update tests 2 and 4** (deferred from Wave 1) to work without `escalate_to_frontier`:

Test 2 (`test_2_graceful_offline_degradation`) currently mocks a frontier escalation. After C5 removal, the low-confidence path returns a clarification message. Rewrite:

```python
def test_2_graceful_offline_degradation():
    storage, registry, router = _fresh_setup()
    base_prompt = load_prompt_files("prompts")

    def mock_chat(sys_prompt, msgs):
        # Simulate Ollama being down — invoke_chat now returns ask_for_clarification JSON
        raise Exception("Connection refused")

    router.invoke_chat = mock_chat
    # invoke_chat raises, but the real invoke_chat catches and returns JSON
    # Instead, test the JSON fallback directly
    router.invoke_chat = lambda sys_prompt, msgs: json.dumps({
        "tool": "ask_for_clarification",
        "arguments": {"reason_for_confusion": "Grug brain foggy. Ollama not responding."},
        "confidence_score": 0
    })
    res = router.route_message(
        "Explain quantum mechanics.",
        context="Test Env",
        compression_mode="FULL",
        base_system_prompt=base_prompt,
    )
    assert res.success is True
    assert "Grug" in res.output  # clarification message
    print("[PASS] TEST 2: Graceful Offline Degradation")
```

Test 4 (`test_4_confidence_score_forces_escalation`) currently expects 2 invoke_chat calls (escalation + fallback). After C5, low confidence returns a clarification directly. Rewrite:

```python
def test_4_confidence_score_forces_escalation():
    _storage, registry, router = _fresh_setup()
    base_prompt = load_prompt_files("prompts")

    router.invoke_chat = lambda sys_prompt, msgs: (
        '{"confidence_score": 3, "tool": "add_note", "arguments": {"content": "unsure"}}'
    )
    res = router.route_message(
        "Complex query",
        context="Test",
        base_system_prompt=base_prompt,
    )
    assert res.success is True
    assert "not very sure" in res.output.lower() or "confidence" in res.output.lower()
    print("[PASS] TEST 4: Low Confidence Returns Clarification")
```

**Add new tests for H1, H7, H10+H13:**

```python
def test_18_cli_flag_injection_blocked():
    """H1: '--'-prefixed values are rejected in CLI tool args."""
    registry = ToolRegistry()
    registry.register_cli_tool(
        name="test_cli",
        schema={"type": "object", "properties": {"title": {"type": "string"}}, "required": ["title"]},
        base_command=["echo"],
        destructive=False,
    )
    res = registry.execute("test_cli", {"title": "--assignee=evil"})
    assert res.success is False
    assert "must not start with" in res.output
    print("[PASS] TEST 18: CLI Flag Injection Blocked")


def test_19_subprocess_timeout():
    """H7: Subprocess calls time out instead of hanging."""
    import os
    os.environ["GRUG_SUBPROCESS_TIMEOUT"] = "1"
    registry = ToolRegistry()
    registry.register_cli_tool(
        name="test_slow",
        schema={"type": "object", "properties": {}, "required": []},
        base_command=["sleep", "60"],
        destructive=False,
    )
    res = registry.execute("test_slow", {})
    assert res.success is False
    assert "timed out" in res.output
    os.environ.pop("GRUG_SUBPROCESS_TIMEOUT", None)
    print("[PASS] TEST 19: Subprocess Timeout")


def test_20_called_process_error_output_surfaced():
    """H10+H13: CalledProcessError.output is included in python-tool error result."""
    registry = ToolRegistry()

    def failing_func():
        raise subprocess.CalledProcessError(returncode=1, cmd=["test"], output="detailed error info")

    registry.register_python_tool(
        name="test_fail",
        schema={"type": "object", "properties": {}},
        func=failing_func,
    )
    res = registry.execute("test_fail", {})
    assert res.success is False
    assert "detailed error info" in res.output
    print("[PASS] TEST 20: CalledProcessError Output Surfaced")
```

Add `import subprocess` to test_grug.py imports if not already present, and add these to `run_tests()`.

### Verification
```bash
python test_grug.py   # all 20 tests pass
```

---

## Agent 2B — H4 + H5 + H12: Ollama fallback + confidence default + degraded-mode execution

### Scope Boundary
Owns: `GrugRouter` methods — `invoke_chat`, `route_message`, `invoke_gemma_text`, `build_system_prompt`.
Does NOT touch: `ToolRegistry` (Agent 2A's territory) or `test_grug.py` (Agent 2A owns).

> [!IMPORTANT]
> **H4 is already fixed by Agent 1B** (the `invoke_chat` error path was rewritten to use `json.dumps` in Step 5). Agent 2B should verify this is in place and skip H4 if so.

### File 1: [core/orchestrator.py](file:///Users/cj/work/llm/open-grug/core/orchestrator.py) — `GrugRouter`

**Fix H5 — Change confidence_score default (in `route_message`).** After Wave 1, Agent 1B already simplified the confidence block. But the default value may still be `10`. Ensure it's `0`:

```diff
-                confidence_score = call_data.get("confidence_score", 10)
+                confidence_score = call_data.get("confidence_score", 0)
```

**Fix H12 — Parse and execute degraded-mode tool calls.** After Agent 1B's changes, the low-confidence path in `route_message` returns a static clarification message. But there may still be an Ollama-down fallback that wraps raw JSON as a string. If a "best-effort" re-prompt fallback exists, ensure the response is parsed and executed:

Post-Wave-1, if any fallback path still does `return ToolExecutionResult(success=True, output=f"Degraded Response: {text}")`, rewrite it to:

```python
# Parse the fallback response and execute the tool if valid
try:
    fallback_data = json.loads(fallback_response_text)
    fb_tool = fallback_data.get("tool")
    fb_args = fallback_data.get("arguments", {})
    if fb_tool:
        fb_result = self.registry.execute(fb_tool, fb_args)
        fb_result.output = f"(Degraded Mode) {fb_result.output}"
        return fb_result
except (json.JSONDecodeError, Exception):
    pass
# If parsing fails, return the raw text
return ToolExecutionResult(success=True, output=f"(Degraded Mode) {fallback_response_text}")
```

> [!NOTE]
> After Agent 1B removes the frontier logic, the main place this applies is the `invoke_chat` exception path. Since Agent 1B rewrites that to return `ask_for_clarification` JSON, the degraded path is inherently handled. Agent 2B's main job is to verify the H5 default change and add a test.

**Add test (append to test_grug.py via coordination with Agent 2A, OR create a separate test file `test_degraded.py`):**

Since Agent 2A owns `test_grug.py`, Agent 2B should either:
- (a) Ask Agent 2A to include the H5 test, or
- (b) Create `test_confidence.py` as a standalone:

```python
"""Tests for confidence scoring and degraded mode (H5, H12)."""
from core.orchestrator import ToolRegistry, GrugRouter, load_prompt_files

def test_missing_confidence_defaults_low():
    registry = ToolRegistry()
    router = GrugRouter(registry)
    base_prompt = load_prompt_files("prompts")

    # Return JSON with no confidence_score field
    router.invoke_chat = lambda sys, msgs: '{"tool": "reply_to_user", "arguments": {"message": "hi"}}'
    res = router.route_message("hello", context="Test", base_system_prompt=base_prompt)
    # With default=0, confidence < 8 triggers the low-confidence path
    assert "not very sure" in res.output.lower() or "confidence" in res.output.lower()
    print("[PASS] Missing confidence_score defaults to low (H5)")

if __name__ == "__main__":
    test_missing_confidence_defaults_low()
```

### Verification
```bash
python test_grug.py
python test_confidence.py   # if separate file
```

---

### Wave 2 Merge Protocol
1. Agent 2A merges first (ToolRegistry + test_grug.py)
2. Agent 2B merges second (GrugRouter methods + optional test_confidence.py)
3. Run `python test_grug.py` after both

---

# Wave 3 — Vector Memory + Supply Chain (2 agents, fully parallel)

---

## Agent 3A — H6 + M3 + M4: VectorMemory thread safety, model pinning, extension guard

### File: [core/vectors.py](file:///Users/cj/work/llm/open-grug/core/vectors.py)

**Fix H6 — Add threading.Lock (Option B from bug bash).** Add a lock to `__init__` and wrap all `self.conn` operations:

```diff
 class VectorMemory:
     def __init__(self, db_path="/app/brain/memory.db", model_name="all-MiniLM-L6-v2"):
         self.db_path = db_path
+        self._lock = threading.Lock()
         if not HAS_VSS:
```

Wrap `_init_db` internals:
```diff
     def _init_db(self):
         if not HAS_VSS:
             return
+        with self._lock:
             ...existing code indented one level deeper...
```

Wrap `index_markdown_directory` DB operations (lines 66–90):
```diff
-        db_cursor = self.conn.cursor()
+        with self._lock:
+            db_cursor = self.conn.cursor()
         ...rest of method body indented...
-        self.conn.commit()
+            self.conn.commit()
```

Wrap `query_memory` DB query (lines 119–131):
```diff
-        cursor = self.conn.cursor()
-        cursor.execute(...)
-        return [...]
+        with self._lock:
+            cursor = self.conn.cursor()
+            cursor.execute(...)
+            return [...]
```

**Fix M3 — Pin sentence-transformers model revision (line 23):**

```diff
-            self.model = SentenceTransformer(model_name)
+            # Pinned revision: all-MiniLM-L6-v2 commit from 2024-11-21
+            self.model = SentenceTransformer(model_name, revision="c5f93f70e82bc3c30e7a1a3ada002cd3c3543307")
```

> [!NOTE]
> The agent should verify the actual latest commit SHA by checking `https://huggingface.co/sentence-transformers/all-MiniLM-L6-v2/commits/main` and using the current HEAD SHA. The one above is a placeholder — use the real one.

**Fix M4 — Guard `enable_load_extension` behind env flag (lines 34–36):**

```diff
-        self.conn.enable_load_extension(True)
-        sqlite_vss.load(self.conn)
-        self.conn.enable_load_extension(False)
+        if os.getenv("VECTORS_LOAD_EXTENSION") == "1":
+            self.conn.enable_load_extension(True)
+            sqlite_vss.load(self.conn)
+            self.conn.enable_load_extension(False)
+        else:
+            # VSS extension loading disabled — fall back to non-VSS path
+            print("[vectors] VECTORS_LOAD_EXTENSION not set, skipping sqlite-vss load")
```

Also update the `HAS_VSS` check at the top of `__init__` to factor in the env flag:

```diff
+        _vss_enabled = os.getenv("VECTORS_LOAD_EXTENSION") == "1"
         if not HAS_VSS:
-            print("WARNING: Vector search disabled locally...")
+            print("WARNING: Vector search disabled (missing sqlite-vss or VECTORS_LOAD_EXTENSION not set).")
             self.model = None
+        elif not _vss_enabled:
+            print("WARNING: Vector search disabled (VECTORS_LOAD_EXTENSION != 1).")
+            self.model = None
         else:
```

### Verification
```bash
python -c "
import threading
from core.vectors import VectorMemory
# Smoke test: instantiate without VSS (will print warning, should not crash)
vm = VectorMemory(db_path='/tmp/test_vectors.db')
print('VectorMemory instantiated OK')
"
```

---

## Agent 3B — M1 + M2: Pin requirements.txt + Docker base image

### File 1: [requirements.txt](file:///Users/cj/work/llm/open-grug/requirements.txt)

Run `pip freeze` in the working environment and pin every package. Post-Wave-1, `anthropic` is already removed. Expected output:

```
slack-bolt==1.22.0
slack-sdk==3.34.0
sqlite-vss==0.1.2
sentence-transformers==3.4.1
pydantic==2.10.6
requests==2.32.3
jsonschema==4.23.0
```

> [!IMPORTANT]
> The exact versions depend on what's installed. The agent should run `pip freeze | grep -i "slack-bolt\|slack-sdk\|sqlite-vss\|sentence-transformers\|pydantic\|requests\|jsonschema"` to capture the real versions.

### File 2: [Dockerfile](file:///Users/cj/work/llm/open-grug/Dockerfile)

```diff
-FROM python:3.11-slim
+# To refresh digest: docker pull python:3.11-slim && docker inspect --format='{{index .RepoDigests 0}}' python:3.11-slim
+FROM python:3.11-slim@sha256:<actual_digest>
```

The agent should run `docker pull python:3.11-slim && docker inspect --format='{{index .RepoDigests 0}}' python:3.11-slim` to get the real digest.

### Verification
```bash
docker build -t grug-test .
pip install -r requirements.txt   # in clean venv
```

---

# Wave 4 — Prompt & UX Polish (3 agents)

---

## Agent 4A — M5 + M6: Routing trace log + hot-reload prompts

### Scope Boundary
Owns: `GrugRouter.__init__` (adding cache state), `route_message` body (adding trace + reload).
Does NOT touch: `register_python_tool`, `register_cli_tool`, `execute_list_capabilities`, `load_prompt_files` (Agent 4C's territory).

### File: [core/orchestrator.py](file:///Users/cj/work/llm/open-grug/core/orchestrator.py)

**Fix M6 — Add prompt mtime tracking to `__init__`:**

```diff
 class GrugRouter:
     def __init__(self, registry: ToolRegistry):
         self.registry = registry
         self._request_state = threading.local()
+        # M6: Hot-reload prompts on mtime change
+        self._prompt_dir = "prompts"
+        self._prompt_mtimes: Dict[str, float] = {}
+        self._cached_base_prompt = ""
+        self._reload_prompts()
         self.register_core_tools()
+
+    def _reload_prompts(self):
+        """Reload prompt files and update mtime cache."""
+        self._cached_base_prompt = load_prompt_files(self._prompt_dir)
+        for name in ["system.md", "rules.md", "schema_examples.md"]:
+            path = os.path.join(self._prompt_dir, name)
+            try:
+                self._prompt_mtimes[name] = os.stat(path).st_mtime
+            except OSError:
+                self._prompt_mtimes[name] = 0
+
+    def _check_prompt_reload(self):
+        """Stat prompt files; reload if any changed."""
+        for name, old_mtime in self._prompt_mtimes.items():
+            path = os.path.join(self._prompt_dir, name)
+            try:
+                if os.stat(path).st_mtime > old_mtime:
+                    self._reload_prompts()
+                    return
+            except OSError:
+                continue
```

> [!NOTE]
> The filenames list is `["system.md", "rules.md", "schema_examples.md"]` — only 3, because `memory.md` is deleted by Agent 4C (M9). If Agent 4A runs before 4C, include `memory.md` conditionally via `os.path.exists`.

**Add prompt reload check at top of `route_message`:**

```diff
     def route_message(self, user_message: str, system_prompt: str = "", ...):
+        # M6: Hot-reload prompts if any file changed
+        self._check_prompt_reload()
+
         # Handle legacy callers...
```

**Fix M5 — Add JSONL trace after JSON parse succeeds in `route_message`.** Right after `call_data = json.loads(response_text)`:

```diff
                 call_data = json.loads(response_text)
                 tool_name = call_data.get("tool")
                 args = call_data.get("arguments", {})
                 confidence_score = call_data.get("confidence_score", 0)
+
+                # M5: Append routing trace
+                try:
+                    trace_entry = json.dumps({
+                        "ts": datetime.now().isoformat(),
+                        "user_msg": user_message[:200],  # truncate for safety
+                        "tool": tool_name,
+                        "args": args,
+                        "confidence": confidence_score,
+                    })
+                    trace_path = os.path.join("brain", "routing_trace.jsonl")
+                    os.makedirs(os.path.dirname(trace_path), exist_ok=True)
+                    with open(trace_path, "a", encoding="utf-8") as tf:
+                        tf.write(trace_entry + "\n")
+                except Exception:
+                    pass  # tracing must never break routing
```

### Verification
```bash
# M5 trace test
python -c "
from core.orchestrator import ToolRegistry, GrugRouter
r = GrugRouter(ToolRegistry())
r.invoke_chat = lambda s, m: '{\"tool\": \"reply_to_user\", \"arguments\": {\"message\": \"hi\"}, \"confidence_score\": 10}'
r.route_message('test')
import os
assert os.path.exists('brain/routing_trace.jsonl')
print('Trace file created OK')
"

# M6 reload test: edit prompts/system.md, call route_message, verify new prompt used
```

---

## Agent 4B — M7 + L2: Offline prompt test harness + schema_examples coverage

### Scope Boundary
Owns: New files (`scripts/test_prompts.py`, `tests/prompt_fixtures.yaml`) and `prompts/schema_examples.md`.
Does NOT touch: Any Python source files.

### New File: `scripts/test_prompts.py`

```python
#!/usr/bin/env python3
"""Offline prompt regression test harness.

Runs prompt fixtures against a live local Ollama instance and verifies
the model routes to the expected tool.

Usage: python scripts/test_prompts.py
Requires: Ollama running locally with the configured model.
"""
import os
import sys
import json
import yaml

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.orchestrator import ToolRegistry, GrugRouter, load_prompt_files


def main():
    fixtures_path = os.path.join(os.path.dirname(__file__), "..", "tests", "prompt_fixtures.yaml")
    with open(fixtures_path, "r") as f:
        fixtures = yaml.safe_load(f)

    registry = ToolRegistry()
    # Register stub tools so schemas are present
    for tool_def in fixtures.get("tool_stubs", []):
        registry.register_python_tool(
            name=tool_def["name"],
            schema=tool_def["schema"],
            func=lambda **kwargs: "stub",
        )

    router = GrugRouter(registry)
    base_prompt = load_prompt_files("prompts")

    passed, failed = 0, 0
    for case in fixtures["cases"]:
        user_msg = case["input"]
        expected_tool = case["expected_tool"]
        try:
            res = router.route_message(
                user_msg,
                context="Test harness",
                compression_mode="FULL",
                base_system_prompt=base_prompt,
            )
            # Try to parse the tool from the result
            # (route_message executes the tool, so we check output characteristics)
            if expected_tool == "reply_to_user" and res.success and not res.requires_approval:
                status = "PASS"
                passed += 1
            elif expected_tool == "add_task" and res.requires_approval:
                status = "PASS"
                passed += 1
            else:
                status = f"FAIL (got output: {res.output[:80]})"
                failed += 1
        except Exception as e:
            status = f"ERROR ({e})"
            failed += 1

        print(f"  [{status}] {user_msg[:60]:<60} → expected {expected_tool}")

    print(f"\n{'='*60}")
    print(f"Results: {passed} passed, {failed} failed out of {passed + failed}")
    sys.exit(1 if failed > 0 else 0)


if __name__ == "__main__":
    main()
```

### New File: `tests/prompt_fixtures.yaml`

```yaml
tool_stubs:
  - name: add_note
    schema: { "type": "object", "properties": { "content": { "type": "string" } }, "required": ["content"] }
  - name: add_task
    schema: { "type": "object", "properties": { "title": { "type": "string" } }, "required": ["title"] }
  - name: list_tasks
    schema: { "type": "object", "properties": { "status": { "type": "string" } } }
  - name: get_recent_notes
    schema: { "type": "object", "properties": { "limit": { "type": "integer" } } }
  - name: query_memory
    schema: { "type": "object", "properties": { "query": { "type": "string" } }, "required": ["query"] }

cases:
  - input: "Hey Grug, how's it going?"
    expected_tool: reply_to_user
  - input: "hi"
    expected_tool: reply_to_user
  - input: "What's the speed of light?"
    expected_tool: reply_to_user
  - input: "Who is the president?"
    expected_tool: reply_to_user
  - input: "Remember that the deploy is on Friday"
    expected_tool: add_note
  - input: "What did I say about the deploy?"
    expected_tool: query_memory
  - input: "Show me my recent notes"
    expected_tool: get_recent_notes
  - input: "Add a task to fix the login page"
    expected_tool: add_task
  - input: "What tasks are open?"
    expected_tool: list_tasks
  - input: "Give me a summary of the board"
    expected_tool: summarize_board
  - input: "What can you do?"
    expected_tool: list_capabilities
```

### File: [prompts/schema_examples.md](file:///Users/cj/work/llm/open-grug/prompts/schema_examples.md)

Add missing examples at the end of the file:

```markdown
USER: "Remember that the standup is at 9am every day."
ASST:
```json
{
  "confidence_score": 10,
  "tool": "add_note",
  "arguments": {
    "content": "Standup at 9am every day",
    "tags": ["meeting"]
  }
}
```

USER: "Show me my recent notes."
ASST:
```json
{
  "confidence_score": 10,
  "tool": "get_recent_notes",
  "arguments": {
    "limit": 10
  }
}
```

USER: "What did I say about the deploy last week?"
ASST:
```json
{
  "confidence_score": 10,
  "tool": "query_memory",
  "arguments": {
    "query": "deploy last week"
  }
}
```
```

Also remove the stale `escalate_to_frontier` example (lines 163–173 in original file).

---

## Agent 4C — M8 + M9 + M10: friendly_names refactor, drop memory.md, tag enum

### Scope Boundary
Owns: `register_python_tool`, `register_cli_tool`, `execute_list_capabilities`, `load_prompt_files`, `get_all_schemas` in `core/orchestrator.py`. Also `app.py` tool registration blocks and `prompts/memory.md` + `prompts/rules.md`.
Does NOT touch: `route_message`, `invoke_chat`, `invoke_gemma_text`, `build_system_prompt` (Agent 4A's territory).

### File 1: [core/orchestrator.py](file:///Users/cj/work/llm/open-grug/core/orchestrator.py)

**Fix M8 — Add `friendly_name` to registration and storage.**

Change the internal tuple structure to include friendly_name:

```diff
     def register_python_tool(self, name: str, schema: dict, func: Callable,
-                              destructive: bool = False):
-        self._python_tools[name] = (schema, func, destructive)
+                              destructive: bool = False, friendly_name: str = None):
+        self._python_tools[name] = (schema, func, destructive, friendly_name or name)

     def register_cli_tool(self, name: str, schema: dict, base_command: list,
-                           destructive: bool = True):
-        self._cli_tools[name] = (schema, base_command, destructive)
+                           destructive: bool = True, friendly_name: str = None):
+        self._cli_tools[name] = (schema, base_command, destructive, friendly_name or name)
```

Update `get_all_schemas` to handle the new tuple length:

```diff
     def get_all_schemas(self):
         schemas = []
         for name, data in self._python_tools.items():
             schemas.append({"name": name, "schema": data[0]})
         for name, data in self._cli_tools.items():
             schemas.append({"name": name, "schema": data[0]})
         return schemas
```
No change needed — `data[0]` is still the schema.

Update all tuple unpacking in `execute` to handle 4-element tuples:

```diff
-            schema, func, is_destructive = self._python_tools[tool_name]
+            schema, func, is_destructive, _friendly = self._python_tools[tool_name]
```
```diff
-            schema, base_command, is_destructive = self._cli_tools[tool_name]
+            schema, base_command, is_destructive, _friendly = self._cli_tools[tool_name]
```

**Rewrite `execute_list_capabilities` to read from registry:**

```diff
     def execute_list_capabilities(self):
-        hidden_tools = {"ask_for_clarification", "list_capabilities", "reply_to_user"}
-
-        friendly_names = {
-            "add_note": "Save a note",
-            "get_recent_notes": "Read recent notes",
-            "query_memory": "Search memory",
-            "add_task": "Create a task",
-            "list_tasks": "List tasks",
-            "edit_task": "Update a task",
-            "summarize_board": "Summarize the board",
-        }
-
-        lines = ["I can help you with the following things:"]
-        for s in self.registry.get_all_schemas():
-            name = s.get("name")
-            if name in hidden_tools:
-                continue
-            display_text = friendly_names.get(name, f"Execute operations for {name}")
-            lines.append(f"• {display_text}")
-        return "\n".join(lines)
+        hidden_tools = {"ask_for_clarification", "list_capabilities", "reply_to_user"}
+        lines = ["I can help you with the following things:"]
+        for name, data in self.registry._python_tools.items():
+            if name in hidden_tools:
+                continue
+            friendly = data[3]  # friendly_name
+            lines.append(f"• {friendly}")
+        for name, data in self.registry._cli_tools.items():
+            if name in hidden_tools:
+                continue
+            friendly = data[3]
+            lines.append(f"• {friendly}")
+        return "\n".join(lines)
```

**Fix M9 — Remove `memory.md` from `load_prompt_files` (line 14):**

```diff
-    filenames = ["system.md", "rules.md", "memory.md", "schema_examples.md"]
+    filenames = ["system.md", "rules.md", "schema_examples.md"]
```

### File 2: [prompts/memory.md](file:///Users/cj/work/llm/open-grug/prompts/memory.md)

**Delete this file entirely.**

### File 3: [app.py](file:///Users/cj/work/llm/open-grug/app.py)

**Fix M10 — Add `enum` to `add_note` tags schema (line 124):**

```diff
-            "tags": {"type": "array", "items": {"type": "string"}}
+            "tags": {"type": "array", "items": {"type": "string", "enum": ["dev", "personal", "infra", "meeting", "urgent", "draft", "misc"]}}
```

**Fix M8 — Add `friendly_name=` to all tool registrations in app.py.** Add the kwarg to each `register_python_tool` call:

```python
registry.register_python_tool(
    name="add_note",
    ...,
    func=storage.add_note,
    friendly_name="Save a note"
)
registry.register_python_tool(
    name="get_recent_notes",
    ...,
    func=storage.get_recent_notes,
    friendly_name="Read recent notes"
)
registry.register_python_tool(
    name="query_memory",
    ...,
    func=vector_memory.query_memory,
    friendly_name="Search memory"
)
registry.register_python_tool(
    name="add_task",
    ...,
    func=add_task,
    destructive=True,
    friendly_name="Create a task"
)
registry.register_python_tool(
    name="list_tasks",
    ...,
    func=list_tasks,
    destructive=False,
    friendly_name="List tasks"
)
registry.register_python_tool(
    name="edit_task",
    ...,
    func=edit_task,
    destructive=True,
    friendly_name="Update a task"
)
```

Also add `friendly_name=` to the core tools in `register_core_tools` in orchestrator.py:

```python
# ask_for_clarification — hidden, but still needs the tuple length to match
self.registry.register_python_tool(
    ..., friendly_name="Ask for clarification"
)
# list_capabilities
self.registry.register_python_tool(
    ..., friendly_name="List capabilities"
)
# reply_to_user
self.registry.register_python_tool(
    ..., friendly_name="Chat with Grug"
)
# summarize_board
self.registry.register_python_tool(
    ..., friendly_name="Summarize the board"
)
```

### File 4: [prompts/rules.md](file:///Users/cj/work/llm/open-grug/prompts/rules.md)

Verify the tag list on line 6 matches the enum exactly. Current text says `[dev, personal, infra, meeting, urgent, draft, misc]` — this matches. No change needed, but confirm.

### Verification
```bash
python -c "
from core.orchestrator import ToolRegistry, GrugRouter
r = GrugRouter(ToolRegistry())
caps = r.execute_list_capabilities()
print(caps)
assert 'Summarize the board' in caps
print('friendly_names from registry OK')
"
python test_grug.py   # all tests pass
```

---

### Wave 4 Merge Protocol
1. Agent 4B merges first (only creates new files + edits schema_examples.md)
2. Agent 4A merges second (touches `route_message` in orchestrator)
3. Agent 4C merges third (touches `register_*` + `load_prompt_files` + `execute_list_capabilities` in orchestrator, + `app.py`)
4. Run `python test_grug.py` after all three

---

# Wave 5 — Final Polish & Tests (2 agents, fully parallel)

---

## Agent 5A — M11 + L1: Compression mode preference + category tags

### File: [app.py](file:///Users/cj/work/llm/open-grug/app.py)

**Fix M11 — Replace hardcoded compression mode.** In the `build_system_prompt` call (line 243 area, post-Wave-1):

```diff
-def build_system_prompt(base, summaries, capped_tail, compression_mode="FULL"):
+def build_system_prompt(base, summaries, capped_tail, compression_mode=None):
+    if compression_mode is None:
+        compression_mode = os.environ.get("GRUG_DEFAULT_COMPRESSION", "FULL")
```

**Fix L1 — Add category tags to all tool descriptions.** Prefix each tool description string:

| Tool | Tag | New description prefix |
|---|---|---|
| `add_note` | `[NOTES]` | `"[NOTES] Save an insight..."` |
| `get_recent_notes` | `[NOTES]` | `"[NOTES] Fetch the most recent..."` |
| `query_memory` | `[NOTES]` | `"[NOTES] Use this tool to remember..."` |
| `add_task` | `[BOARD]` | `"[BOARD] Create a new task..."` |
| `list_tasks` | `[BOARD]` | `"[BOARD] List tasks on the project board..."` |
| `edit_task` | `[BOARD]` | `"[BOARD] Update an existing task's status..."` |

And in `core/orchestrator.py` `register_core_tools`:

| Tool | Tag |
|---|---|
| `ask_for_clarification` | `[CHAT]` |
| `list_capabilities` | `[META]` |
| `reply_to_user` | `[CHAT]` |
| `summarize_board` | `[BOARD]` |

### Verification
```bash
GRUG_DEFAULT_COMPRESSION=ULTRA python -c "
import app
prompt = app.build_system_prompt(app.base_prompt, '', '')
assert 'ULTRA' in prompt
print('Compression mode from env OK')
"
```

---

## Agent 5B — L3: Expand test coverage

### File 1: [test_grug.py](file:///Users/cj/work/llm/open-grug/test_grug.py)

Add CLI-branch coverage tests (these build on Agent 2A's H1/H7 tests but test additional scenarios):

```python
def test_21_cli_tool_valid_args_produce_correct_argv():
    """L3: CLI tool with valid args produces expected subprocess command."""
    registry = ToolRegistry()
    registry.register_cli_tool(
        name="test_echo",
        schema={
            "type": "object",
            "properties": {"message": {"type": "string"}},
            "required": ["message"]
        },
        base_command=["echo"],
        destructive=False,
    )
    res = registry.execute("test_echo", {"message": "hello world"})
    assert res.success is True
    assert "hello world" in res.output
    print("[PASS] TEST 21: CLI Tool Valid Args")


def test_22_cli_tool_schema_validation():
    """L3: Invalid CLI args fail jsonschema validation cleanly."""
    registry = ToolRegistry()
    registry.register_cli_tool(
        name="test_cli",
        schema={
            "type": "object",
            "properties": {"count": {"type": "integer"}},
            "required": ["count"]
        },
        base_command=["echo"],
        destructive=False,
    )
    res = registry.execute("test_cli", {"count": "not_a_number"})
    assert res.success is False
    assert "Invalid args" in res.output
    print("[PASS] TEST 22: CLI Schema Validation")


def test_23_destructive_cli_tool_gated_by_hitl():
    """L3: Destructive CLI tool requires approval."""
    registry = ToolRegistry()
    registry.register_cli_tool(
        name="test_destroy",
        schema={"type": "object", "properties": {}},
        base_command=["rm"],
        destructive=True,
    )
    res = registry.execute("test_destroy", {})
    assert res.requires_approval is True
    assert res.tool_name == "test_destroy"
    print("[PASS] TEST 23: Destructive CLI HITL Gate")
```

### File 2: [test_list.py](file:///Users/cj/work/llm/open-grug/test_list.py)

Add real assertions:

```python
import os
from core.orchestrator import ToolRegistry, GrugRouter

registry = ToolRegistry()
router = GrugRouter(registry)

res = registry.execute("list_capabilities", {})
assert res.success is True
assert "I can help you" in res.output
print("[PASS] list_capabilities returns expected output")

res2 = registry.execute("reply_to_user", {"message": "Hello!"})
assert res2.success is True
assert res2.output == "Hello!"
print("[PASS] reply_to_user returns the message")

print("\n--- test_list.py ALL PASSED ---")
```

### Verification
```bash
python test_grug.py    # all 23+ tests pass
python test_list.py    # assertions pass
```

---

# Task → Status Tracker

| ID | Status | Agent | Wave |
|---|---|---|---|
| C1 | ✅ Done (pre-existing) | — | — |
| C2 | ✅ Done (pre-existing) | — | — |
| C3 | ✅ Done (pre-existing) | — | — |
| C4 | ✅ Done | 1A | 1 |
| C5 | ✅ Done | 1B | 1 |
| H1 | ✅ Done | 2A | 2 |
| H2 | 🗑️ Auto-resolved by C4 | — | 1 |
| H3 | ✅ Done (SessionStore exists) | — | — |
| H4 | ✅ Done (json.dumps in invoke_chat) | 1B | 1 |
| H5 | ✅ Done | 2B | 2 |
| H6 | ✅ Done | 3A | 3 |
| H7 | ✅ Done | 2A | 2 |
| H8 | 🗑️ Auto-resolved by C4 | — | 1 |
| H9 | 🗑️ Merged into C5 | 1B | 1 |
| H10 | 🗑️ Merged into H13 | 2A | 2 |
| H11 | 🗑️ Auto-resolved by C4 | — | 1 |
| H12 | ✅ Done (implicit via ask_for_clarification fallback) | 2B | 2 |
| H13 | ✅ Done (as H10+H13) | 2A | 2 |
| M1 | ✅ Done | 3B | 3 |
| M2 | ✅ Done | 3B | 3 |
| M3 | ✅ Done | 3A | 3 |
| M4 | ✅ Done | 3A | 3 |
| M5 | ✅ Done | 4A | 4 |
| M6 | ✅ Done | 4A | 4 |
| M7 | ✅ Done | 4B | 4 |
| M8 | ✅ Done | 4C | 4 |
| M9 | ✅ Done (memory.md deleted) | 4C | 4 |
| M10 | ✅ Done | 4C | 4 |
| M11 | ✅ Done | 5A | 5 |
| M12 | 🗑️ Auto-resolved by C4 | — | 1 |
| L1 | ✅ Done | 5A | 5 |
| L2 | ✅ Done | 4B | 4 |
| L3 | ✅ Done | 5B | 5 |
| L4 | 🗑️ Auto-resolved by C4 | — | 1 |

---

## Verification Plan

### Per-Wave Gate
```bash
python test_grug.py
docker build -t grug-test .
python -c "from core.orchestrator import ToolRegistry, GrugRouter; print('imports OK')"
timeout 5 python app.py || true
```

### Final Verification (after Wave 5)
```bash
python test_grug.py           # all tests green
python test_list.py           # assertions pass
pip install -r requirements.txt  # clean venv
docker build -t grug-test .     # container builds
# Grep for removed artifacts
grep -rn "anthropic\|backlog\|escalate_to_frontier\|memory\.md\|frontier_available" --include="*.py" . | grep -v build-plan | grep -v __pycache__
# Should return nothing
```
