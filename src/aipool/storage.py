"""Small SQLite persistence layer for outcomes and provider observations."""

from __future__ import annotations

import json
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
            CREATE TABLE IF NOT EXISTS queue_tasks (
                task_id TEXT PRIMARY KEY, idempotency_key TEXT NOT NULL UNIQUE,
                task_json TEXT NOT NULL, status TEXT NOT NULL,
                created_at REAL NOT NULL, updated_at REAL NOT NULL,
                lease_id TEXT, lease_until REAL,
                cancel_requested INTEGER NOT NULL DEFAULT 0,
                outcome_json TEXT
            );
            CREATE TABLE IF NOT EXISTS provider_usage (
                provider_id TEXT NOT NULL, window_start REAL NOT NULL,
                requests INTEGER NOT NULL, tokens INTEGER NOT NULL,
                PRIMARY KEY (provider_id, window_start)
            );
            CREATE TABLE IF NOT EXISTS provider_candidates (
                candidate_id TEXT PRIMARY KEY, name TEXT NOT NULL, source TEXT NOT NULL,
                transport TEXT NOT NULL, endpoint TEXT NOT NULL, terms_url TEXT NOT NULL,
                authorization TEXT NOT NULL, state TEXT NOT NULL,
                rejection_reason TEXT, probe_json TEXT, updated_at REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS discovery_leads (
                lead_id TEXT PRIMARY KEY, title TEXT NOT NULL, source_url TEXT NOT NULL,
                summary TEXT NOT NULL, external_url TEXT NOT NULL, source_kind TEXT NOT NULL,
                terms_url TEXT NOT NULL, transport_hint TEXT NOT NULL,
                discovered_at REAL NOT NULL, first_seen REAL NOT NULL,
                last_seen REAL NOT NULL, hit_count INTEGER NOT NULL
            );
            CREATE TABLE IF NOT EXISTS discovered_models (
                model_key TEXT PRIMARY KEY, provider_slug TEXT NOT NULL,
                provider_name TEXT NOT NULL, model_id TEXT NOT NULL,
                transport TEXT NOT NULL, endpoint TEXT NOT NULL,
                source_url TEXT NOT NULL, power TEXT NOT NULL,
                quota_weight REAL NOT NULL, capabilities_json TEXT NOT NULL,
                metadata_confidence TEXT NOT NULL, state TEXT NOT NULL,
                first_seen REAL NOT NULL, last_seen REAL NOT NULL,
                probe_status TEXT NOT NULL DEFAULT 'not_run', probe_json TEXT NOT NULL DEFAULT '{}',
                probed_at REAL,
                activation_note TEXT, activated_at REAL,
                UNIQUE(provider_slug, model_id)
            );
            CREATE TABLE IF NOT EXISTS discovered_model_reviews (
                review_id INTEGER PRIMARY KEY AUTOINCREMENT,
                model_key TEXT NOT NULL, decision TEXT NOT NULL,
                note TEXT NOT NULL, reviewed_at REAL NOT NULL
            );
            """
        )
        columns = {str(row["name"]) for row in self.connection.execute("PRAGMA table_info(discovered_models)")}
        for name, definition in (
            ("review_note", "TEXT"), ("reviewed_at", "REAL"),
            ("probe_status", "TEXT NOT NULL DEFAULT 'not_run'"),
            ("probe_json", "TEXT NOT NULL DEFAULT '{}'"), ("probed_at", "REAL"),
            ("activation_note", "TEXT"), ("activated_at", "REAL"),
        ):
            if name not in columns:
                self.connection.execute(f"ALTER TABLE discovered_models ADD COLUMN {name} {definition}")
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

    def save_discovered_models(self, provider: object, models: list[dict[str, object]], *, now: float) -> None:
        """Upsert redacted model metadata; discovery never changes routing state."""
        provider_slug = str(getattr(provider, "provider_slug"))
        provider_name = str(getattr(provider, "provider_name"))
        transport = str(getattr(provider, "transport"))
        endpoint = str(getattr(provider, "endpoint"))
        source_url = str(getattr(provider, "source_url"))
        with self._lock:
            for model in models:
                model_id = str(model.get("id", "")).strip()
                if not model_id:
                    continue
                self.connection.execute(
                    """INSERT INTO discovered_models
                    (model_key, provider_slug, provider_name, model_id, transport,
                     endpoint, source_url, power, quota_weight, capabilities_json,
                     metadata_confidence, state, first_seen, last_seen)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'quarantined', ?, ?)
                    ON CONFLICT(provider_slug, model_id) DO UPDATE SET
                    provider_name=excluded.provider_name, transport=excluded.transport,
                    endpoint=excluded.endpoint, source_url=excluded.source_url,
                    power=excluded.power, quota_weight=excluded.quota_weight,
                    capabilities_json=excluded.capabilities_json,
                    metadata_confidence=excluded.metadata_confidence,
                    last_seen=excluded.last_seen""",
                    (
                        f"model:{provider_slug}:{model_id}", provider_slug, provider_name,
                        model_id, transport, endpoint, source_url,
                        str(model.get("power", "unknown")), float(model.get("quota_weight", 1.0)),
                        json.dumps(model.get("capabilities", []), separators=(",", ":")),
                        str(model.get("metadata_confidence", "low")), now, now,
                    ),
                )
            self.connection.commit()

    def discovered_model_rows(self, provider_slug: str | None = None) -> list[sqlite3.Row]:
        with self._lock:
            if provider_slug:
                return self.connection.execute(
                    "SELECT * FROM discovered_models WHERE provider_slug = ? ORDER BY model_id",
                    (provider_slug,),
                ).fetchall()
            return self.connection.execute("SELECT * FROM discovered_models ORDER BY provider_slug, model_id").fetchall()

    def review_discovered_model(self, model_key: str, decision: str, note: str, *, now: float) -> sqlite3.Row:
        model_key = str(model_key).strip()
        decision = str(decision).strip().casefold()
        note = str(note).strip()
        if not model_key:
            raise ValueError("model_key is required")
        if decision not in {"approve", "reject"}:
            raise ValueError("decision must be approve or reject")
        if not note or len(note) > 1_000:
            raise ValueError("a review note between 1 and 1000 characters is required")
        with self._lock:
            row = self.connection.execute(
                "SELECT * FROM discovered_models WHERE model_key = ?", (model_key,)
            ).fetchone()
            if row is None:
                raise LookupError("unknown_discovered_model")
            if row["state"] != "quarantined":
                raise ValueError("discovered model has already been reviewed")
            state = "approved" if decision == "approve" else "rejected"
            self.connection.execute(
                "UPDATE discovered_models SET state = ?, review_note = ?, reviewed_at = ? WHERE model_key = ?",
                (state, note, now, model_key),
            )
            self.connection.execute(
                "INSERT INTO discovered_model_reviews(model_key, decision, note, reviewed_at) VALUES (?, ?, ?, ?)",
                (model_key, decision, note, now),
            )
            self.connection.commit()
            return self.connection.execute(
                "SELECT * FROM discovered_models WHERE model_key = ?", (model_key,)
            ).fetchone()

    def record_discovered_probe(self, model_key: str, result: BenchmarkResult, *, now: float) -> sqlite3.Row:
        passed = result.stopped_error is None and result.attempts > 0 and result.valid == result.attempts
        status = "passed" if passed else "failed"
        payload = json.dumps({
            "scores": result.scores, "attempts": result.attempts, "valid": result.valid,
            "stopped_error": result.stopped_error.value if result.stopped_error else None,
            "retry_after_seconds": result.retry_after_seconds,
        }, separators=(",", ":"))
        with self._lock:
            row = self.connection.execute(
                "SELECT * FROM discovered_models WHERE model_key = ?", (str(model_key),)
            ).fetchone()
            if row is None:
                raise LookupError("unknown_discovered_model")
            if row["state"] != "approved":
                raise ValueError("discovered model must be approved before smoke testing")
            state = "smoke_tested" if passed else "approved"
            self.connection.execute(
                "UPDATE discovered_models SET state = ?, probe_status = ?, probe_json = ?, probed_at = ? WHERE model_key = ?",
                (state, status, payload, now, str(model_key)),
            )
            self.connection.commit()
            return self.connection.execute(
                "SELECT * FROM discovered_models WHERE model_key = ?", (str(model_key),)
            ).fetchone()

    def activate_discovered_model(self, model_key: str, note: str, *, now: float) -> sqlite3.Row:
        return self._set_discovered_activation(model_key, "active", note, now=now)

    def deactivate_discovered_model(self, model_key: str, note: str, *, now: float) -> sqlite3.Row:
        return self._set_discovered_activation(model_key, "smoke_tested", note, now=now)

    def _set_discovered_activation(self, model_key: str, state: str, note: str, *, now: float) -> sqlite3.Row:
        model_key = str(model_key).strip()
        note = str(note).strip()
        if not model_key:
            raise ValueError("model_key is required")
        if not note or len(note) > 1_000:
            raise ValueError("an activation note between 1 and 1000 characters is required")
        with self._lock:
            row = self.connection.execute(
                "SELECT * FROM discovered_models WHERE model_key = ?", (model_key,)
            ).fetchone()
            if row is None:
                raise LookupError("unknown_discovered_model")
            if state == "active":
                if row["state"] != "smoke_tested" or row["probe_status"] != "passed":
                    raise ValueError("discovered model must pass its smoke test before activation")
                decision = "activate"
                activation_note = note
                activated_at = now
            else:
                if row["state"] != "active":
                    raise ValueError("discovered model is not active")
                decision = "deactivate"
                activation_note = None
                activated_at = None
            self.connection.execute(
                "UPDATE discovered_models SET state = ?, activation_note = ?, activated_at = ? WHERE model_key = ?",
                (state, activation_note, activated_at, model_key),
            )
            self.connection.execute(
                "INSERT INTO discovered_model_reviews(model_key, decision, note, reviewed_at) VALUES (?, ?, ?, ?)",
                (model_key, decision, note, now),
            )
            self.connection.commit()
            return self.connection.execute(
                "SELECT * FROM discovered_models WHERE model_key = ?", (model_key,)
            ).fetchone()

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

    def usage(self, provider_id: str, window_start: float) -> tuple[int, int]:
        with self._lock:
            row = self.connection.execute(
                "SELECT requests, tokens FROM provider_usage WHERE provider_id = ? AND window_start = ?",
                (provider_id, window_start),
            ).fetchone()
        return (int(row["requests"]), int(row["tokens"])) if row else (0, 0)

    def reserve_usage(self, provider_id: str, window_start: float) -> tuple[int, int]:
        with self._lock:
            self.connection.execute(
                "INSERT INTO provider_usage(provider_id, window_start, requests, tokens) VALUES (?, ?, 1, 0) "
                "ON CONFLICT(provider_id, window_start) DO UPDATE SET requests = requests + 1",
                (provider_id, window_start),
            )
            self.connection.commit()
            row = self.connection.execute(
                "SELECT requests, tokens FROM provider_usage WHERE provider_id = ? AND window_start = ?",
                (provider_id, window_start),
            ).fetchone()
        return int(row["requests"]), int(row["tokens"])

    def add_usage_tokens(self, provider_id: str, window_start: float, tokens: int) -> None:
        with self._lock:
            self.connection.execute(
                "UPDATE provider_usage SET tokens = tokens + ? WHERE provider_id = ? AND window_start = ?",
                (max(0, tokens), provider_id, window_start),
            )
            self.connection.commit()

    def save_candidate(self, candidate: object, *, updated_at: float = 0.0) -> None:
        """Persist validated candidate metadata; never stores an endpoint credential."""
        with self._lock:
            self.connection.execute(
                """INSERT OR REPLACE INTO provider_candidates
                (candidate_id, name, source, transport, endpoint, terms_url,
                 authorization, state, rejection_reason, probe_json, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?,
                        COALESCE((SELECT probe_json FROM provider_candidates WHERE candidate_id = ?), NULL), ?)""",
                (candidate.id, candidate.name, candidate.source, candidate.transport,
                 candidate.endpoint, candidate.terms_url, candidate.authorization,
                 candidate.state.value, candidate.rejection_reason, candidate.id,
                 updated_at),
            )
            self.connection.commit()

    def candidate_rows(self) -> list[sqlite3.Row]:
        with self._lock:
            return self.connection.execute(
                """SELECT candidate_id, name, source, transport, endpoint, terms_url,
                authorization, state, rejection_reason FROM provider_candidates
                ORDER BY candidate_id"""
            ).fetchall()

    def save_candidate_probe(self, candidate_id: str, probe_json: str) -> None:
        with self._lock:
            self.connection.execute(
                "UPDATE provider_candidates SET probe_json = ? WHERE candidate_id = ?",
                (probe_json, candidate_id),
            )
            self.connection.commit()

    def candidate_probe(self, candidate_id: str) -> str | None:
        with self._lock:
            row = self.connection.execute(
                "SELECT probe_json FROM provider_candidates WHERE candidate_id = ?",
                (candidate_id,),
            ).fetchone()
        return str(row["probe_json"]) if row and row["probe_json"] is not None else None

    def save_discovery_lead(self, lead: object) -> None:
        with self._lock:
            self.connection.execute(
                """INSERT OR REPLACE INTO discovery_leads
                (lead_id, title, source_url, summary, external_url, source_kind,
                 terms_url, transport_hint, discovered_at, first_seen, last_seen, hit_count)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (lead.lead_id, lead.title, lead.source_url, lead.summary, lead.external_url,
                 lead.source_kind, lead.terms_url, lead.transport_hint, lead.discovered_at,
                 lead.first_seen, lead.last_seen, lead.hit_count),
            )
            self.connection.commit()

    def discovery_lead_rows(self) -> list[sqlite3.Row]:
        with self._lock:
            return self.connection.execute(
                """SELECT lead_id, title, source_url, summary, external_url, source_kind,
                terms_url, transport_hint, discovered_at, first_seen, last_seen, hit_count
                FROM discovery_leads ORDER BY last_seen DESC, lead_id"""
            ).fetchall()

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
