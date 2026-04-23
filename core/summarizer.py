"""Summarization engine for Grug.

Provides three distinct summarization operations:
1. Daily FIFO — compress daily notes into high-density summaries.
2. Prune auto-offload — summarize pruned conversation turns.
3. Idle session compaction — summarize idle conversations for archival.

All methods return strings. Callers handle file I/O.
"""

import os
import glob


class Summarizer:
    """LLM-powered summarization for the Memory Pyramid."""

    def __init__(self, llm_client):
        self.llm_client = llm_client

    # ------------------------------------------------------------------
    # 1. Daily FIFO Summarization
    # ------------------------------------------------------------------

    def summarize_daily_notes(self, daily_notes_dir, summaries_dir, threshold_bytes):
        """Return list of (date_str, summary) for notes that need summarizing.

        Skips files that already have a summary or are below the size threshold.
        """
        results = []
        md_files = sorted(glob.glob(os.path.join(daily_notes_dir, "*.md")))

        for file_path in md_files:
            basename = os.path.basename(file_path)
            date_str = basename.replace(".md", "")
            summary_path = os.path.join(summaries_dir, f"{date_str}.summary.md")

            if os.path.exists(summary_path):
                continue

            if os.path.getsize(file_path) < threshold_bytes:
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

            results.append((date_str, summary))

        return results

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
    # 3. After Action Report (AAR)
    # ------------------------------------------------------------------

    def generate_aar(self, messages):
        """Generate an After Action Report from conversation messages.

        Returns the raw LLM output as a formatted report.
        """
        transcript_lines = []
        for msg in messages:
            role = msg.get("role", "unknown").upper()
            content = msg.get("content", "")
            if content:
                transcript_lines.append(f"{role}: {content}")
        transcript = "\n".join(transcript_lines)

        if not transcript.strip():
            return "No conversation content to review."

        prompt = (
            "Review this conversation between Grug and a user.\n\n"
            "## What Went Wrong\n"
            "List mistakes, user corrections, or misunderstandings. Be specific.\n\n"
            "## What To Remember\n"
            "For each finding, write a candidate instruction Grug should follow next time.\n"
            "Format each as: - #tag instruction\n"
            "Tags: tasks, notes, scheduling, conversation, general\n\n"
            "Output at most 5 candidate instructions. If nothing notable happened, "
            "say \"No issues found.\"\n\n"
            f"CONVERSATION:\n{transcript}"
        )
        try:
            return self.llm_client.generate(prompt)
        except Exception as e:
            print(f"[summarizer] AAR generation failed: {e}")
            return "AAR generation failed — LLM returned an error."

    # ------------------------------------------------------------------
    # 4. Idle Session Compaction
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
