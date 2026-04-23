#!/usr/bin/env python3
"""Golden Dataset Evaluation Harness for OpenGrug.

Runs JSONL test cases against a live LLM to evaluate its reasoning
and tool-calling accuracy. Uses the REAL production tool schemas
and interpolated system prompt — not simplified stubs.

Usage:
    export OLLAMA_HOST="http://localhost:11434"
    export GRUG_MODEL="gemma:e4b"          # optional override
    python evals/run_evals.py
    python evals/run_evals.py --filter eval-004
    python evals/run_evals.py --category SCHEDULE
"""
import os
import sys
import json
import time
import warnings
import argparse

# Suppress harmless urllib3 SSL warning on macOS (LibreSSL vs OpenSSL)
warnings.filterwarnings("ignore", message="urllib3 v2 only supports OpenSSL")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.registry import ToolRegistry
from core.utils import load_prompt_files
from core.router import GrugRouter
from core.llm import OllamaClient
from core.config import config
from core.context import build_system_prompt


# ---------------------------------------------------------------------------
# Mock dependencies — satisfy register_tools() signatures without side effects
# ---------------------------------------------------------------------------

class _MockStorage:
    """Satisfies GrugStorage interface for tool registration."""
    def add_note(self, **kw): return "stub"
    def get_raw_notes(self, **kw): return ""
    def get_capped_tail(self, *a): return ""
    def append_log(self, *a): pass

class _MockLLMClient:
    """Satisfies OllamaClient interface for tool registration (not used for inference)."""
    def generate(self, prompt): return "stub title"
    def chat(self, *a, **kw): return None

class _MockVectorMemory:
    """Satisfies VectorMemory interface for tool registration."""
    def query_memory(self, query, **kw): return "stub"
    def query_memory_raw(self, *a, **kw): return []
    def stats(self): return {"enabled": False, "block_count": 0, "db_size": 0}

class _MockSessionStore:
    def session_count(self): return 0

class _MockMessageQueue:
    worker_count = 1

class _MockScheduleStore:
    """Satisfies ScheduleStore interface for tool registration."""
    from datetime import timezone
    tz = timezone.utc
    def add_schedule(self, **kw): return 1
    def list_schedules(self, **kw): return []
    def delete(self, *a): pass

class _MockTaskList:
    """Satisfies TaskList interface for tool registration."""
    def add_task(self, **kw): return "stub"
    def list_tasks(self, **kw): return "stub"
    def complete_task(self, **kw): return "stub"


def _register_production_schemas(registry, router):
    """Register ALL production tool schemas using the real register_tools()
    functions from each tool module, with mocked dependencies.

    This guarantees the eval LLM sees the exact same tool descriptions
    and parameter schemas as the production deployment.
    """
    mock_storage = _MockStorage()
    mock_llm = _MockLLMClient()
    mock_vectors = _MockVectorMemory()
    mock_sessions = _MockSessionStore()
    mock_queue = _MockMessageQueue()
    mock_schedule_store = _MockScheduleStore()
    mock_task_list = _MockTaskList()
    mock_brain_dir = "/tmp/grug_eval_brain"

    from tools.system import register_tools as register_system_tools
    from tools.notes import register_tools as register_note_tools
    from tools.tasks import register_tools as register_task_tools
    from tools.scheduler_tools import register_tools as register_scheduler_tools
    from tools.health import register_tools as register_health_tools

    register_system_tools(registry, router)
    register_note_tools(registry, mock_storage, mock_llm, mock_vectors, mock_brain_dir)
    register_task_tools(registry, mock_task_list, mock_storage)
    register_scheduler_tools(registry, mock_schedule_store, router, config)
    register_health_tools(registry, mock_vectors, mock_sessions, mock_queue,
                          mock_schedule_store, mock_llm, mock_brain_dir)


# ---------------------------------------------------------------------------
# Argument matching helpers
# ---------------------------------------------------------------------------

def _normalize(val):
    """Normalize a string value for fuzzy comparison."""
    if isinstance(val, str):
        return val.strip().lower()
    return val


def _check_args(expected_args, actual_args):
    """Check expected arguments against actual. Returns (passed, message).

    For string values: uses case-insensitive, whitespace-trimmed comparison.
    For enum/numeric values: uses exact match.
    """
    for key, expected_val in expected_args.items():
        actual_val = actual_args.get(key)
        if actual_val is None:
            return False, f"Missing argument '{key}'. Expected: {expected_val}"

        if isinstance(expected_val, str) and isinstance(actual_val, str):
            if _normalize(expected_val) != _normalize(actual_val):
                return False, f"Argument '{key}': expected ~'{expected_val}', got '{actual_val}'"
        elif expected_val != actual_val:
            return False, f"Argument '{key}': expected {expected_val}, got {actual_val}"

    return True, ""


