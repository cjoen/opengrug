"""Tests for instruction storage methods and AAR prompt construction."""

import os
import glob
import pytest
from core.storage import GrugStorage


TEST_DIR = "./brain_test"


@pytest.fixture
def storage():
    """Create a clean storage instance with no leftover memory.md."""
    for subdir in ("daily_notes", "daily_logs"):
        path = os.path.join(TEST_DIR, subdir)
        if os.path.exists(path):
            for f in glob.glob(os.path.join(path, "*.md")):
                os.remove(f)
    mem_path = os.path.join(TEST_DIR, "memory.md")
    if os.path.exists(mem_path):
        os.remove(mem_path)
    return GrugStorage(base_dir=TEST_DIR)


# ------------------------------------------------------------------
# get_instructions — empty state
# ------------------------------------------------------------------

def test_get_instructions_empty(storage):
    assert storage.get_instructions() == []


def test_get_instructions_block_empty(storage):
    assert storage.get_instructions_block() == ""


# ------------------------------------------------------------------
# add_instruction — happy path
# ------------------------------------------------------------------

def test_add_instruction_basic(storage):
    result = storage.add_instruction("Always greet the user warmly", "conversation", 1500)
    assert "added" in result.lower()
    items = storage.get_instructions()
    assert len(items) == 1
    assert items[0]["tag"] == "conversation"
    assert items[0]["text"] == "Always greet the user warmly"


def test_add_multiple_instructions(storage):
    storage.add_instruction("Use stable task IDs", "tasks", 1500)
    storage.add_instruction("Keep responses concise", "conversation", 1500)
    items = storage.get_instructions()
    assert len(items) == 2


# ------------------------------------------------------------------
# add_instruction — validation
# ------------------------------------------------------------------

def test_add_instruction_invalid_tag(storage):
    result = storage.add_instruction("Some valid instruction text", "invalid_tag", 1500)
    assert "invalid tag" in result.lower()
    assert storage.get_instructions() == []


def test_add_instruction_too_short(storage):
    result = storage.add_instruction("Short", "general", 1500)
    assert "too short" in result.lower()


def test_add_instruction_too_long(storage):
    result = storage.add_instruction("x" * 201, "general", 1500)
    assert "too long" in result.lower()


# ------------------------------------------------------------------
# add_instruction — dedup
# ------------------------------------------------------------------

def test_add_instruction_exact_duplicate(storage):
    storage.add_instruction("Always greet the user warmly", "conversation", 1500)
    result = storage.add_instruction("Always greet the user warmly", "conversation", 1500)
    assert "duplicate" in result.lower()
    assert len(storage.get_instructions()) == 1


def test_add_instruction_substring_duplicate(storage):
    storage.add_instruction("Always greet the user warmly", "conversation", 1500)
    result = storage.add_instruction("greet the user warmly", "conversation", 1500)
    assert "duplicate" in result.lower()


def test_add_instruction_case_insensitive_duplicate(storage):
    storage.add_instruction("Always greet the user warmly", "conversation", 1500)
    result = storage.add_instruction("ALWAYS GREET THE USER WARMLY", "conversation", 1500)
    assert "duplicate" in result.lower()


# ------------------------------------------------------------------
# add_instruction — budget
# ------------------------------------------------------------------

def test_add_instruction_budget_exceeded(storage):
    storage.add_instruction("A" * 100 + " first rule", "general", 200)
    result = storage.add_instruction("B" * 100 + " second rule", "general", 200)
    assert "budget" in result.lower()


# ------------------------------------------------------------------
# edit_instruction
# ------------------------------------------------------------------

def test_edit_instruction_text(storage):
    storage.add_instruction("Old instruction text here", "general", 1500)
    result = storage.edit_instruction(1, "New instruction text here")
    assert "updated" in result.lower()
    items = storage.get_instructions()
    assert items[0]["text"] == "New instruction text here"
    assert items[0]["tag"] == "general"


