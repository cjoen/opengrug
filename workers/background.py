"""Background workers for Grug."""

import os
import time
from datetime import datetime


def boot_summarize(summarizer, config):
    """Run daily note summarization on startup."""
    try:
        summaries_dir = os.path.join(config.storage.base_dir, "summaries")
        daily_notes_dir = os.path.join(config.storage.base_dir, "daily_notes")
        summarizer.summarize_daily_notes(
            summaries_dir=summaries_dir,
            daily_notes_dir=daily_notes_dir,
            threshold_bytes=config.memory.summarization_threshold_bytes,
            days_limit=config.memory.summary_days_limit,
        )
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


def nightly_summarize_loop(summarizer, config):
    """Run daily summarization once per night around midnight."""
    last_run_date = None
    while True:
        time.sleep(60)
        now = datetime.now()
        today_str = now.strftime("%Y-%m-%d")
        if now.hour == 0 and last_run_date != today_str:
            last_run_date = today_str
            try:
                summaries_dir = os.path.join(config.storage.base_dir, "summaries")
                daily_notes_dir = os.path.join(config.storage.base_dir, "daily_notes")
                summarizer.summarize_daily_notes(
                    summaries_dir=summaries_dir,
                    daily_notes_dir=daily_notes_dir,
                    threshold_bytes=config.memory.summarization_threshold_bytes,
                    days_limit=config.memory.summary_days_limit,
                )
                print(f"[nightly] daily note summarization complete for {today_str}")
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