# ---------------------------------------------------------------------------
# Main harness
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="OpenGrug LLM Eval Harness")
    parser.add_argument("--filter", help="Run only cases matching this session_id prefix")
    parser.add_argument("--category", help="Run only cases matching this category tag")
    parser.add_argument("--output", help="Write JSON results to this file")
    args = parser.parse_args()

    dataset_path = os.path.join(os.path.dirname(__file__), "golden_dataset.jsonl")
    if not os.path.exists(dataset_path):
        print(f"Dataset not found at {dataset_path}")
        sys.exit(1)

    # 1. Setup LLM Client (this is the REAL client that hits Ollama)
    ollama_host = os.environ.get("OLLAMA_HOST", config.llm.ollama_host)
    model_name = os.environ.get("GRUG_MODEL", config.llm.model_name)

    print(f"🚀 Evals — Ollama: {ollama_host} | Model: {model_name}")
    print(f"{'='*60}")

    llm_client = OllamaClient(
        host=ollama_host,
        model=model_name,
        timeout=config.llm.ollama_timeout,
        num_keep=config.llm.num_keep,
    )

    # 2. Setup Router with REAL production schemas
    registry = ToolRegistry()
    router = GrugRouter(registry=registry, storage=None, llm_client=llm_client)
    _register_production_schemas(registry, router)

    # 3. Build the system prompt the same way production does
    base_prompt = load_prompt_files("prompts")
    system_prompt = build_system_prompt(base_prompt, capped_tail="", rag_context="")

    schemas = registry.get_all_schemas()
    tool_count = len(schemas)
    print(f"   Registered {tool_count} tools (production parity)")
    print(f"   System prompt: {len(system_prompt)} chars (interpolated)")
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
                print(f"  ⚠️  PARSE ERROR on line {line_no}: {e}")
                errors += 1
                continue

            session_id = case.get("session_id", f"line-{line_no}")
            category = case.get("category", "")
            messages = case.get("messages", [])
            expected_tools = case.get("expected_tools", [])
            expected_tool = case.get("expected_tool")
            expected_args = case.get("expected_args", {})
            accept_tools = case.get("accept_tools", [])

            # Backward compat: single expected_tool → list
            if expected_tool and not expected_tools:
                expected_tools = [{"tool": expected_tool, "args": expected_args}]

            # Filtering
            if args.filter and not session_id.startswith(args.filter):
                continue
            if args.category and category.upper() != args.category.upper():
                continue

            label = messages[-1]["content"][:55] if messages else "empty"
            print(f"  [{session_id}] {label}...")

            try:
                start_time = time.time()
                response = router.invoke_chat(
                    system_prompt=system_prompt,
                    messages=messages,
                    tools=schemas,
                )
                duration = time.time() - start_time

                actual_calls = response.tool_calls or []

                # --- Assertion logic ---
                case_passed = True
                failure_reason = ""

                for i, expectation in enumerate(expected_tools):
                    exp_tool = expectation["tool"]
                    exp_args = expectation.get("args", {})

                    if i >= len(actual_calls):
                        case_passed = False
                        failure_reason = f"Expected {len(expected_tools)} tool call(s), got {len(actual_calls)}"
                        break

                    act_tool = actual_calls[i].get("tool")
                    act_args = actual_calls[i].get("arguments", {})

                    if act_tool != exp_tool:
                        # Check if it matches an accepted alternative
                        if act_tool in accept_tools:
                            case_passed = True
                            failure_reason = ""
                            break  # Accept the alternative
                        case_passed = False
                        failure_reason = f"Tool #{i+1}: expected '{exp_tool}', got '{act_tool}'"
                        break

                    if exp_args:
                        args_ok, args_msg = _check_args(exp_args, act_args)
                        if not args_ok:
                            case_passed = False
                            failure_reason = f"Tool #{i+1} ({exp_tool}): {args_msg}"
                            break

                if case_passed:
                    actual_summary = ", ".join(c.get("tool", "?") for c in actual_calls)
                    print(f"    ✅ PASS ({duration:.2f}s) → {actual_summary}")
                    passed += 1
                else:
                    print(f"    ❌ FAIL: {failure_reason}")
                    # Show what the LLM actually returned for debugging
                    for j, call in enumerate(actual_calls):
                        print(f"       actual[{j}]: {call.get('tool')} → {json.dumps(call.get('arguments', {}), default=str)[:100]}")
                    failed += 1

                results.append({
                    "session_id": session_id,
                    "passed": case_passed,
                    "duration": round(duration, 2),
                    "expected": [e["tool"] for e in expected_tools],
                    "actual": [c.get("tool") for c in actual_calls],
                    "failure_reason": failure_reason,
                })

            except Exception as e:
                print(f"    ⚠️  ERROR: {e}")
                errors += 1
                results.append({
                    "session_id": session_id,
                    "passed": False,
                    "error": str(e),
                })

    # 5. Summary
    total = passed + failed + errors
    print(f"\n{'='*60}")
    print(f"Results: {passed} passed, {failed} failed, {errors} errors ({total} total)")

    # 6. Failed test recap
    failed_cases = [r for r in results if not r.get("passed")]
    if failed_cases:
        print(f"\n{'─'*60}")
        print("FAILED CASES:")
        print(f"{'─'*60}")
        for r in failed_cases:
            sid = r["session_id"]
            if "error" in r:
                print(f"  {sid}: ERROR — {r['error']}")
            else:
                expected = ", ".join(r.get("expected", []))
                actual = ", ".join(r.get("actual", []))
                print(f"  {sid}: expected [{expected}] → got [{actual}]")
                if r.get("failure_reason"):
                    print(f"    └─ {r['failure_reason']}")

    if args.output:
        with open(args.output, "w", encoding="utf-8") as f:
            json.dump({
                "model": model_name,
                "host": ollama_host,
                "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
                "summary": {"passed": passed, "failed": failed, "errors": errors},
                "cases": results,
            }, f, indent=2)
        print(f"\nResults written to {args.output}")

    sys.exit(1 if (failed + errors) > 0 else 0)


if __name__ == "__main__":
    main()