def test_edit_instruction_tag(storage):
    storage.add_instruction("Keep responses concise", "general", 1500)
    result = storage.edit_instruction(1, "Keep responses concise", tag="conversation")
    assert "updated" in result.lower()
    assert storage.get_instructions()[0]["tag"] == "conversation"


def test_edit_instruction_invalid_number(storage):
    storage.add_instruction("Some instruction text here", "general", 1500)
    result = storage.edit_instruction(5, "New text for editing")
    assert "invalid" in result.lower()


def test_edit_instruction_dedup(storage):
    storage.add_instruction("First instruction is here", "general", 1500)
    storage.add_instruction("Second instruction text", "general", 1500)
    result = storage.edit_instruction(2, "First instruction is here")
    assert "duplicate" in result.lower()


# ------------------------------------------------------------------
# remove_instruction
# ------------------------------------------------------------------

def test_remove_instruction(storage):
    storage.add_instruction("Instruction to remove later", "general", 1500)
    storage.add_instruction("Instruction to keep around", "general", 1500)
    result = storage.remove_instruction(1)
    assert "removed" in result.lower()
    items = storage.get_instructions()
    assert len(items) == 1
    assert items[0]["text"] == "Instruction to keep around"


def test_remove_instruction_invalid_number(storage):
    result = storage.remove_instruction(1)
    assert "invalid" in result.lower()


# ------------------------------------------------------------------
# get_instructions_block — formatting
# ------------------------------------------------------------------

def test_get_instructions_block_grouped(storage):
    storage.add_instruction("Use stable task IDs always", "tasks", 1500)
    storage.add_instruction("Keep responses under three sentences", "conversation", 1500)
    storage.add_instruction("Check timezone before scheduling", "general", 1500)
    block = storage.get_instructions_block()
    assert "[TASKS]" in block
    assert "[CONVERSATION]" in block
    assert "[GENERAL]" in block
    assert "- Use stable task IDs always" in block


# ------------------------------------------------------------------
# Valid tags
# ------------------------------------------------------------------

def test_all_valid_tags(storage):
    for tag in ["tasks", "notes", "scheduling", "conversation", "general"]:
        result = storage.add_instruction(f"Instruction for the {tag} category", tag, 5000)
        assert "added" in result.lower(), f"Tag '{tag}' should be valid"


# ------------------------------------------------------------------
# AAR prompt construction
# ------------------------------------------------------------------

def test_generate_aar_builds_transcript():
    """Test that generate_aar constructs the right prompt structure."""
    class MockLLM:
        def generate(self, prompt):
            # Verify the prompt contains expected structure
            assert "What Went Wrong" in prompt
            assert "What To Remember" in prompt
            assert "USER: hello" in prompt
            assert "ASSISTANT: hi there" in prompt
            return "## What Went Wrong\nNothing.\n\nNo issues found."

    from core.summarizer import Summarizer
    s = Summarizer(llm_client=MockLLM())
    messages = [
        {"role": "user", "content": "hello"},
        {"role": "assistant", "content": "hi there"},
    ]
    result = s.generate_aar(messages)
    assert "No issues found" in result


def test_generate_aar_empty_messages():
    from core.summarizer import Summarizer
    s = Summarizer(llm_client=None)
    result = s.generate_aar([])
    assert "No conversation content" in result


def test_generate_aar_skips_empty_content():
    class MockLLM:
        def generate(self, prompt):
            assert "USER:" not in prompt or "empty" not in prompt.lower()
            return "No issues found."

    from core.summarizer import Summarizer
    s = Summarizer(llm_client=MockLLM())
    messages = [
        {"role": "user", "content": ""},
        {"role": "assistant", "content": "response here"},
    ]
    result = s.generate_aar(messages)
    assert result == "No issues found."


def test_generate_aar_handles_llm_error():
    class FailLLM:
        def generate(self, prompt):
            raise RuntimeError("LLM down")

    from core.summarizer import Summarizer
    s = Summarizer(llm_client=FailLLM())
    messages = [{"role": "user", "content": "hello"}]
    result = s.generate_aar(messages)
    assert "failed" in result.lower()
