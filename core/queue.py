"""Message queue for Grug.

Single global queue with configurable worker count. Workers drain all
messages for the active session before moving on to the next session,
keeping context warm and avoiding redundant setup.
"""

import threading
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Callable


@dataclass
class QueuedMessage:
    """A message waiting to be processed."""
    session_id: str
    text: str
    user_id: str
    metadata: dict = field(default_factory=dict)
    on_result: Any = None  # callback(event) when processing completes


class GrugMessageQueue:
    """Thread-safe message queue with session-draining workers.

    Messages are grouped by session_id. The active worker fully drains
    one session's messages before moving on to the next, so the LLM
    context (session, summaries, system prompt) only needs to be built
    once per session burst.
    """

    def __init__(self, process_fn: Callable, worker_count: int = 1):
        self._queue: deque[QueuedMessage] = deque()
        self._lock = threading.Lock()
        self._not_empty = threading.Condition(self._lock)
        self._process_fn = process_fn
        self._worker_count = worker_count
        self._workers: list[threading.Thread] = []

    @property
    def worker_count(self) -> int:
        """Number of configured worker threads."""
        return self._worker_count

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
        """Add a message to the queue."""
        with self._not_empty:
            self._queue.append(msg)
            self._not_empty.notify()

    def _worker_loop(self):
        """Pick a session, drain all its queued messages, repeat."""
        while True:
            batch = self._take_next_thread_batch()
            self._process_batch(batch)

    def _take_next_thread_batch(self) -> list[QueuedMessage]:
        """Wait for messages and return all messages for the next session."""
        with self._not_empty:
            while not self._queue:
                self._not_empty.wait()

            # Pick the session_id of the first message in queue
            target_session = self._queue[0].session_id

            # Pull all messages for that session
            batch = []
            remaining = deque()
            for msg in self._queue:
                if msg.session_id == target_session:
                    batch.append(msg)
                else:
                    remaining.append(msg)
            self._queue.clear()
            self._queue.extend(remaining)

            return batch

    def _process_batch(self, batch: list[QueuedMessage]):
        """Process a batch of messages for the same session sequentially."""
        for msg in batch:
            result = None
            try:
                result = self._process_fn(msg)
            except Exception as e:
                print(f"[grug-queue] error processing message: {e}")
            finally:
                if msg.on_result:
                    try:
                        msg.on_result(result)
                    except Exception as cb_err:
                        print(f"[grug-queue] error in on_result callback: {cb_err}")
