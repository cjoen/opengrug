import os
import re
import json
import glob
import threading
from datetime import datetime

class GrugStorage:
    def __init__(self, base_dir="/app/brain"):
        self.base_dir = base_dir
        self.daily_notes_dir = os.path.join(base_dir, "daily_notes")
        os.makedirs(self.daily_notes_dir, exist_ok=True)
        self._write_lock = threading.Lock()
        
    def _get_daily_file(self):
        """Returns the path to the daily markdown log."""
        today_str = datetime.now().strftime("%Y-%m-%d")
        return os.path.join(self.daily_notes_dir, f"{today_str}.md")

    def get_daily_file_for_date(self, date_str: str) -> str:
        """Returns the path to a specific day's markdown log.
        
        Args:
            date_str: Date in YYYY-MM-DD format.
        """
        return os.path.join(self.daily_notes_dir, f"{date_str}.md")
        
    def append_log(self, source: str, text: str):
        """Append an event to today's log. Thread-safe.
        
        Format: ``- HH:MM:SS [source] text``
        """
        target_file = self._get_daily_file()
        timestamp = datetime.now().strftime("%H:%M:%S")
        
        # Markdown convention: Bullet points are our atomic block size
        line = f"- {timestamp} [{source}] {text}\n"
        
        with self._write_lock:
            with open(target_file, "a", encoding="utf-8") as f:
                f.write(line)
        return True
        
    def add_note(self, content: str, tags: list = None):
        """Write a generic note to the log."""
        content = content.replace("</untrusted_context>", "[context_tag_stripped]")
        tag_str = ""
        if tags:
            tag_str = " " + " ".join(f"#{t}" for t in tags)
        
        self.append_log("note", f"{content}{tag_str}")
        return ""

    def get_raw_notes(self, limit: int = 10) -> str:
        """Fetch the most recent events sequentially from the daily logs."""
        md_files = sorted(glob.glob(os.path.join(self.daily_notes_dir, "*.md")), reverse=True)
        lines = []
        
        for file_path in md_files:
            if len(lines) >= limit:
                break
            with open(file_path, "r", encoding="utf-8") as f:
                file_lines = f.readlines()
                # Read from newest to oldest line in the file
                file_lines.reverse()
                for line in file_lines:
                    if line.startswith("- "):
                        lines.append(line.strip())
                    if len(lines) >= limit:
                        break
                        
        lines.reverse() # Sort back to chronological for the context window
        return "\n".join(lines)

    def reformat_daily_file(self, date_str: str, summary: str):
        """Rewrite a daily notes file in Obsidian-friendly format.

        Structure:
            # YYYY-MM-DD
            ## Summary
            <summary bullets>
            ---
            ## Log
            **Notes**
            - HH:MM — content
            **Activity**
            - HH:MM — [source] content
        """
        file_path = self.get_daily_file_for_date(date_str)
        if not os.path.exists(file_path):
            return

        with open(file_path, "r", encoding="utf-8") as f:
            raw_lines = f.readlines()

        notes = []
        activity = []
        freeform = []
        for line in raw_lines:
            stripped = line.strip()
            if not stripped:
                continue
            # Parse: - HH:MM:SS [source] content
            m = re.match(r"^- (\d{2}:\d{2}):\d{2} \[([^\]]+)\] (.+)$", stripped)
            if not m:
                if not stripped.startswith("- "):
                    continue
                freeform.append(stripped)
                continue
            time_str, source, content = m.group(1), m.group(2), m.group(3)
            if source == "note":
                notes.append(f"- {time_str} — {content}")
            else:
                activity.append(f"- {time_str} — [{source}] {content}")

        parts = [f"# {date_str}", "", "## Summary", summary.strip()]
        if freeform:
            parts += ["", "---", ""] + freeform
        parts += ["", "---", "", "## Log"]
        if notes:
            parts += ["", "**Notes**"] + notes
        if activity:
            parts += ["", "**Activity**"] + activity

        with self._write_lock:
            with open(file_path, "w", encoding="utf-8") as f:
                f.write("\n".join(parts) + "\n")

    def log_routing_trace(self, user_message, thinking, actions):
        """Append a routing trace entry to brain/routing_trace.jsonl."""
        try:
            trace_entry = json.dumps({
                "ts": datetime.now().isoformat(),
                "user_msg": user_message[:200],
                "thinking": thinking[:500] if thinking else "",
                "actions": [{"tool": a.get("tool"), "args": a.get("arguments", {})} for a in actions],
            })
            trace_path = os.path.join(self.base_dir, "routing_trace.jsonl")
            with self._write_lock:
                with open(trace_path, "a", encoding="utf-8") as tf:
                    tf.write(trace_entry + "\n")
        except Exception:
            pass

    def get_capped_tail(self, max_lines: int = 100) -> str:
        """Read the LAST ``max_lines`` lines from today's daily note file.

        Returns the lines joined as a string, or empty string if no file exists.
        Used for the "Capped Tail" context injection (§5.1 step 3).
        """
        target_file = self._get_daily_file()
        if not os.path.exists(target_file):
            return ""

        with open(target_file, "r", encoding="utf-8") as f:
            all_lines = f.readlines()

        tail = all_lines[-max_lines:] if len(all_lines) > max_lines else all_lines
        return "".join(tail).rstrip("\n")
