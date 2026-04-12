import os
import glob
from datetime import datetime

class GrugStorage:
    def __init__(self, base_dir="/app/brain"):
        self.base_dir = base_dir
        self.daily_notes_dir = os.path.join(base_dir, "daily_notes")
        os.makedirs(self.daily_notes_dir, exist_ok=True)
        
    def _get_daily_file(self):
        """Returns the path to the daily markdown log."""
        today_str = datetime.now().strftime("%Y-%m-%d")
        return os.path.join(self.daily_notes_dir, f"{today_str}.md")
        
    def append_log(self, tool_name: str, text: str):
        """Append an event to today's log."""
        target_file = self._get_daily_file()
        timestamp = datetime.now().strftime("%H:%M:%S")
        
        # Markdown convention: Bullet points are our atomic block size
        line = f"- {timestamp} [{tool_name}] {text}\n"
        
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
        return f"Note added successfully."

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
