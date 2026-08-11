"""Application service: route, execute, validate, and record one task."""

from __future__ import annotations

import hashlib
import json
import time

from .domain import ProviderState, Strategy, TaskEnvelope, TaskOutcome
from .health import HealthManager
from .providers import ProviderRegistry
from .quality import validate_output
from .routing import RoutingDecision, choose_provider
from .storage import Store


class Coordinator:
    def __init__(self, registry: ProviderRegistry, store: Store, health: HealthManager | None = None) -> None:
        self.registry = registry
        self.store = store
        self.health = health or HealthManager(store)

    def submit(self, task: TaskEnvelope) -> TaskOutcome:
        profiles = self.health.profiles(adapter.profile for adapter in self.registry.all())
        decision = choose_provider(task, profiles)
        if decision.provider is None:
            outcome = TaskOutcome(
                task.task_id, Strategy.NO_DELEGATION, None, None, True, True,
                decision.reason, native_fallback=True,
            )
            self.store.record_outcome(outcome)
            return outcome

        attempted: set[str] = set()
        candidates = [decision.provider] + [profile for profile in profiles if profile.id != decision.provider.id]
        for profile in candidates:
            if profile.id in attempted or profile.state not in {ProviderState.HEALTHY, ProviderState.DEGRADED}:
                continue
            attempted.add(profile.id)
            cache_key = self._cache_key(task, profile.id)
            cached = self.store.cache_get(cache_key)
            if cached is not None:
                outcome = TaskOutcome(
                    task.task_id, decision.strategy, cached["provider_id"], cached["output"],
                    True, True, "cache_hit", orchestration_cost=0.0,
                    delegated_compute_saved=max(0.0, task.local_estimate), worker_tokens=cached["worker_tokens"],
                )
                self.store.record_outcome(outcome)
                return outcome
            result = self.registry.get(profile.id).complete(task)
            report = validate_output(
                result.output,
                require_json=task.requirements.get("output") == "json",
                task=decision.assessment.kind,
            ) if result.success else None
            if result.success and report and report.valid:
                outcome = TaskOutcome(
                    task.task_id, decision.strategy, profile.id, result.output, True, True,
                    orchestration_cost=decision.assessment.delegation_cost,
                    delegated_compute_saved=max(0.0, task.local_estimate - decision.assessment.delegation_cost),
                    worker_tokens=result.worker_tokens,
                )
                self.store.record_outcome(outcome)
                self.store.record_observation(profile, decision.assessment.capabilities, True)
                self.health.success(profile)
                self.store.cache_put(cache_key, outcome, time.time())
                return outcome
            reason = report.reason if report else (result.error_kind.value if result.error_kind else "provider_failure")
            self.store.record_observation(profile, decision.assessment.capabilities, False)
            self.health.failure(profile, result.error_kind, reason)

        outcome = TaskOutcome(
            task.task_id, decision.strategy, decision.provider.id, None, False, False,
            "all_candidate_providers_failed",
            orchestration_cost=decision.assessment.delegation_cost,
        )
        self.store.record_outcome(outcome)
        return outcome

    @staticmethod
    def _cache_key(task: TaskEnvelope, provider_id: str) -> str:
        payload = json.dumps({"task": task.normalized(), "provider": provider_id}, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(payload.encode()).hexdigest()
