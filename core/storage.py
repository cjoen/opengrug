import os
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
        
    def append_log(self, tool_name: str, payload: dict):
        """Append an event to today's log."""
        target_file = self._get_daily_file()
        timestamp = datetime.now().strftime("%H:%M:%S")
        
        # Format the block: e.g., "- 14:32:01 [insight] The sky is blue"
        payload_str = ", ".join(f"{k}: {v}" for k, v in payload.items())
        line = f"- {timestamp} [{tool_name}] {payload_str}\n"
        
        with open(target_file, "a", encoding="utf-8") as f:
            f.write(line)
        return True
