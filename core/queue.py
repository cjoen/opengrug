"""Message queue for Grug.

Single global queue with configurable worker count. Workers drain all
messages for the active thread before moving on to the next thread,
keeping context warm and avoiding redundant setup.

Reactions:
  📬 (mailbox_with_mail) — message received / queued
  💭 (thought_balloon) — message currently being processed
"""

import threading
from collections import deque, OrderedDict
from dataclasses import dataclass, field
from typing import Any, Callable, Optional


@dataclass
class QueuedMessage:
    """A message waiting to be processed."""
    thread_ts: str
    channel_id: str
    ts: str  # individual message timestamp (for reactions)
    user_id: str
    text: str
    client: Any  # Slack client


class GrugMessageQueue:
    """Thread-safe message queue with thread-draining workers.

    Messages are grouped by thread_ts. The active worker fully drains
    one thread's messages before moving on to the next, so the LLM
    context (session, summaries, system prompt) only needs to be built
    once per thread burst.
    """

    def __init__(self, process_fn: Callable, worker_count: int = 1):
        self._queue: deque[QueuedMessage] = deque()
        self._lock = threading.Lock()
        self._not_empty = threading.Condition(self._lock)
        self._process_fn = process_fn
        self._worker_count = worker_count
        self._workers: list[threading.Thread] = []

    def start(self):
        """Start worker threads."""
        for i in range(self._worker_count):
            t = threading.Thread(
                target=self._worker_loop,
                name=f"grug-worker-{i}",
                daemon=True,
            )
            t.start()
            self._workers.append(t)

    def enqueue(self, msg: QueuedMessage):
        """Add a message and add 👀 reaction to acknowledge receipt."""
        try:
            msg.client.reactions_add(
                channel=msg.channel_id, timestamp=msg.ts, name="mailbox_with_mail"
            )
        except Exception:
            pass

        with self._not_empty:
            self._queue.append(msg)
            self._not_empty.notify()

    def _worker_loop(self):
        """Pick a thread, drain all its queued messages, repeat."""
        while True:
            batch = self._take_next_thread_batch()
            self._process_batch(batch)

    def _take_next_thread_batch(self) -> list[QueuedMessage]:
        """Wait for messages and return all messages for the next thread."""
        with self._not_empty:
            while not self._queue:
                self._not_empty.wait()

            # Pick the thread_ts of the first message in queue
            target_thread = self._queue[0].thread_ts

            # Pull all messages for that thread
            batch = []
            remaining = deque()
            for msg in self._queue:
                if msg.thread_ts == target_thread:
                    batch.append(msg)
                else:
                    remaining.append(msg)
            self._queue.clear()
            self._queue.extend(remaining)

            return batch

    def _process_batch(self, batch: list[QueuedMessage]):
        """Process a batch of messages for the same thread sequentially."""
        for msg in batch:
            # Swap reactions: remove 📬, add 💭
            try:
                msg.client.reactions_remove(
                    channel=msg.channel_id, timestamp=msg.ts, name="mailbox_with_mail"
                )
            except Exception:
                pass
            try:
                msg.client.reactions_add(
                    channel=msg.channel_id, timestamp=msg.ts, name="thought_balloon"
                )
            except Exception:
                pass

            try:
                self._process_fn(msg)
            except Exception as e:
                print(f"[grug-queue] error processing message: {e}")

            # Remove 💭 when done
            try:
                msg.client.reactions_remove(
                    channel=msg.channel_id, timestamp=msg.ts, name="thought_balloon"
                )
            except Exception:
                pass
