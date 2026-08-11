"""Small SQLite persistence layer for outcomes and provider observations."""

from __future__ import annotations

import sqlite3
import threading
from pathlib import Path

from .benchmark import BenchmarkResult
from .domain import ProviderProfile, ProviderState, Strategy, TaskOutcome


class Store:
    def __init__(self, path: str | Path = ":memory:") -> None:
        self.connection = sqlite3.connect(str(path), check_same_thread=False)
        self._lock = threading.RLock()
        self.connection.row_factory = sqlite3.Row
        self.connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS outcomes (
                task_id TEXT PRIMARY KEY, strategy TEXT NOT NULL, provider_id TEXT,
                success INTEGER NOT NULL, valid INTEGER NOT NULL, reason TEXT,
                orchestration_cost REAL NOT NULL, delegated_compute_saved REAL NOT NULL,
                worker_tokens INTEGER NOT NULL, output TEXT
            );
            CREATE TABLE IF NOT EXISTS observations (
                provider_id TEXT NOT NULL, capability TEXT NOT NULL,
                attempts INTEGER NOT NULL, successes INTEGER NOT NULL,
                PRIMARY KEY (provider_id, capability)
            );
            CREATE TABLE IF NOT EXISTS provider_health (
                provider_id TEXT PRIMARY KEY, state TEXT NOT NULL,
                failure_streak INTEGER NOT NULL, next_probe_at REAL NOT NULL,
                last_success REAL, last_failure_reason TEXT
            );
            CREATE TABLE IF NOT EXISTS cache (
                cache_key TEXT PRIMARY KEY, task_id TEXT NOT NULL,
                provider_id TEXT NOT NULL, output TEXT NOT NULL,
                worker_tokens INTEGER NOT NULL, created_at REAL NOT NULL
            );
            """
        )
        self.connection.commit()

    def record_outcome(self, outcome: TaskOutcome) -> None:
        with self._lock:
            self.connection.execute(
                """INSERT OR REPLACE INTO outcomes
                (task_id, strategy, provider_id, success, valid, reason,
                 orchestration_cost, delegated_compute_saved, worker_tokens, output)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (outcome.task_id, outcome.strategy.value, outcome.provider_id, int(outcome.success), int(outcome.valid), outcome.reason,
                 outcome.orchestration_cost, outcome.delegated_compute_saved, outcome.worker_tokens, outcome.output),
            )
            self.connection.commit()

    def record_observation(self, provider: ProviderProfile, capabilities: tuple[str, ...], success: bool) -> None:
        with self._lock:
            for capability in capabilities:
                self.connection.execute(
                    """INSERT INTO observations(provider_id, capability, attempts, successes) VALUES (?, ?, 1, ?)
                    ON CONFLICT(provider_id, capability) DO UPDATE SET
                    attempts = attempts + 1, successes = successes + excluded.successes""",
                    (provider.id, capability, int(success)),
                )
            self.connection.commit()

    def record_benchmark(self, result: BenchmarkResult) -> None:
        """Persist one bounded benchmark score per capability as provider evidence."""
        provider_id = str(result.provider_id)
        with self._lock:
            for capability, score in result.scores.items():
                self.connection.execute(
                    """INSERT INTO observations(provider_id, capability, attempts, successes) VALUES (?, ?, 1, ?)
                    ON CONFLICT(provider_id, capability) DO UPDATE SET
                    attempts = attempts + 1, successes = successes + excluded.successes""",
                    (provider_id, str(capability), float(score)),
                )
            self.connection.commit()

    def observation(self, provider_id: str, capability: str) -> tuple[int, float]:
        with self._lock:
            row = self.connection.execute(
                "SELECT attempts, successes FROM observations WHERE provider_id = ? AND capability = ?",
                (provider_id, capability),
            ).fetchone()
        return (int(row["attempts"]), float(row["successes"])) if row else (0, 0.0)

    def learned_capabilities(self, provider: ProviderProfile, *, prior_weight: float = 3.0) -> dict[str, float]:
        """Blend declared capability with observed outcomes using a conservative prior."""
        learned: dict[str, float] = {}
        with self._lock:
            observed = {
                str(row["capability"])
                for row in self.connection.execute(
                    "SELECT capability FROM observations WHERE provider_id = ?", (provider.id,)
                ).fetchall()
            }
        for capability in set(provider.capabilities) | observed:
            attempts, successes = self.observation(provider.id, capability)
            if capability not in provider.capabilities:
                learned[capability] = successes / attempts if attempts else 0.0
            else:
                declared = float(provider.capabilities[capability])
                learned[capability] = ((declared * prior_weight) + successes) / (prior_weight + attempts)
        return learned

    def ensure_health(self, provider: ProviderProfile) -> None:
        with self._lock:
            self.connection.execute(
                "INSERT OR IGNORE INTO provider_health(provider_id, state, failure_streak, next_probe_at) VALUES (?, ?, 0, 0)",
                (provider.id, provider.state.value),
            )
            self.connection.commit()

    def health(self, provider_id: str) -> sqlite3.Row | None:
        with self._lock:
            return self.connection.execute(
                "SELECT provider_id, state, failure_streak, next_probe_at, last_success, last_failure_reason FROM provider_health WHERE provider_id = ?",
                (provider_id,),
            ).fetchone()

    def set_health(self, provider_id: str, *, state: ProviderState, failure_streak: int | None = None,
                   next_probe_at: float | None = None, last_success: float | None = None,
                   last_failure_reason: str | None = None) -> None:
        with self._lock:
            self.connection.execute(
                """UPDATE provider_health SET state = ?,
                failure_streak = COALESCE(?, failure_streak),
                next_probe_at = COALESCE(?, next_probe_at),
                last_success = COALESCE(?, last_success),
                last_failure_reason = ? WHERE provider_id = ?""",
                (state.value, failure_streak, next_probe_at, last_success, last_failure_reason, provider_id),
            )
            self.connection.commit()

    def close(self) -> None:
        with self._lock:
            self.connection.close()

    def cache_get(self, cache_key: str) -> sqlite3.Row | None:
        with self._lock:
            return self.connection.execute(
                "SELECT task_id, provider_id, output, worker_tokens FROM cache WHERE cache_key = ?",
                (cache_key,),
            ).fetchone()

    def cache_put(self, cache_key: str, outcome: TaskOutcome, created_at: float) -> None:
        if not outcome.success or not outcome.valid or outcome.provider_id is None or outcome.output is None:
            return
        with self._lock:
            self.connection.execute(
                """INSERT OR REPLACE INTO cache(cache_key, task_id, provider_id, output, worker_tokens, created_at)
                VALUES (?, ?, ?, ?, ?, ?)""",
                (cache_key, outcome.task_id, outcome.provider_id, outcome.output, outcome.worker_tokens, created_at),
            )
            self.connection.commit()

    def stats(self) -> dict[str, object]:
        with self._lock:
            totals = self.connection.execute(
                """SELECT COUNT(*) AS tasks,
                COALESCE(SUM(success), 0) AS successes,
                COALESCE(SUM(valid), 0) AS valid,
                COALESCE(SUM(orchestration_cost), 0) AS orchestration_cost,
                COALESCE(SUM(delegated_compute_saved), 0) AS delegated_compute_saved,
                COALESCE(SUM(worker_tokens), 0) AS worker_tokens
                FROM outcomes"""
            ).fetchone()
            providers = self.connection.execute(
                """SELECT provider_id, COUNT(*) AS tasks,
                SUM(success) AS successes, SUM(valid) AS valid,
                AVG(orchestration_cost) AS average_orchestration_cost,
                SUM(delegated_compute_saved) AS delegated_compute_saved
                FROM outcomes WHERE provider_id IS NOT NULL GROUP BY provider_id ORDER BY tasks DESC, provider_id"""
            ).fetchall()
        return {
            "tasks": int(totals["tasks"]),
            "successes": int(totals["successes"]),
            "valid": int(totals["valid"]),
            "orchestration_cost": float(totals["orchestration_cost"]),
            "delegated_compute_saved": float(totals["delegated_compute_saved"]),
            "worker_tokens": int(totals["worker_tokens"]),
            "providers": [dict(row) for row in providers],
        }
