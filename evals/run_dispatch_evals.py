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
