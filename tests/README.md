# OpenGrug Tests (Code Verification)

This directory contains the standard Unit and Integration tests for the OpenGrug platform.

While the `evals/` directory tests the non-deterministic reasoning of the AI, this `tests/` directory is strictly for verifying the **deterministic software logic** (the "plumbing").

## Purpose
These tests verify that:
* Database operations (CRUD for notes, tasks) work correctly.
* The `GrugRouter` properly parses LLM responses and dispatches to the `ToolRegistry`.
* Context window management and summarization triggers fire properly.
* The system is thread-safe.

**These tests should NOT hit a real LLM endpoint.** Any interactions with the LLM layer are mocked or rely on fallback states to ensure the test suite remains blazing fast, isolated, and 100% deterministic.

## Running the Tests
The test suite is built on `pytest`.

```bash
# Run the entire test suite
pytest tests/

# Run a specific test file
pytest tests/test_task_store.py
```

## Architecture Note
If you are adding new Tools or modifying prompts, you likely want to add a case to the `evals/golden_dataset.jsonl` rather than here. Only write tests here when modifying core Python logic (like `core/` or `tools/` implementation code).
