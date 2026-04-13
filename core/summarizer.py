"""Summarization engine for Grug.

Provides three distinct summarization operations:
1. Daily FIFO — compress daily notes into high-density summaries.
2. Prune auto-offload — summarize pruned conversation turns.
3. Idle session compaction — summarize idle conversations for archival.
"""

import os
import glob
import requests
from core.storage import GrugStorage


class Summarizer:
    """LLM-powered summarization for the Memory Pyramid."""

    def __init__(self, storage: GrugStorage, ollama_host: str, model_name: str):
        """
        Args:
            storage: GrugStorage instance for appending to daily notes.
            ollama_host: Ollama API base URL (e.g. ``http://localhost:11434``).
            model_name: Model identifier (e.g. ``gemma:2b``).
        """
        self.storage = storage
        self.ollama_host = ollama_host
        self.model_name = model_name

    # ------------------------------------------------------------------
    # Internal LLM helper
    # ------------------------------------------------------------------

    def _call_llm_text(self, prompt: str) -> str:
        """Make a plain-text (non-JSON) LLM call via ``/api/generate``.

        Returns the response text, or ``""`` on any error.
        Uses a 60-second timeout since summarization can be slow.
        """
        url = f"{self.ollama_host.rstrip('/')}/api/generate"
        payload = {
            "model": self.model_name,
            "prompt": prompt,
            "stream": False,
        }
        try:
            response = requests.post(url, json=payload, timeout=60)
            response.raise_for_status()
            return response.json().get("response", "").strip()
        except Exception as e:
            print(f"[summarizer] LLM call failed: {e}")
            return ""

    # ------------------------------------------------------------------
    # 1. Daily FIFO Summarization (boot + nightly cron)
    # ------------------------------------------------------------------

    def summarize_daily_notes(
        self,
        summaries_dir: str,
        daily_notes_dir: str,
        threshold_bytes: int,
        days_limit: int,
    ):
        """Compress daily notes into high-density professional summaries.

        - Skips files smaller than ``threshold_bytes``.
        - Skips days that already have a summary file.
        - Prunes old summaries to maintain exactly ``days_limit`` files.
        - Idempotent: safe to re-run at any time.
        """
        os.makedirs(summaries_dir, exist_ok=True)

        # List all daily note files
        md_files = sorted(glob.glob(os.path.join(daily_notes_dir, "*.md")))

        for file_path in md_files:
            basename = os.path.basename(file_path)          # YYYY-MM-DD.md
            date_str = basename.replace(".md", "")           # YYYY-MM-DD
            summary_path = os.path.join(summaries_dir, f"{date_str}.summary.md")

            # Skip if summary already exists
            if os.path.exists(summary_path):
                continue

            # Skip if file is too small
            file_size = os.path.getsize(file_path)
            if file_size < threshold_bytes:
                continue

            # Read the raw daily notes
            try:
                with open(file_path, "r", encoding="utf-8") as f:
                    content = f.read()
            except OSError as e:
                print(f"[summarizer] Failed to read {file_path}: {e}")
                continue

            # Summarize via LLM
            prompt = (
                "Summarize the following daily log entries into high-density "
                "professional bullet points. No caveman voice. Be concise and "
                "factual. Output ONLY bullet points, each starting with '- '.\n\n"
                f"DAILY LOG:\n{content}\n\nSUMMARY:"
            )
            summary = self._call_llm_text(prompt)
            if not summary:
                print(f"[summarizer] LLM returned empty summary for {date_str}, skipping.")
                continue

            # Write summary file
            try:
                with open(summary_path, "w", encoding="utf-8") as f:
                    f.write(summary + "\n")
                print(f"[summarizer] Created summary for {date_str}")
            except OSError as e:
                print(f"[summarizer] Failed to write {summary_path}: {e}")

        # Prune: keep only the newest `days_limit` summary files
        self._prune_summaries(summaries_dir, days_limit)

    def _prune_summaries(self, summaries_dir: str, days_limit: int):
        """Delete the oldest summary files beyond ``days_limit``."""
        summary_files = sorted(
            glob.glob(os.path.join(summaries_dir, "*.summary.md")),
            reverse=True,  # newest first
        )
        for old_file in summary_files[days_limit:]:
            try:
                os.remove(old_file)
                print(f"[summarizer] Pruned old summary: {os.path.basename(old_file)}")
            except OSError as e:
                print(f"[summarizer] Failed to prune {old_file}: {e}")

    # ------------------------------------------------------------------
    # 2. Prune Auto-Offload (called during context pruning)
    # ------------------------------------------------------------------

    def summarize_pruned_turns(self, turns_text: str) -> str:
        """Summarize pruned conversation turns for auto-offload to the Truth Layer.

        Returns the summary string, or ``""`` on LLM failure.
        The caller formats the result as a bullet and appends via
        ``storage.append_log("auto-offload", ...)``.
        """
        prompt = (
            "Summarize the key facts from this conversation excerpt. "
            "Output ONLY a single concise bullet point suitable for a log entry. "
            "No caveman voice. Be factual.\n\n"
            f"CONVERSATION:\n{turns_text}\n\nSUMMARY:"
        )
        try:
            return self._call_llm_text(prompt)
        except Exception as e:
            print(f"[summarizer] prune offload failed: {e}")
            return ""

    # ------------------------------------------------------------------
    # 3. Idle Session Compaction
    # ------------------------------------------------------------------

    def summarize_session_for_compaction(self, messages: list) -> str:
        """Summarize an idle session's conversation for compaction to the Truth Layer.

        Converts the message list to a readable transcript, then summarizes.
        Returns the summary string, or ``""`` on LLM failure.
        """
        # Convert messages to readable transcript
        transcript_lines = []
        for msg in messages:
            role = msg.get("role", "unknown").upper()
            content = msg.get("content", "")
            transcript_lines.append(f"{role}: {content}")
        transcript = "\n".join(transcript_lines)

        if not transcript.strip():
            return ""

        prompt = (
            "Summarize this Slack conversation into high-density professional "
            "bullet points. No caveman voice. Output ONLY bullet points, each "
            "starting with '- '.\n\n"
            f"CONVERSATION:\n{transcript}\n\nSUMMARY:"
        )
        try:
            return self._call_llm_text(prompt)
        except Exception as e:
            print(f"[summarizer] session compaction failed: {e}")
            return ""
