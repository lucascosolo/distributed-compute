"""Application service: route, execute, validate, and record one task."""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import replace

from .benchmark import BenchmarkCase, BenchmarkResult, run_benchmark
from .domain import ProviderErrorKind, ProviderState, Strategy, TaskEnvelope, TaskOutcome
from .health import HealthManager
from .providers import ProviderRegistry
from .quality import validate_output
from .routing import RoutingDecision, choose_provider
from .scoping import ALLOWED_SUBTASKS, split_task
from .storage import Store
from .usage import UsageManager


MAX_MAP_REDUCE_INPUT_BYTES = 256 * 1024


class Coordinator:
    def __init__(self, registry: ProviderRegistry, store: Store, health: HealthManager | None = None, usage: UsageManager | None = None) -> None:
        self.registry = registry
        self.store = store
        self.health = health or HealthManager(store)
        self.usage = usage or UsageManager(store)

    def submit(self, task: TaskEnvelope) -> TaskOutcome:
        if task.strategy == Strategy.VERIFY:
            return self._submit_verified(task)
        if task.strategy == Strategy.CONSENSUS:
            return self._submit_consensus(task)
        if task.strategy == Strategy.MAP:
            return self._submit_map(task)
        if task.strategy == Strategy.MAP_REDUCE:
            return self._submit_map_reduce(task)
        return self._submit_single(task)

    def benchmark_provider(
        self,
        provider_id: str,
        cases: tuple[BenchmarkCase, ...] | None = None,
    ) -> BenchmarkResult:
        """Run a bounded capability probe and persist its evidence for future routing."""
        adapter = self.registry.get(provider_id)
        self.health.profiles([adapter.profile])
        result = run_benchmark(adapter, cases)
        self.store.record_benchmark(result)
        if result.valid and result.stopped_error is None:
            self.health.success(adapter.profile)
        else:
            self.health.failure(
                adapter.profile,
                result.stopped_error or ProviderErrorKind.INTERNAL,
                "benchmark stopped before completion" if result.stopped_error else "benchmark produced no valid results",
                retry_after_seconds=result.retry_after_seconds,
            )
        return result

    def benchmark_providers(
        self,
        provider_ids: tuple[str, ...] | None = None,
        cases: tuple[BenchmarkCase, ...] | None = None,
    ) -> dict[str, BenchmarkResult]:
        """Probe a bounded set of registered providers sequentially."""
        ids = provider_ids if provider_ids is not None else tuple(adapter.profile.id for adapter in self.registry.all())
        if not ids or len(ids) > 32 or len(set(ids)) != len(ids):
            raise ValueError("provider probe set must contain 1 to 32 unique providers")
        return {provider_id: self.benchmark_provider(provider_id, cases) for provider_id in ids}

    def _submit_single(self, task: TaskEnvelope, excluded: frozenset[str] = frozenset()) -> TaskOutcome:
        delegation_ancestors = set(task.delegation_chain)
        if task.origin_provider_id:
            delegation_ancestors.add(task.origin_provider_id)
        excluded = frozenset(set(excluded) | delegation_ancestors)
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
        blocked_transports: set[str] = set()
        dispatched = False
        candidates = [decision.provider] + [profile for profile in profiles if profile.id != decision.provider.id]
        for profile in candidates:
            if (
                profile.id in excluded or profile.id in attempted
                or profile.transport in blocked_transports
                or profile.state not in {ProviderState.HEALTHY, ProviderState.DEGRADED}
            ):
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
            reserved, hold_until = self.usage.reserve(profile)
            if not reserved:
                self.health.hold(profile, hold_until, "configured_usage_limit_reached")
                continue
            dispatched = True
            result = self.registry.get(profile.id).complete(task)
            usage_hold_until = self.usage.record_tokens(profile, result.worker_tokens)
            usage_exhausted = bool(
                profile.token_limit
                and self.store.usage(profile.quota_group, self.usage.window(profile, time.time())[0])[1] >= profile.token_limit
            )
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
                if usage_exhausted:
                    self.health.hold(profile, usage_hold_until, "configured_token_limit_reached")
                self.store.cache_put(cache_key, outcome, time.time())
                return outcome
            reason = report.reason if report else (result.error_kind.value if result.error_kind else "provider_failure")
            self.store.record_observation(profile, decision.assessment.capabilities, False)
            self.health.failure(
                profile,
                result.error_kind,
                reason,
                retry_after_seconds=result.retry_after_seconds,
            )
            if result.error_kind == ProviderErrorKind.RATE_LIMITED:
                blocked_transports.add(profile.transport)
            if usage_exhausted:
                self.health.hold(profile, usage_hold_until, "configured_token_limit_reached")

        if not dispatched:
            healthy_profiles = [
                profile for profile in profiles
                if profile.state in {ProviderState.HEALTHY, ProviderState.DEGRADED}
            ]
            if (task.origin_provider_id or task.delegation_chain) and healthy_profiles and all(profile.id in excluded for profile in healthy_profiles):
                reason = "delegation_chain_exhausted"
            else:
                reason = "provider_usage_limit_reached"
            outcome = TaskOutcome(
                task.task_id, Strategy.NO_DELEGATION, None, None, True, True,
                reason, native_fallback=True,
            )
            self.store.record_outcome(outcome)
            return outcome

        outcome = TaskOutcome(
            task.task_id, decision.strategy, decision.provider.id, None, False, False,
            "all_candidate_providers_failed",
            orchestration_cost=decision.assessment.single_cost * len(attempted),
        )
        self.store.record_outcome(outcome)
        return outcome

    def _submit_map(self, task: TaskEnvelope) -> TaskOutcome:
        scopes = task.requirements.get("scopes")
        subtask_kind = task.requirements.get("subtask_kind")
        if not isinstance(scopes, (list, tuple)) or not isinstance(subtask_kind, str):
            return self._native_composite_fallback(task, Strategy.MAP, "map_scopes_required")
        try:
            subtasks = split_task(task, scopes, subtask_kind=subtask_kind)
        except (TypeError, ValueError):
            return self._native_composite_fallback(task, Strategy.MAP, "map_scopes_invalid")

        outcomes: list[TaskOutcome] = []
        used_provider_ids: set[str] = set()
        for subtask in subtasks:
            # Independent scopes benefit from different model/provider biases.
            # Exclusion is advisory: if there is no alternative, reuse the
            # available provider rather than failing a valid map unnecessarily.
            outcome = self._submit_single(subtask, frozenset(used_provider_ids))
            if outcome.native_fallback and outcome.reason in {"no_healthy_capable_provider", "provider_usage_limit_reached"} and used_provider_ids:
                outcome = self._submit_single(subtask)
            outcomes.append(outcome)
            if outcome.provider_id:
                selected = self.registry.get(outcome.provider_id).profile
                used_provider_ids.update(
                    profile.id for profile in
                    (adapter.profile for adapter in self.registry.all())
                    if profile.quota_group == selected.quota_group
                )
        total_cost = sum(outcome.orchestration_cost for outcome in outcomes)
        total_tokens = sum(outcome.worker_tokens for outcome in outcomes)
        if any(not outcome.valid or outcome.native_fallback for outcome in outcomes):
            reason = "map_subtask_native_fallback" if any(outcome.native_fallback for outcome in outcomes) else "map_subtask_failed"
            return self._native_composite_fallback(task, Strategy.MAP, reason, total_cost, total_tokens)

        aggregate = json.dumps(
            [
                {"scope": subtask.requirements["scope"], "output": outcome.output}
                for subtask, outcome in zip(subtasks, outcomes)
            ],
            separators=(",", ":"),
        )
        outcome = TaskOutcome(
            task.task_id, Strategy.MAP, None, aggregate, True, True, "map",
            orchestration_cost=total_cost,
            delegated_compute_saved=max(0.0, task.local_estimate - total_cost),
            worker_tokens=total_tokens,
        )
        self.store.record_outcome(outcome)
        return outcome

    def _submit_map_reduce(self, task: TaskEnvelope) -> TaskOutcome:
        reduce_kind = task.requirements.get("reduce_kind", "summarization")
        if not isinstance(reduce_kind, str) or reduce_kind not in ALLOWED_SUBTASKS:
            return self._native_composite_fallback(task, Strategy.MAP_REDUCE, "reduce_kind_invalid")
        mapped = self._submit_map(replace(task, strategy=Strategy.MAP))
        if not mapped.valid or mapped.output is None:
            return self._native_composite_fallback(
                task, Strategy.MAP_REDUCE, "map_reduce_map_fallback",
                mapped.orchestration_cost, mapped.worker_tokens,
            )
        try:
            mapped_outputs = json.loads(mapped.output)
            serialized = json.dumps(mapped_outputs, separators=(",", ":"))
        except (TypeError, json.JSONDecodeError):
            return self._native_composite_fallback(task, Strategy.MAP_REDUCE, "map_reduce_invalid_map_output", mapped.orchestration_cost, mapped.worker_tokens)
        if len(serialized.encode()) > MAX_MAP_REDUCE_INPUT_BYTES:
            return self._native_composite_fallback(task, Strategy.MAP_REDUCE, "map_reduce_input_too_large", mapped.orchestration_cost, mapped.worker_tokens)

        requirements = dict(task.requirements)
        for key in ("scopes", "subtask_kind", "reduce_kind", "output"):
            requirements.pop(key, None)
        requirements["mapped_outputs"] = mapped_outputs
        reduce_task = TaskEnvelope(
            task=reduce_kind,
            input_ref=task.input_ref,
            requirements=requirements,
            importance=task.importance,
            strategy=Strategy.SINGLE,
            max_cost=max(0.0, task.max_cost - mapped.orchestration_cost),
            local_estimate=max(0.0, task.local_estimate - mapped.orchestration_cost),
            origin_provider_id=task.origin_provider_id,
            delegation_chain=task.delegation_chain,
        )
        reduced = self.submit(reduce_task)
        total_cost = mapped.orchestration_cost + reduced.orchestration_cost
        if not reduced.valid:
            return self._native_composite_fallback(
                task, Strategy.MAP_REDUCE,
                "map_reduce_reduce_fallback" if reduced.native_fallback else "map_reduce_reduce_failed",
                total_cost, mapped.worker_tokens + reduced.worker_tokens,
            )
        outcome = TaskOutcome(
            task.task_id, Strategy.MAP_REDUCE, reduced.provider_id, reduced.output,
            True, True, "map_reduce", orchestration_cost=total_cost,
            delegated_compute_saved=max(0.0, task.local_estimate - total_cost),
            worker_tokens=mapped.worker_tokens + reduced.worker_tokens,
        )
        self.store.record_outcome(outcome)
        return outcome

    def _native_composite_fallback(
        self,
        task: TaskEnvelope,
        strategy: Strategy,
        reason: str,
        orchestration_cost: float = 0.0,
        worker_tokens: int = 0,
    ) -> TaskOutcome:
        outcome = TaskOutcome(
            task.task_id, strategy, None, None, True, False, reason,
            orchestration_cost=orchestration_cost,
            delegated_compute_saved=max(0.0, task.local_estimate - orchestration_cost),
            worker_tokens=worker_tokens,
            native_fallback=True,
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
                task.task_id, Strategy.VERIFY, None, self._opinion_bundle((first, second)), True, False,
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
                task.task_id, Strategy.CONSENSUS, None, self._opinion_bundle(tuple(results)), True, False,
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
    def _opinion_bundle(results: tuple[TaskOutcome, ...]) -> str:
        """Return bounded, untrusted alternatives for the native fallback model."""
        return json.dumps({
            "kind": "independent_opinions",
            "explanation": "Providers disagreed; the native model should compare these bounded opinions.",
            "opinions": [
                {"provider_id": result.provider_id, "output": (result.output or "")[:16_000]}
                for result in results
            ],
        }, separators=(",", ":"))

    @staticmethod
    def _cache_key(task: TaskEnvelope, provider_id: str) -> str:
        payload = json.dumps({"task": task.normalized(), "provider": provider_id}, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(payload.encode()).hexdigest()
