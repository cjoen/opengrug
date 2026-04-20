"""Shared fixtures for Grug test suite."""

import os
import glob
import pytest
from core.storage import GrugStorage
from core.registry import ToolRegistry
from core.router import GrugRouter
from tools.system import register_tools as register_system_tools


TEST_DIR = "./brain_test"


@pytest.fixture
def fresh_env():
    """Create a clean test environment and return (storage, registry, router)."""
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
        func=storage.add_note,
        category="NOTES"
    )
    os.environ["CLAUDE_API_KEY"] = ""
    router = GrugRouter(registry)
    register_system_tools(registry, router)
    return storage, registry, router
