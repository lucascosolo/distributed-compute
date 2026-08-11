"""Durable, bounded task queue primitives."""

from __future__ import annotations

import json
import sqlite3
import uuid
from dataclasses import dataclass

from .domain import Strategy, TaskEnvelope, TaskOutcome
from .storage import Store


class QueueFull(RuntimeError):
    """The configured number of queued or leased tasks has been reached."""


@dataclass(frozen=True, slots=True)
class QueueRecord:
    task_id: str
    idempotency_key: str
    task: TaskEnvelope
    status: str
    lease_id: str | None
    lease_until: float | None
    cancel_requested: bool
    outcome: TaskOutcome | None


def record_to_dict(record: QueueRecord) -> dict[str, object]:
    """Serialize queue state without echoing the task input reference."""
    return {
        "task_id": record.task_id,
        "idempotency_key": record.idempotency_key,
        "status": record.status,
        "lease_until": record.lease_until,
        "cancel_requested": record.cancel_requested,
        "outcome": _outcome_to_dict(record.outcome) if record.outcome is not None else None,
    }


def _outcome_to_dict(outcome: TaskOutcome) -> dict[str, object]:
    return {
        "task_id": outcome.task_id,
        "strategy": outcome.strategy.value,
        "provider_id": outcome.provider_id,
        "output": outcome.output,
        "success": outcome.success,
        "valid": outcome.valid,
        "reason": outcome.reason,
        "orchestration_cost": outcome.orchestration_cost,
        "delegated_compute_saved": outcome.delegated_compute_saved,
        "worker_tokens": outcome.worker_tokens,
        "native_fallback": outcome.native_fallback,
    }


def _outcome_from_dict(payload: dict[str, object]) -> TaskOutcome:
    return TaskOutcome(
        task_id=str(payload["task_id"]),
        strategy=Strategy(str(payload["strategy"])),
        provider_id=str(payload["provider_id"]) if payload["provider_id"] is not None else None,
        output=str(payload["output"]) if payload["output"] is not None else None,
        success=bool(payload["success"]),
        valid=bool(payload["valid"]),
        reason=str(payload["reason"]) if payload["reason"] is not None else None,
        orchestration_cost=float(payload["orchestration_cost"]),
        delegated_compute_saved=float(payload["delegated_compute_saved"]),
        worker_tokens=int(payload["worker_tokens"]),
        native_fallback=bool(payload["native_fallback"]),
    )


