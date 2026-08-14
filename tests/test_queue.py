import threading
import time
import unittest
from unittest import mock

from aipool.domain import Strategy, TaskEnvelope, TaskOutcome
from aipool.queue import QueueFull, TaskQueue
from aipool.storage import Store
from aipool.worker import QueueWorker


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

    def test_active_lease_can_be_renewed_during_long_provider_call(self) -> None:
        record = self.queue.enqueue(self.task, idempotency_key="renew")
        claimed = self.queue.claim("worker-a", lease_seconds=10, now=100)
        self.assertTrue(self.queue.renew(record.task_id, claimed.lease_id, lease_seconds=10, now=109))
        self.assertIsNone(self.queue.claim("worker-b", lease_seconds=10, now=110))
        self.assertIsNotNone(self.queue.claim("worker-b", lease_seconds=10, now=120))

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

    def test_worker_claims_submits_and_completes(self) -> None:
        self.queue.enqueue(self.task)
        outcome = TaskOutcome(self.task.task_id, Strategy.SINGLE, "p", "ok", True, True)
        submitted = []

        class Coordinator:
            def submit(_, task):
                submitted.append(task.task_id)
                return outcome

        worker = QueueWorker(self.queue, Coordinator(), worker_id="w", clock=lambda: 10.0)
        self.assertTrue(worker.run_once(now=10.0))
        record = self.queue.get(self.task.task_id)
        self.assertEqual(submitted, [self.task.task_id])
        self.assertEqual(record.status, "succeeded")
        self.assertEqual(record.outcome.output, "ok")

    def test_worker_skips_queued_cancellation(self) -> None:
        self.queue.enqueue(self.task)
        self.assertTrue(self.queue.cancel(self.task.task_id, now=10.0))
        submitted = []

        class Coordinator:
            def submit(_, task):
                submitted.append(task.task_id)
                raise AssertionError("cancelled task was submitted")

        worker = QueueWorker(self.queue, Coordinator(), worker_id="w", clock=lambda: 11.0)
        self.assertFalse(worker.run_once(now=11.0))
        self.assertEqual(submitted, [])

    def test_worker_run_forever_stops_without_leaking_thread(self) -> None:
        worker = QueueWorker(self.queue, mock.Mock(), poll_seconds=0.01)
        stop = threading.Event()
        thread = threading.Thread(target=worker.run_forever, args=(stop,))
        thread.start()
        time.sleep(0.03)
        stop.set()
        thread.join(timeout=1)
        self.assertFalse(thread.is_alive())

    def test_worker_records_provider_exception_as_failed_outcome(self) -> None:
        self.queue.enqueue(self.task)
        coordinator = mock.Mock()
        coordinator.submit.side_effect = RuntimeError("coordinator unavailable")
        worker = QueueWorker(self.queue, coordinator, clock=lambda: 10.0)
        self.assertTrue(worker.run_once(now=10.0))
        record = self.queue.get(self.task.task_id)
        self.assertEqual(record.status, "failed")
        self.assertEqual(record.outcome.reason, "worker_exception")


if __name__ == "__main__":
    unittest.main()
