"""Background workers for Grug."""

import os
import glob
import time
from datetime import datetime, timedelta

from core.task import Task, TaskPriority


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
                ts = sess["session_id"]
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


def scheduler_poll_loop(schedule_store, task_queue, config, deliver_fn=None):
    """Poll for due scheduled jobs and enqueue them as URGENT Tasks.

    Each due job becomes a Task whose metadata carries the pre-validated tool
    name/arguments. The orchestrator runs the tool deterministically via the
    registry (no LLM round-trip). ``deliver_fn(channel, thread_ts, text)`` is
    invoked with the result; pass ``None`` to disable delivery (used in tests).
    """
    interval = config.scheduler.poll_interval_seconds
    while True:
        time.sleep(interval)
        try:
            due = schedule_store.get_due()
            for job in due:
                task = _build_scheduled_task(job, deliver_fn)
                try:
                    task_queue.enqueue(task)
                except Exception as e:
                    print(f"[scheduler] failed to enqueue job {job['id']}: {e}")
                    continue

                if job["is_recurring"]:
                    schedule_store.advance(job["id"], job["schedule"])
                else:
                    schedule_store.delete(job["id"])
        except Exception as e:
            print(f"[scheduler] poll error: {e}")


def _build_scheduled_task(job: dict, deliver_fn=None) -> Task:
    """Build an URGENT Task carrying a deterministic scheduled-tool payload.

    The orchestrator detects ``metadata['scheduled_tool']`` and runs the tool
    directly through the registry, bypassing the LLM. The ``on_result``
    callback hands the formatted output to ``deliver_fn``.
    """
    desc = job["description"] or job["tool_name"]
    channel = job.get("channel")
    thread_ts = job.get("thread_ts")

    def _on_result(event):
        if deliver_fn is None or event is None:
            return
        text = getattr(event, "text", None) or str(event)
        try:
            deliver_fn(channel, thread_ts, text)
        except Exception as e:
            print(f"[scheduler] deliver_fn failed: {e}")

    return Task(
        session_id=f"scheduled-{job['id']}",
        user_id="grug",
        agent_name="chat_agent",
        context=desc,
        priority=TaskPriority.URGENT,
        metadata={
            "scheduled_tool": {
                "name": job["tool_name"],
                "arguments": job["arguments"],
                "description": desc,
            },
            "channel_id": channel,
            "thread_ts": thread_ts,
            "platform": "scheduled",
        },
        on_result=_on_result,
    )


def nightly_grug_tasks_loop(grug_task_queue, task_queue, storage, config):
    """Once per night, drain pending grug-tasks into the priority queue as
    BACKGROUND tasks. The off-hours window in the priority queue handles the
    actual dispatch timing; we just produce work."""
    while True:
        now = datetime.now()
        tomorrow_3am = (now + timedelta(days=1)).replace(
            hour=3, minute=0, second=0, microsecond=0
        )
        if now.hour < 3:
            tomorrow_3am = now.replace(hour=3, minute=0, second=0, microsecond=0)
        sleep_seconds = (tomorrow_3am - now).total_seconds()
        time.sleep(sleep_seconds)

        try:
            pending = grug_task_queue.get_pending()
            limit = getattr(config.grug_tasks, 'nightly_limit', 5)
            run_ts = int(time.time())

            for i, (_task_num, description) in enumerate(pending):
                if i >= limit:
                    print(f"[grug-tasks] hit nightly limit ({limit}), stopping")
                    break

                # Capture loop-bound vars for the callback closure.
                position = 1  # Always complete #1 — list shifts up after each completion
                desc = description

                def _on_result(_event, desc=desc):
                    try:
                        grug_task_queue.complete_task(position)
                        storage.append_log("grug-task", f"Processed: {desc}")
                    except Exception as e:
                        print(f"[grug-tasks] complete_task failed for '{desc}': {e}")

                t = Task(
                    session_id=f"grug-task-{run_ts}-{i}",
                    user_id="grug",
                    agent_name="chat_agent",
                    context=desc,
                    priority=TaskPriority.BACKGROUND,
                    metadata={"platform": "background", "raw_text": desc},
                    on_result=_on_result,
                )
                try:
                    task_queue.enqueue(t)
                    print(f"[grug-tasks] enqueued: {desc}")
                except Exception as e:
                    print(f"[grug-tasks] enqueue failed for '{desc}': {e}")
                    storage.append_log("grug-task", f"Enqueue failed: {desc} — {e}")
                    break

        except Exception as e:
            print(f"[grug-tasks] nightly loop error: {e}")
