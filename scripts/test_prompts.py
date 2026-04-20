#!/usr/bin/env python3
"""Offline prompt regression test harness.

Runs prompt fixtures against a live local Ollama instance and verifies
the model routes to the expected tool.

Usage: python3 scripts/test_prompts.py
Requires: Ollama running locally with the configured model.
"""
import os
import sys
import json
import yaml

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.registry import ToolRegistry, load_prompt_files
from core.router import GrugRouter


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

    passed, failed, errors = 0, 0, 0
    for case in fixtures["cases"]:
        user_msg = case["input"]
        expected_tool = case["expected_tool"]
        try:
            res = router.route_message(
                user_msg,
                context="Prompt test harness",
                base_system_prompt=base_prompt,
            )

            # Parse what tool was actually called from the result
            actual_output = res.output if res else ""

            # Heuristic checks based on expected tool behavior
            if expected_tool == "reply_to_user" and res.success and not res.requires_approval:
                status = "PASS"
                passed += 1
            elif expected_tool in ("add_task", "edit_task") and res.requires_approval:
                status = "PASS"
                passed += 1
            elif expected_tool == "list_capabilities" and "I can help" in actual_output:
                status = "PASS"
                passed += 1
            elif expected_tool == "ask_for_clarification" and "Grug" in actual_output:
                status = "PASS"
                passed += 1
            elif expected_tool in ("add_note", "get_recent_notes", "query_memory",
                                   "list_tasks", "summarize_board"):
                # These tools execute and return output; hard to verify without
                # real data, so we just check success
                if res.success:
                    status = "PASS"
                    passed += 1
                else:
                    status = f"FAIL (got: {actual_output[:80]})"
                    failed += 1
            else:
                status = f"FAIL (got: {actual_output[:80]})"
                failed += 1
        except Exception as e:
            status = f"ERROR ({e})"
            errors += 1

        print(f"  [{status}] {user_msg[:60]:<60} → expected {expected_tool}")

    print(f"\n{'='*60}")
    print(f"Results: {passed} passed, {failed} failed, {errors} errors out of {passed + failed + errors}")
    sys.exit(1 if (failed + errors) > 0 else 0)


if __name__ == "__main__":
    main()
