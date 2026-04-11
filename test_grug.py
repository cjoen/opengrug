"""Unit tests for the Grug orchestration layer.

Run with: python test_grug.py

## Manual E2E Checklist (not automated)
1. docker-compose up  — verify container starts as UID 1000 (non-root)
2. Send a Slack message: "Remind me to call Alice tomorrow"
   - Expect :thought_balloon: reaction appears then clears
   - Expect response about task added
   - Expect ./brain/daily_notes/<today>.md to contain a new "- HH:MM:SS [task]" line
3. Send a destructive-tool message (if any are registered as destructive=True)
   - Expect Block Kit Approve/Deny card
   - Click Approve — expect tool to execute and result posted
4. Send complex synthesis ("analyze this log and summarize..."):
   - With CLAUDE_API_KEY set — expect Claude response
   - With CLAUDE_API_KEY="" — expect "Degraded Response:" fallback
5. Write a new bullet to ./brain/daily_notes/<today>.md manually, wait 30s,
   then query_memory — expect the new block to be semantically searchable.
"""

import os
import glob
from datetime import datetime
from core.storage import GrugStorage
from core.vectors import VectorMemory
from core.orchestrator import ToolRegistry, GrugRouter, load_prompt_files, ToolExecutionResult


TEST_DIR = "./brain_test"


def _fresh_setup():
    """Create a clean test environment and return (storage, registry, router)."""
    # Clean slate: remove any existing test daily notes
    daily_notes = os.path.join(TEST_DIR, "daily_notes")
    if os.path.exists(daily_notes):
        for f in glob.glob(os.path.join(daily_notes, "*.md")):
            os.remove(f)

    storage = GrugStorage(base_dir=TEST_DIR)
    registry = ToolRegistry()
    registry.register_python_tool(
        name="add_note",
        schema={
            "type": "object",
            "properties": {
                "content": {"type": "string"}
            },
            "required": ["content"]
        },
        func=storage.add_note
    )
    os.environ["CLAUDE_API_KEY"] = ""  # Force offline for deterministic tests
    router = GrugRouter(registry)
    return storage, registry, router


def test_1_caveman_storage_flow():
    storage, registry, router = _fresh_setup()
    base_prompt = load_prompt_files("prompts")

    # Mock Gemma to return a valid add_note call
    router.invoke_gemma = lambda prompt: '{"confidence_score": 10, "tool": "add_note", "arguments": {"content": "Fire is hot."}}'
    res = router.route_message(
        "Store this idea: Fire is hot.",
        context="Test Env",
        base_system_prompt=base_prompt,
    )

    assert res.success is True, f"expected success=True, got {res}"
    today = datetime.now().strftime("%Y-%m-%d")
    daily_file = os.path.join(TEST_DIR, "daily_notes", f"{today}.md")
    assert os.path.exists(daily_file), f"daily note file not created at {daily_file}"
    with open(daily_file, "r", encoding="utf-8") as f:
        content = f.read()
    assert "Fire is hot." in content, f"note not written to markdown file: {content!r}"
    print("[PASS] TEST 1: Caveman Storage Flow")


def test_2_graceful_offline_degradation():
    storage, registry, router = _fresh_setup()
    base_prompt = load_prompt_files("prompts")

    def mock_gemma(prompt):
        if "OFFLINE" in prompt:
            return '{"confidence_score": 10, "tool": "add_note", "arguments": {"content": "Grug no reach cloud. Fire hot."}}'
        return '{"confidence_score": 10, "tool": "escalate_to_frontier", "arguments": {"reason_for_escalation": "Too hard"}}'

    router.invoke_gemma = mock_gemma
    res = router.route_message(
        "Explain quantum mechanics.",
        context="Test Env",
        base_system_prompt=base_prompt,
    )

    assert res.success is True, f"expected success=True, got {res}"
    assert res.output, "degraded response must be non-empty"
    assert "Degraded Response" in res.output or "Grug no reach cloud" in res.output, (
        f"expected degradation marker in output, got: {res.output!r}"
    )
    print("[PASS] TEST 2: Graceful Offline Degradation")


