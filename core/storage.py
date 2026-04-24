import os
import re
import json
import glob
import threading
from datetime import datetime
from core.utils import _sanitize_untrusted

class GrugStorage:
    def __init__(self, base_dir="/app/brain"):
        self.base_dir = base_dir
        self.daily_notes_dir = os.path.join(base_dir, "daily_notes")
        self.daily_logs_dir = os.path.join(base_dir, "daily_logs")
        os.makedirs(self.daily_notes_dir, exist_ok=True)
        os.makedirs(self.daily_logs_dir, exist_ok=True)
        self._write_lock = threading.Lock()

    def _get_daily_note_file(self):
        """Returns the path to today's notes file."""
        today_str = datetime.now().strftime("%Y-%m-%d")
        return os.path.join(self.daily_notes_dir, f"{today_str}.md")

    def _get_daily_log_file(self):
        """Returns the path to today's activity log file."""
        today_str = datetime.now().strftime("%Y-%m-%d")
        return os.path.join(self.daily_logs_dir, f"{today_str}.md")

    def get_daily_log_for_date(self, date_str: str) -> str:
        """Returns the path to a specific day's log file."""
        return os.path.join(self.daily_logs_dir, f"{date_str}.md")
        
    def append_log(self, source: str, text: str):
        """Append an event to today's activity log. Thread-safe."""
        text = _sanitize_untrusted(text, "untrusted_context")
        target_file = self._get_daily_log_file()
        timestamp = datetime.now().strftime("%H:%M:%S")
        line = f"- {timestamp} [{source}] {text}\n"
        with self._write_lock:
            with open(target_file, "a", encoding="utf-8") as f:
                f.write(line)
        return True

    def add_note(self, content: str, tags: list = None):
        """Write a note to today's daily notes file."""
        content = _sanitize_untrusted(content, "untrusted_context")
        tag_str = ""
        if tags:
            tag_str = " " + " ".join(f"#{t}" for t in tags)
        timestamp = datetime.now().strftime("%H:%M:%S")
        line = f"- {timestamp} {content}{tag_str}\n"
        target_file = self._get_daily_note_file()
        with self._write_lock:
            with open(target_file, "a", encoding="utf-8") as f:
                f.write(line)
        return ""

    def get_raw_notes(self, limit: int = 10) -> str:
        """Fetch the most recent notes from daily_notes/."""
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
        """Rewrite a daily log file with a summary section at the top.

        Structure:
            # YYYY-MM-DD
            ## Summary
            <summary bullets>
            ---
            ## Log
            - HH:MM — [source] content
        """
        file_path = self.get_daily_log_for_date(date_str)
        if not os.path.exists(file_path):
            return

        with open(file_path, "r", encoding="utf-8") as f:
            raw_lines = f.readlines()

        entries = []
        for line in raw_lines:
            stripped = line.strip()
            if not stripped:
                continue
            m = re.match(r"^- (\d{2}:\d{2}):\d{2} \[([^\]]+)\] (.+)$", stripped)
            if m:
                time_str, source, content = m.group(1), m.group(2), m.group(3)
                entries.append(f"- {time_str} — [{source}] {content}")
            elif stripped.startswith("- "):
                entries.append(stripped)

        parts = [f"# {date_str}", "", "## Summary", summary.strip()]
        if entries:
            parts += ["", "---", "", "## Log"] + entries

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

    # ------------------------------------------------------------------
    # Instruction methods (brain/memory.md)
    # ------------------------------------------------------------------

    VALID_TAGS = {"tasks", "notes", "scheduling", "conversation", "general"}

    def _instructions_path(self):
        return os.path.join(self.base_dir, "memory.md")

    def get_instructions(self) -> list:
        """Parse brain/memory.md and return list of {tag, text} dicts."""
        path = self._instructions_path()
        if not os.path.exists(path):
            return []
        with open(path, "r", encoding="utf-8") as f:
            lines = f.readlines()
        instructions = []
        for line in lines:
            m = re.match(r"^- #(\w+)\s+(.+)$", line.strip())
            if m:
                instructions.append({"tag": m.group(1), "text": m.group(2)})
        return instructions

    def add_instruction(self, instruction: str, tag: str, max_chars: int) -> str:
        """Append an instruction. Returns status message."""
        instruction = instruction.strip()
        tag = tag.strip().lower()

        if tag not in self.VALID_TAGS:
            return f"Invalid tag '{tag}'. Valid tags: {', '.join(sorted(self.VALID_TAGS))}"
        if len(instruction) < 10:
            return "Instruction too short (minimum 10 characters)."
        if len(instruction) > 200:
            return "Instruction too long (maximum 200 characters)."

        existing = self.get_instructions()
        for item in existing:
            if instruction.lower() in item["text"].lower() or item["text"].lower() in instruction.lower():
                return f"Duplicate: already have a similar instruction (#{item['tag']} {item['text']})."

        total_chars = sum(len(i["text"]) for i in existing) + len(instruction)
        if total_chars > max_chars:
            return f"Budget exceeded: {total_chars}/{max_chars} chars. Remove an instruction first."

        line = f"- #{tag} {instruction}\n"
        with self._write_lock:
            with open(self._instructions_path(), "a", encoding="utf-8") as f:
                f.write(line)
        return f"Instruction added: #{tag} {instruction}"

    def edit_instruction(self, number: int, instruction: str, tag: str = None) -> str:
        """Edit instruction at 1-based index. Returns status message."""
        instruction = instruction.strip()
        items = self.get_instructions()

        if number < 1 or number > len(items):
            return f"Invalid instruction number {number}. Have {len(items)} instructions."

        if len(instruction) < 10:
            return "Instruction too short (minimum 10 characters)."
        if len(instruction) > 200:
            return "Instruction too long (maximum 200 characters)."

        if tag is not None:
            tag = tag.strip().lower()
            if tag not in self.VALID_TAGS:
                return f"Invalid tag '{tag}'. Valid tags: {', '.join(sorted(self.VALID_TAGS))}"
        else:
            tag = items[number - 1]["tag"]

        # Dedup check against other instructions
        for i, item in enumerate(items):
            if i == number - 1:
                continue
            if instruction.lower() in item["text"].lower() or item["text"].lower() in instruction.lower():
                return f"Duplicate: already have a similar instruction (#{item['tag']} {item['text']})."

        items[number - 1] = {"tag": tag, "text": instruction}
        self._rewrite_instructions(items)
        return f"Instruction {number} updated: #{tag} {instruction}"

    def remove_instruction(self, number: int) -> str:
        """Remove instruction at 1-based index. Returns status message."""
        items = self.get_instructions()
        if number < 1 or number > len(items):
            return f"Invalid instruction number {number}. Have {len(items)} instructions."
        removed = items.pop(number - 1)
        self._rewrite_instructions(items)
        return f"Removed: #{removed['tag']} {removed['text']}"

    def get_instructions_block(self) -> str:
        """Return instructions grouped by tag for prompt injection. Empty string if none."""
        items = self.get_instructions()
        if not items:
            return ""
        groups = {}
        for item in items:
            groups.setdefault(item["tag"], []).append(item["text"])
        sections = []
        for tag, texts in groups.items():
            lines = f"[{tag.upper()}]\n"
            lines += "\n".join(f"- {t}" for t in texts)
            sections.append(lines)
        return "\n".join(sections)

    def _rewrite_instructions(self, items: list):
        """Rewrite brain/memory.md from a list of {tag, text} dicts."""
        target = self._instructions_path()
        tmp = target + ".tmp"
        with self._write_lock:
            with open(tmp, "w", encoding="utf-8") as f:
                for item in items:
                    f.write(f"- #{item['tag']} {item['text']}\n")
            os.replace(tmp, target)

    def get_capped_tail(self, max_lines: int = 100) -> str:
        """Read the LAST ``max_lines`` lines from today's activity log.

        Returns the lines joined as a string, or empty string if no file exists.
        """
        target_file = self._get_daily_log_file()
        if not os.path.exists(target_file):
            return ""

        with open(target_file, "r", encoding="utf-8") as f:
            all_lines = f.readlines()

        tail = all_lines[-max_lines:] if len(all_lines) > max_lines else all_lines
        return "".join(tail).rstrip("\n")
