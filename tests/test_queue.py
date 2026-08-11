import unittest

from aipool.domain import Strategy, TaskEnvelope, TaskOutcome
from aipool.queue import QueueFull, TaskQueue
from aipool.storage import Store


class QueueTests(unittest.TestCase):
    def setUp(self) -> None:
        self.store = Store()
        self.addCleanup(self.store.close)
        self.queue = TaskQueue(self.store, max_pending=1)
        self.task = TaskEnvelope(task="classification", input_ref="artifact:q", local_estimate=1)

    def test_enqueue_is_idempotent_and_bounded(self) -> None:
        first = self.queue.enqueue(self.task, idempotency_key="same")
        duplicate = self.queue.enqueue(self.task, idempotency_key="same")
        self.assertEqual(first.task_id, duplicate.task_id)
        with self.assertRaises(QueueFull):
            self.queue.enqueue(TaskEnvelope(task="classification", input_ref="artifact:other", local_estimate=1), idempotency_key="other")

    def test_expired_lease_can_be_reclaimed_and_completed(self) -> None:
        record = self.queue.enqueue(self.task, idempotency_key="lease")
        claimed = self.queue.claim("worker-a", lease_seconds=10, now=100)
        self.assertEqual(claimed.task_id, record.task_id)
        self.assertEqual(self.queue.claim("worker-b", lease_seconds=10, now=105), None)
        reclaimed = self.queue.claim("worker-b", lease_seconds=10, now=111)
        self.assertEqual(reclaimed.lease_id, self.queue.get(record.task_id).lease_id)
        outcome = TaskOutcome(record.task_id, Strategy.SINGLE, "p", "ok", True, True)
        self.assertTrue(self.queue.complete(record.task_id, reclaimed.lease_id, outcome, now=112))
        self.assertEqual(self.queue.get(record.task_id).status, "succeeded")
        self.assertEqual(self.queue.get(record.task_id).outcome.output, "ok")

    def test_queued_task_can_be_cancelled_and_wrong_lease_cannot_complete(self) -> None:
        record = self.queue.enqueue(self.task, idempotency_key="cancel")
        self.assertTrue(self.queue.cancel(record.task_id, now=100))
        self.assertEqual(self.queue.get(record.task_id).status, "cancelled")
        self.assertIsNone(self.queue.claim("worker", lease_seconds=10, now=101))
        outcome = TaskOutcome(record.task_id, Strategy.SINGLE, "p", "ok", True, True)
        self.assertFalse(self.queue.complete(record.task_id, "wrong", outcome, now=102))

    def test_running_cancellation_is_cooperative(self) -> None:
        record = self.queue.enqueue(self.task, idempotency_key="running-cancel")
        claimed = self.queue.claim("worker", lease_seconds=10, now=100)
        self.assertTrue(self.queue.cancel(record.task_id, now=101))
        outcome = TaskOutcome(record.task_id, Strategy.SINGLE, "p", "ok", True, True)
        self.assertFalse(self.queue.complete(record.task_id, claimed.lease_id, outcome, now=102))
        self.assertEqual(self.queue.get(record.task_id).status, "cancelled")


if __name__ == "__main__":
    unittest.main()
