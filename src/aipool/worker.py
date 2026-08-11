"""Bounded background execution for durable queue records."""

from __future__ import annotations

import threading
import time

from .queue import TaskQueue
from .domain import Strategy, TaskOutcome
from .service import Coordinator


class QueueWorker:
    def __init__(
        self,
        queue: TaskQueue,
        coordinator: Coordinator,
        *,
        worker_id: str = "worker-1",
        lease_seconds: float = 60.0,
        poll_seconds: float = 0.1,
        clock=time.time,
    ) -> None:
        if not worker_id or lease_seconds <= 0 or poll_seconds < 0:
            raise ValueError("worker_id, lease_seconds, and poll_seconds are invalid")
        self.queue = queue
        self.coordinator = coordinator
        self.worker_id = worker_id
        self.lease_seconds = lease_seconds
        self.poll_seconds = poll_seconds
        self.clock = clock

    def run_once(self, *, now: float | None = None) -> bool:
        current = self.clock() if now is None else now
        record = self.queue.claim(self.worker_id, lease_seconds=self.lease_seconds, now=current)
        if record is None:
            return False
        latest = self.queue.get(record.task_id)
        if latest is not None and latest.cancel_requested:
            self.queue.cancel_claimed(record.task_id, record.lease_id or "", now=self.clock())
            return True
        try:
            outcome = self.coordinator.submit(record.task)
        except Exception:
            # Keep one bad invocation from killing supervision or hiding the
            # task until its lease expires.
            outcome = TaskOutcome(
                task_id=record.task_id, strategy=Strategy.SINGLE, provider_id=None,
                output=None, success=False, valid=False, reason="worker_exception",
            )
        self.queue.complete(record.task_id, record.lease_id or "", outcome, now=self.clock())
        return True

    def run_forever(self, stop_event: threading.Event) -> None:
        while not stop_event.is_set():
            if not self.run_once():
                stop_event.wait(self.poll_seconds)