class TaskQueue:
    def __init__(self, store: Store, *, max_pending: int = 1000) -> None:
        if max_pending < 1:
            raise ValueError("max_pending must be positive")
        self.store = store
        self.max_pending = max_pending

    def enqueue(self, task: TaskEnvelope, *, idempotency_key: str | None = None, now: float = 0.0) -> QueueRecord:
        key = (idempotency_key or task.task_id).strip()
        if not key or len(key) > 256:
            raise ValueError("idempotency_key must be non-empty and at most 256 characters")
        with self.store._lock:
            existing = self.store.connection.execute(
                "SELECT * FROM queue_tasks WHERE idempotency_key = ? OR task_id = ? LIMIT 1",
                (key, task.task_id),
            ).fetchone()
            if existing is not None:
                return self._record(existing)
            pending = self.store.connection.execute(
                "SELECT COUNT(*) AS count FROM queue_tasks WHERE status IN ('queued', 'running')"
            ).fetchone()["count"]
            if int(pending) >= self.max_pending:
                raise QueueFull("task queue is full")
            try:
                self.store.connection.execute(
                    """INSERT INTO queue_tasks
                    (task_id, idempotency_key, task_json, status, created_at, updated_at)
                    VALUES (?, ?, ?, 'queued', ?, ?)""",
                    (task.task_id, key, json.dumps(task.to_dict(), separators=(",", ":")), now, now),
                )
                self.store.connection.commit()
            except sqlite3.IntegrityError:
                self.store.connection.rollback()
                existing = self.store.connection.execute(
                    "SELECT * FROM queue_tasks WHERE idempotency_key = ? OR task_id = ? LIMIT 1",
                    (key, task.task_id),
                ).fetchone()
                if existing is None:
                    raise
                return self._record(existing)
            return self._record(self.store.connection.execute("SELECT * FROM queue_tasks WHERE task_id = ?", (task.task_id,)).fetchone())

    def get(self, task_id: str) -> QueueRecord | None:
        with self.store._lock:
            row = self.store.connection.execute("SELECT * FROM queue_tasks WHERE task_id = ?", (task_id,)).fetchone()
        return self._record(row) if row is not None else None

    def claim(self, worker_id: str, *, lease_seconds: float = 60.0, now: float = 0.0) -> QueueRecord | None:
        if not worker_id or lease_seconds <= 0:
            raise ValueError("worker_id and positive lease_seconds are required")
        lease_id = uuid.uuid4().hex
        with self.store._lock:
            row = self.store.connection.execute(
                """SELECT * FROM queue_tasks
                WHERE cancel_requested = 0 AND
                (status = 'queued' OR (status = 'running' AND lease_until <= ?))
                ORDER BY created_at, task_id LIMIT 1""",
                (now,),
            ).fetchone()
            if row is None:
                return None
            self.store.connection.execute(
                """UPDATE queue_tasks SET status = 'running', lease_id = ?, lease_until = ?, updated_at = ?
                WHERE task_id = ?""",
                (lease_id, now + lease_seconds, now, row["task_id"]),
            )
            self.store.connection.commit()
            row = self.store.connection.execute("SELECT * FROM queue_tasks WHERE task_id = ?", (row["task_id"],)).fetchone()
        return self._record(row)

    def cancel(self, task_id: str, *, now: float = 0.0) -> bool:
        with self.store._lock:
            row = self.store.connection.execute("SELECT status FROM queue_tasks WHERE task_id = ?", (task_id,)).fetchone()
            if row is None or row["status"] in {"cancelled", "succeeded", "failed"}:
                return False
            if row["status"] == "queued":
                self.store.connection.execute(
                    "UPDATE queue_tasks SET status = 'cancelled', updated_at = ? WHERE task_id = ?", (now, task_id)
                )
            else:
                self.store.connection.execute(
                    "UPDATE queue_tasks SET cancel_requested = 1, updated_at = ? WHERE task_id = ?", (now, task_id)
                )
            self.store.connection.commit()
            return True

    def complete(self, task_id: str, lease_id: str, outcome: TaskOutcome, *, now: float = 0.0) -> bool:
        with self.store._lock:
            row = self.store.connection.execute("SELECT status, lease_id, cancel_requested FROM queue_tasks WHERE task_id = ?", (task_id,)).fetchone()
            if row is None or row["status"] != "running" or row["lease_id"] != lease_id:
                return False
            if row["cancel_requested"]:
                self.store.connection.execute(
                    "UPDATE queue_tasks SET status = 'cancelled', updated_at = ?, lease_id = NULL, lease_until = NULL WHERE task_id = ?",
                    (now, task_id),
                )
                self.store.connection.commit()
                return False
            status = "succeeded" if outcome.success and outcome.valid else "failed"
            self.store.connection.execute(
                """UPDATE queue_tasks SET status = ?, updated_at = ?, lease_id = NULL,
                lease_until = NULL, outcome_json = ? WHERE task_id = ?""",
                (status, now, json.dumps(_outcome_to_dict(outcome), separators=(",", ":")), task_id),
            )
            self.store.connection.commit()
            return True

    def cancel_claimed(self, task_id: str, lease_id: str, *, now: float = 0.0) -> bool:
        """Acknowledge a cancellation without invoking the provider."""
        with self.store._lock:
            row = self.store.connection.execute(
                "SELECT status, lease_id, cancel_requested FROM queue_tasks WHERE task_id = ?",
                (task_id,),
            ).fetchone()
            if row is None or row["status"] != "running" or row["lease_id"] != lease_id:
                return False
            if not row["cancel_requested"]:
                return False
            self.store.connection.execute(
                "UPDATE queue_tasks SET status = 'cancelled', updated_at = ?, lease_id = NULL, lease_until = NULL WHERE task_id = ?",
                (now, task_id),
            )
            self.store.connection.commit()
            return True

    @staticmethod
    def _record(row: sqlite3.Row) -> QueueRecord:
        return QueueRecord(
            task_id=str(row["task_id"]),
            idempotency_key=str(row["idempotency_key"]),
            task=TaskEnvelope.from_dict(json.loads(row["task_json"])),
            status=str(row["status"]),
            lease_id=str(row["lease_id"]) if row["lease_id"] is not None else None,
            lease_until=float(row["lease_until"]) if row["lease_until"] is not None else None,
            cancel_requested=bool(row["cancel_requested"]),
            outcome=_outcome_from_dict(json.loads(row["outcome_json"])) if row["outcome_json"] else None,
        )
