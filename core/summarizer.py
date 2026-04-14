"""Summarization engine for Grug.

Provides three distinct summarization operations:
1. Daily FIFO — compress daily notes into high-density summaries.
2. Prune auto-offload — summarize pruned conversation turns.
3. Idle session compaction — summarize idle conversations for archival.
"""

import os
import glob


class Summarizer:
    """LLM-powered summarization for the Memory Pyramid."""

    def __init__(self, storage, llm_client):
        self.storage = storage
        self.llm_client = llm_client

    # ------------------------------------------------------------------
    # 1. Daily FIFO Summarization (boot + nightly cron)
    # ------------------------------------------------------------------

    def summarize_daily_notes(self, summaries_dir, daily_notes_dir,
                              threshold_bytes, days_limit):
        """Compress daily notes into high-density professional summaries."""
        os.makedirs(summaries_dir, exist_ok=True)

        md_files = sorted(glob.glob(os.path.join(daily_notes_dir, "*.md")))

        for file_path in md_files:
            basename = os.path.basename(file_path)
            date_str = basename.replace(".md", "")
            summary_path = os.path.join(summaries_dir, f"{date_str}.summary.md")

            if os.path.exists(summary_path):
                continue

            file_size = os.path.getsize(file_path)
            if file_size < threshold_bytes:
                continue

            try:
                with open(file_path, "r", encoding="utf-8") as f:
                    content = f.read()
            except OSError as e:
                print(f"[summarizer] Failed to read {file_path}: {e}")
                continue

            prompt = (
                "Summarize the following daily log entries into high-density "
                "professional bullet points. No caveman voice. Be concise and "
                "factual. Output ONLY bullet points, each starting with '- '.\n\n"
                f"DAILY LOG:\n{content}\n\nSUMMARY:"
            )
            summary = self.llm_client.generate(prompt)
            if not summary:
                print(f"[summarizer] LLM returned empty summary for {date_str}, skipping.")
                continue

            try:
                with open(summary_path, "w", encoding="utf-8") as f:
                    f.write(summary + "\n")
                print(f"[summarizer] Created summary for {date_str}")
            except OSError as e:
                print(f"[summarizer] Failed to write {summary_path}: {e}")

        self._prune_summaries(summaries_dir, days_limit)

    def _prune_summaries(self, summaries_dir, days_limit):
        summary_files = sorted(
            glob.glob(os.path.join(summaries_dir, "*.summary.md")),
            reverse=True,
        )
        for old_file in summary_files[days_limit:]:
            try:
                os.remove(old_file)
                print(f"[summarizer] Pruned old summary: {os.path.basename(old_file)}")
            except OSError as e:
                print(f"[summarizer] Failed to prune {old_file}: {e}")

    # ------------------------------------------------------------------
    # 2. Prune Auto-Offload
    # ------------------------------------------------------------------

    def summarize_pruned_turns(self, turns_text):
        prompt = (
            "Summarize the key facts from this conversation excerpt. "
            "Output ONLY a single concise bullet point suitable for a log entry. "
            "No caveman voice. Be factual.\n\n"
            f"CONVERSATION:\n{turns_text}\n\nSUMMARY:"
        )
        try:
            return self.llm_client.generate(prompt)
        except Exception as e:
            print(f"[summarizer] prune offload failed: {e}")
            return ""

    # ------------------------------------------------------------------
    # 3. Idle Session Compaction
    # ------------------------------------------------------------------

    def summarize_session_for_compaction(self, messages):
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
            return self.llm_client.generate(prompt)
        except Exception as e:
            print(f"[summarizer] session compaction failed: {e}")
            return ""
