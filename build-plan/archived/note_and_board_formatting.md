# Note & Board Formatting Plan

**Status:** Implemented (note retrieval); board summary N/A (simplified to todo list)

Two UX improvements to make Grug's output more readable.

---

## 5.2 — High-Density Note Retrieval

### Goal
Replace the raw `get_recent_notes` registered tool with a formatted bulletin version. Keep the underlying storage method as a private data-fetcher.

### Step 1: Rename storage method (`core/storage.py`)

Rename `get_recent_notes` → `get_raw_notes`. No logic changes — just the name.

```python
def get_raw_notes(self, limit: int = 10) -> str:
    # identical body to current get_recent_notes
```

### Step 2: Update all internal callers

Two places call `get_recent_notes` directly:
- `app.py:398` — fallback context injection: update to `storage.get_raw_notes(limit=10)`
- `app.py:132` — tool registration: this block gets replaced entirely (see Step 3)

### Step 3: Update tool registration (`app.py`)

Remove the current `get_recent_notes` tool registration block.

Replace with a registration that points to the new router method:
```python
registry.register_python_tool(
    name="get_recent_notes",
    schema={
        "description": "[NOTES] Fetch and display recent notes as a readable grouped bulletin. Use when the user asks to see, show, or read their notes.",
        "type": "object",
        "properties": {}
    },
    func=router.execute_get_recent_notes,
    friendly_name="Read recent notes"
)
```

No `limit` argument exposed to Grug — limit is controlled by `config.memory.notes_display_limit`.

### Step 4: Add config value (`core/config.py`)

Add `notes_display_limit` to the `memory` section of `_DEFAULTS`:
```python
"notes_display_limit": 10,
```

### Step 5: Add `execute_get_recent_notes` to `GrugRouter` (`core/orchestrator.py`)

New method on `GrugRouter`, same pattern as `execute_summarize_board`:

```python
import re

def execute_get_recent_notes(self):
    raw = self.storage.get_raw_notes(limit=config.memory.notes_display_limit)
    if not raw:
        return "Cave empty. No notes yet."

    # Group lines by tag — scan each line for first #tag found
    groups = {}  # tag -> list of note content strings
    for line in raw.splitlines():
        # Line format: "- HH:MM:SS [note] content #tag"
        tag = "misc"
        tag_match = re.search(r"#(\w+)", line)
        if tag_match:
            tag = tag_match.group(1)
        # Strip markdown bullet, timestamp, [source] prefix
        content = re.sub(r"^- \d+:\d+:\d+ \[\w+\] ", "", line).strip()
        groups.setdefault(tag, []).append(content)

    # Build grouped text block — no LLM reformat needed,
    # Grug's main LLM handles tone when presenting to user
    result = ""
    for tag, notes in groups.items():
        result += f"[{tag.upper()}]\n"
        for n in notes:
            result += f"  - {n}\n"

    return result
```

### Implementation Note: Storage Access

Pass `storage` into `GrugRouter.__init__` and store as `self.storage`. Update instantiation in `app.py` (line 195):
```python
router = GrugRouter(registry, storage)
```

---

## Summarize Board — Format Improvement

### Goal
Replace the raw `--- Full list ---` dump with a clean formatted task list grouped by status.

### Changes: `execute_summarize_board` in `core/orchestrator.py`

Current structure:
```
<llm summary>

--- Full list ---
<raw markdown>
```

New structure:
```
<llm summary>

Tasks:
• [Todo] Fix login button
• [In Progress] Update docs
• [Done] Deploy v1.2
```

Steps inside `execute_summarize_board`:
1. After getting `raw` from `list_tasks`, parse each line to extract status and title
2. Sort into buckets: Todo → In Progress → Done → other
3. Format as bulleted list: `• [Status] Title`
4. Append after LLM summary — no `---` divider, just a blank line separator

**Line parsing:** `list_tasks` returns lines in format `"{line_number}: {stripped_task_line}"` (not raw markdown bullets). The parser needs to strip the `N: ` prefix before extracting status/title.

---

## Files Changed

| File | Change |
|------|--------|
| `core/storage.py` | Rename `get_recent_notes` → `get_raw_notes` |
| `core/config.py` | Add `notes_display_limit: 10` to `memory` defaults |
| `core/orchestrator.py` | Add `storage` param to `GrugRouter.__init__`, add `execute_get_recent_notes`, update `execute_summarize_board` task list formatting |
| `app.py` | Update `get_recent_notes` tool registration to point to `router.execute_get_recent_notes`, update `storage.get_recent_notes` call to `storage.get_raw_notes` |

---

## Tests

- `get_raw_notes` rename: existing storage tests should still pass with updated method name
- `execute_get_recent_notes`: mock `storage.get_raw_notes`, assert grouped-by-tag structure returned
- `execute_get_recent_notes` with empty notes: assert fallback message returned
- `execute_summarize_board`: assert formatted bullet list appears in output, no `--- Full list ---`

Run: `python3 -m pytest test_grug.py -q -k "not test_16"` — all 35 tests must still pass.
