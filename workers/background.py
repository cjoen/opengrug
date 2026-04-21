"""Background workers for Grug."""

import os
import glob
import time
from datetime import datetime, timedelta


def _run_summarization(summarizer, storage, config):
    """Generate summaries, write them, reformat daily files, prune old summaries."""
    summaries_dir = os.path.join(config.storage.base_dir, "summaries")
    daily_logs_dir = os.path.join(config.storage.base_dir, "daily_logs")
    os.makedirs(summaries_dir, exist_ok=True)

    results = summarizer.summarize_daily_notes(
        daily_notes_dir=daily_logs_dir,
        summaries_dir=summaries_dir,
        threshold_bytes=config.memory.summarization_threshold_bytes,
    )

    for date_str, summary in results:
        summary_path = os.path.join(summaries_dir, f"{date_str}.summary.md")
        try:
            with open(summary_path, "w", encoding="utf-8") as f:
                f.write(summary + "\n")
            print(f"[summarizer] Created summary for {date_str}")
        except OSError as e:
            print(f"[summarizer] Failed to write {summary_path}: {e}")
            continue

        storage.reformat_daily_file(date_str, summary)

    # Prune old summaries beyond the retention limit
    summary_files = sorted(
        glob.glob(os.path.join(summaries_dir, "*.summary.md")),
        reverse=True,
    )
    for old_file in summary_files[config.memory.summary_days_limit:]:
        try:
            os.remove(old_file)
            print(f"[summarizer] Pruned old summary: {os.path.basename(old_file)}")
        except OSError as e:
            print(f"[summarizer] Failed to prune {old_file}: {e}")


def boot_summarize(summarizer, storage, config):
    """Run daily note summarization on startup."""
    try:
        _run_summarization(summarizer, storage, config)
        print("[boot] daily note summarization complete")
    except Exception as e:
        print(f"[boot] summarization failed: {e}")


def idle_sweep_loop(session_store, summarizer, storage, config):
    """Compact idle sessions to the Truth Layer."""
    interval = config.memory.idle_sweep_interval_minutes * 60
    while True:
        time.sleep(interval)
        try:
            idle_sessions = session_store.get_idle_sessions(
                config.memory.thread_idle_timeout_hours
            )
            for sess in idle_sessions:
                ts = sess["thread_ts"]
                original_last_active = session_store.check_last_active(ts)

                messages = sess["messages"]
                if not messages:
                    session_store.delete_session(ts)
                    continue

                summary = summarizer.summarize_session_for_compaction(messages)
                if summary:
                    for line in summary.strip().split("\n"):
                        line = line.strip()
                        if line.startswith("- "):
                            line = line[2:]
                        if line:
                            storage.append_log("idle-compaction", line)

                current_last_active = session_store.check_last_active(ts)
                if current_last_active != original_last_active:
                    print(f"[idle-sweep] session {ts} became active during compaction, skipping deletion")
                    continue

                session_store.delete_session(ts)
                print(f"[idle-sweep] compacted and deleted session {ts}")

        except Exception as e:
            print(f"[idle-sweep] error: {e}")


def nightly_summarize_loop(summarizer, storage, config):
    """Run daily summarization once per night at midnight."""
    while True:
        now = datetime.now()
        tomorrow_midnight = (now + timedelta(days=1)).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        sleep_seconds = (tomorrow_midnight - now).total_seconds()
        time.sleep(sleep_seconds)
        try:
            _run_summarization(summarizer, storage, config)
            print(f"[nightly] daily note summarization complete")
        except Exception as e:
            print(f"[nightly] summarization failed: {e}")


def scheduler_poll_loop(schedule_store, registry, slack_client, config):
    """Poll for due scheduled tasks and execute them."""
    interval = config.scheduler.poll_interval_seconds
    while True:
        time.sleep(interval)
        try:
            due = schedule_store.get_due()
            for job in due:
                result = registry.execute(job["tool_name"], job["arguments"], skip_hitl=True)

                msg = result.output or "(no output)"
                desc = job["description"] or job["tool_name"]
                text = f"[Scheduled: {desc}] {msg}"

                try:
                    if slack_client:
                        kwargs = {"channel": job["channel"], "text": text}
                        if job.get("thread_ts"):
                            kwargs["thread_ts"] = job["thread_ts"]
                        slack_client.chat_postMessage(**kwargs)
                except Exception as e:
                    print(f"[scheduler] failed to post to Slack: {e}")

                if job["is_recurring"]:
                    schedule_store.advance(job["id"], job["schedule"])
                else:
                    schedule_store.delete(job["id"])

        except Exception as e:
            print(f"[scheduler] poll error: {e}")
