# OpenGrug Evals (LLM Reasoning Tests)

This directory contains the Evaluation Framework ("Evals") for OpenGrug.

While the standard `tests/` directory verifies deterministic code logic, this `evals/` directory evaluates the **probabilistic reasoning** of the language model itself using a live Ollama instance.

## Purpose
Run evals when you:
* Modify system prompts (`prompts/`)
* Add or change a tool schema (`tools/`)
* Switch the underlying LLM (e.g., Gemma → Llama)
* Want to verify adversarial/injection resistance

## How it Works
The eval harness calls the **real `register_tools()` functions** from each tool module with mocked dependencies. This guarantees the LLM sees the exact same tool descriptions and schemas as production — no simplified stubs. The system prompt is interpolated through `build_system_prompt()` just like production, including `{{CURRENT_DATE}}` and `{{CURRENT_TIME}}` substitution.

Responses are intercepted at the `invoke_chat` layer before tool execution, so no side effects occur.

## Prerequisites
* **Ollama running locally** (or accessible at a remote host)
* **The target model pulled** — e.g. `ollama pull gemma:e4b`
* **Python dependencies installed** — the same `requirements.txt` as the main project (`pyyaml`, `jsonschema`, `requests`, etc.)

## Running

```bash
# Basic run against local Ollama
export OLLAMA_HOST="http://localhost:11434"
python3 evals/run_evals.py

# Override the model to compare reasoning across models
GRUG_MODEL="llama3:8b" python3 evals/run_evals.py

# Filter by session ID prefix
python3 evals/run_evals.py --filter adv

# Filter by category
python3 evals/run_evals.py --category SCHEDULE

# Save structured JSON results for tracking regressions
python3 evals/run_evals.py --output evals/results.json
```

> **Note:** Evals hit a live LLM, so expect ~2-10 seconds per case depending on your hardware. A full 30-case run may take several minutes on slower machines.

> **Note:** You may see a `NotOpenSSLWarning` from urllib3 on macOS — this is harmless and does not affect results.

## Dataset Format (`golden_dataset.jsonl`)

Each line is a JSON object. Lines starting with `#` are treated as comments.

```json
{
  "session_id": "task-002",
  "category": "TASKS",
  "messages": [{"role": "user", "content": "Add a high priority task to fix the db"}],
  "expected_tool": "add_task",
  "expected_args": {"priority": "high"}
}
```

### Multi-tool assertions
```json
{
  "session_id": "multi-001",
  "messages": [{"role": "user", "content": "Add a task and a note about it"}],
  "expected_tools": [
    {"tool": "add_task", "args": {}},
    {"tool": "add_note", "args": {}}
  ]
}
```

### Argument matching rules
- **String values**: case-insensitive, whitespace-trimmed (fuzzy)
- **Numeric/enum values**: exact match
- **Subset match**: only the keys you specify are checked

## Test Categories
| Category | What it tests |
|---|---|
| `SYSTEM` | Greetings, chitchat, factual Q&A, help requests |
| `NOTES` | Note saving, memory search, keyword search boundaries |
| `TASKS` | Task creation, listing, completion with correct IDs |
| `SCHEDULE` | Reminder creation, schedule listing |
| `MULTI` | Multi-tool requests in a single turn |
| `ADVERSARIAL` | Prompt injection, distractor resistance, ambiguity handling |

## Result Output

When using `--output`, results are saved as structured JSON:

```json
{
  "model": "gemma:e4b",
  "host": "http://localhost:11434",
  "timestamp": "2026-04-22T17:04:19",
  "summary": {"passed": 28, "failed": 1, "errors": 1},
  "cases": [...]
}
```

This lets you diff results across model upgrades or prompt changes.
