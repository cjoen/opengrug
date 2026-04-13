import os
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
        
        success_msg = "Note added successfully."
        if tags:
            success_msg += f" Tags: {', '.join(tags)}"
        return success_msg

    def get_recent_notes(self, limit: int = 10) -> str:
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