def test_3_schema_validation_rejects_bad_args():
    _storage, registry, _router = _fresh_setup()

    # add_note requires "content" (string). Pass an invalid args dict.
    res = registry.execute("add_note", {"wrong_field": 1})
    assert res.success is False, f"expected success=False on bad args, got {res}"
    assert "Invalid args" in res.output, f"expected 'Invalid args' in output, got: {res.output!r}"
    print("[PASS] TEST 3: Schema Validation Rejects Bad Args")


def test_4_confidence_score_forces_escalation():
    _storage, registry, router = _fresh_setup()
    base_prompt = load_prompt_files("prompts")

    # Gemma returns a low confidence score on a non-escalation tool
    call_count = {"n": 0}

    def mock_gemma(prompt):
        call_count["n"] += 1
        if "OFFLINE" in prompt:
            return '{"confidence_score": 10, "tool": "add_note", "arguments": {"content": "best effort"}}'
        return '{"confidence_score": 5, "tool": "add_note", "arguments": {"content": "unsure"}}'

    router.invoke_gemma = mock_gemma
    res = router.route_message(
        "Complex query",
        context="Test",
        base_system_prompt=base_prompt,
    )

    assert res.success is True, f"expected success=True, got {res}"
    # CLAUDE_API_KEY="" forces ERROR_OFFLINE from escalation, which triggers Gemma re-prompt w/ OFFLINE marker
    assert call_count["n"] >= 2, (
        f"expected Gemma to be called at least twice (first for tool call, then for offline fallback), got {call_count['n']}"
    )
    assert "Degraded Response" in res.output, (
        f"expected degraded response after failed escalation, got: {res.output!r}"
    )
    print("[PASS] TEST 4: Confidence Score Forces Escalation")


def test_5_hitl_requires_approval_populates_fields():
    _storage, registry, _router = _fresh_setup()

    # Register a destructive tool
    registry.register_python_tool(
        name="delete_note",
        schema={
            "type": "object",
            "properties": {"note_id": {"type": "integer"}},
            "required": ["note_id"],
        },
        func=lambda note_id: f"deleted {note_id}",
        destructive=True,
    )

    res = registry.execute("delete_note", {"note_id": 42})
    assert res.requires_approval is True, f"expected requires_approval=True, got {res}"
    assert res.tool_name == "delete_note", f"expected tool_name populated, got {res.tool_name!r}"
    assert res.arguments == {"note_id": 42}, f"expected arguments populated, got {res.arguments!r}"
    print("[PASS] TEST 5: HITL Populates tool_name/arguments")


def test_6_prompt_stitching_and_current_date():
    _storage, _registry, router = _fresh_setup()

    stitched = load_prompt_files("prompts")
    # All four files should appear as section headers
    for name in ("system.md", "rules.md", "memory.md", "schema_examples.md"):
        assert f"## {name}" in stitched, f"missing section header for {name}"

    # {{CURRENT_DATE}} lives in rules.md
    assert "{{CURRENT_DATE}}" in stitched, "expected {{CURRENT_DATE}} placeholder before interpolation"

    # After build_system_prompt, both placeholders should be gone
    built = router.build_system_prompt(stitched, compression_mode="ULTRA")
    assert "{{CURRENT_DATE}}" not in built, "CURRENT_DATE was not interpolated"
    assert "{{COMPRESSION_MODE}}" not in built, "COMPRESSION_MODE was not interpolated"
    today = datetime.now().strftime("%Y-%m-%d")
    assert today in built, f"expected today's date {today} in built prompt"
    print("[PASS] TEST 6: Prompt Stitching + CURRENT_DATE Interpolation")


def run_tests():
    print("--- TESTING GRUG ARCHITECTURE ---")
    test_1_caveman_storage_flow()
    test_2_graceful_offline_degradation()
    test_3_schema_validation_rejects_bad_args()
    test_4_confidence_score_forces_escalation()
    test_5_hitl_requires_approval_populates_fields()
    test_6_prompt_stitching_and_current_date()
    print("\n--- ALL TESTS PASSED ---")


if __name__ == "__main__":
    run_tests()
