"""Search tool for Grug — plain text search across all markdown,
with vector memory fallback for vague queries."""

import os
import re
import glob


def _format_hit(fname, line):
    """Clean up a raw log line into a readable search result.

    Format:
        📂 daily_notes/2026-04-10.md · 15:32
          content of the matching line
    """
    line = line.strip()
    time_str = ""
    # Extract time from daily log format: "- HH:MM:SS [source] content"
    m = re.match(r"^- (\d{2}:\d{2}):\d{2} \[[^\]]+\] (.+)$", line)
    if m:
        time_str = f" · {m.group(1)}"
        line = m.group(2)
    else:
        # Strip leading bullet if present
        line = re.sub(r"^-\s+", "", line)

    return f"📂 {fname}{time_str}\n  {line}"


def search(base_dir, query, vector_memory=None, limit=5):
    """Search all markdown files in brain/ for lines matching query.

    Falls back to vector semantic search if grep finds nothing.
    """
    query_lower = query.lower()
    results = []

    md_files = sorted(
        glob.glob(os.path.join(base_dir, "**", "*.md"), recursive=True),
        reverse=True,
    )

    for file_path in md_files:
        with open(file_path, "r", encoding="utf-8") as f:
            for line in f:
                if query_lower in line.lower():
                    fname = os.path.relpath(file_path, base_dir)
                    results.append(_format_hit(fname, line))
                    if len(results) >= limit:
                        break
        if len(results) >= limit:
            break

    if results:
        header = f"Found {len(results)} result{'s' if len(results) != 1 else ''} for \"{query}\":\n\n"
        return header + "\n\n".join(results)

    # Grep found nothing — try vector search as fallback
    if vector_memory is not None:
        try:
            hits = vector_memory.query_memory_raw(query, limit=limit)
            if hits and not hits[0].get("offline"):
                lines = [f"• {h['content']}" for h in hits]
                return f"No exact matches for \"{query}\". Similar notes:\n" + "\n".join(lines)
        except Exception:
            pass

    return f"No results found for '{query}'."
