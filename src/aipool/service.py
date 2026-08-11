"""Application service: route, execute, validate, and record one task."""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import replace

from .benchmark import BenchmarkCase, BenchmarkResult, run_benchmark
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
        if task.strategy == Strategy.VERIFY:
            return self._submit_verified(task)
        if task.strategy == Strategy.CONSENSUS:
            return self._submit_consensus(task)
        return self._submit_single(task)

    def benchmark_provider(
        self,
        provider_id: str,
        cases: tuple[BenchmarkCase, ...] | None = None,
    ) -> BenchmarkResult:
        """Run a bounded capability probe and persist its evidence for future routing."""
        result = run_benchmark(self.registry.get(provider_id), cases)
        self.store.record_benchmark(result)
        return result

    def _submit_single(self, task: TaskEnvelope, excluded: frozenset[str] = frozenset()) -> TaskOutcome:
        profiles = [
            replace(profile, capabilities=self.store.learned_capabilities(profile))
            for profile in self.health.profiles(adapter.profile for adapter in self.registry.all())
        ]
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
            if profile.id in excluded or profile.id in attempted or profile.state not in {ProviderState.HEALTHY, ProviderState.DEGRADED}:
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
                    orchestration_cost=decision.assessment.single_cost,
                    delegated_compute_saved=max(0.0, task.local_estimate - decision.assessment.single_cost),
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
            orchestration_cost=decision.assessment.single_cost * len(attempted),
        )
        self.store.record_outcome(outcome)
        return outcome

    def _submit_verified(self, task: TaskEnvelope) -> TaskOutcome:
        first = self._submit_single(task)
        if not first.success or not first.valid or first.provider_id is None or first.output is None:
            return first
        second = self._submit_single(replace(task, strategy=Strategy.SINGLE), frozenset({first.provider_id}))
        if not second.success or not second.valid or second.output is None:
            return TaskOutcome(
                task.task_id, Strategy.VERIFY, first.provider_id, None, True, False,
                "verification_provider_unavailable", native_fallback=True,
            )
        if self._normalized_output(task, first.output) != self._normalized_output(task, second.output):
            outcome = TaskOutcome(
                task.task_id, Strategy.VERIFY, None, None, True, False,
                "verification_disagreement", native_fallback=True,
            )
            self.store.record_outcome(outcome)
            return outcome
        outcome = replace(
            first,
            strategy=Strategy.VERIFY,
            reason="verified",
            orchestration_cost=first.orchestration_cost + second.orchestration_cost,
            delegated_compute_saved=max(0.0, task.local_estimate - first.orchestration_cost - second.orchestration_cost),
        )
        self.store.record_outcome(outcome)
        return outcome

    def _submit_consensus(self, task: TaskEnvelope) -> TaskOutcome:
        """Run at most three independent providers and accept a two-result majority."""
        first = self._submit_single(task)
        if not first.success or not first.valid or first.provider_id is None or first.output is None:
            return first

        results = [first]
        excluded = {first.provider_id}
        for _ in range(2):
            result = self._submit_single(
                replace(task, strategy=Strategy.SINGLE),
                frozenset(excluded),
            )
            if not result.success or not result.valid or result.provider_id is None or result.output is None:
                break
            results.append(result)
            excluded.add(result.provider_id)

        groups: dict[str, list[TaskOutcome]] = {}
        for result in results:
            key = json.dumps(self._normalized_output(task, result.output), sort_keys=True, separators=(",", ":"))
            groups.setdefault(key, []).append(result)
        majority = max(groups.values(), key=len)
        total_cost = sum(result.orchestration_cost for result in results)
        if len(majority) < 2:
            outcome = TaskOutcome(
                task.task_id, Strategy.CONSENSUS, None, None, True, False,
                "consensus_disagreement" if len(results) >= 2 else "consensus_provider_unavailable",
                orchestration_cost=total_cost,
                delegated_compute_saved=max(0.0, task.local_estimate - total_cost),
                native_fallback=True,
            )
            self.store.record_outcome(outcome)
            return outcome

        winner = majority[0]
        outcome = replace(
            winner,
            strategy=Strategy.CONSENSUS,
            reason="consensus",
            orchestration_cost=total_cost,
            delegated_compute_saved=max(0.0, task.local_estimate - total_cost),
            worker_tokens=sum(result.worker_tokens for result in results),
        )
        self.store.record_outcome(outcome)
        return outcome

    @staticmethod
    def _normalized_output(task: TaskEnvelope, output: str) -> object:
        if task.requirements.get("output") == "json":
            try:
                return json.loads(output)
            except json.JSONDecodeError:
                return output.strip()
        return " ".join(output.split())

    @staticmethod
    def _cache_key(task: TaskEnvelope, provider_id: str) -> str:
        payload = json.dumps({"task": task.normalized(), "provider": provider_id}, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(payload.encode()).hexdigest()
