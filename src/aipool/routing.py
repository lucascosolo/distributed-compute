"""Task classification and cost-aware provider selection."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from .domain import ProviderProfile, ProviderState, Strategy, TaskEnvelope, TaskKind


@dataclass(frozen=True, slots=True)
class TaskAssessment:
    kind: str
    capabilities: tuple[str, ...]
    complexity: int
    delegation_cost: float


@dataclass(frozen=True, slots=True)
class RoutingDecision:
    strategy: Strategy
    provider: ProviderProfile | None
    assessment: TaskAssessment
    reason: str


_CAPABILITIES = {
    TaskKind.INVENTORY.value: ("classification",),
    TaskKind.CLASSIFICATION.value: ("classification", "structured_json"),
    TaskKind.EXTRACTION.value: ("extraction", "structured_json"),
    TaskKind.SUMMARIZATION.value: ("summarization",),
    TaskKind.CODING.value: ("coding", "instruction_following"),
    TaskKind.REVIEW.value: ("code_review", "reasoning"),
    TaskKind.RESEARCH.value: ("research", "long_context"),
}
_COMPLEXITY = {"inventory": 1, "classification": 1, "extraction": 1, "summarization": 2, "coding": 3, "review": 4, "research": 4}


def assess(task: TaskEnvelope) -> TaskAssessment:
    kind = task.task.value if isinstance(task.task, TaskKind) else str(task.task)
    capabilities = _CAPABILITIES.get(kind, (kind,))
    complexity = _COMPLEXITY.get(kind, 3)
    input_units = max(1, len(task.input_ref) // 64)
    delegation_cost = 0.05 + (0.01 * input_units) + (0.02 * complexity)
    return TaskAssessment(kind, capabilities, complexity, delegation_cost)


def _meets(profile: ProviderProfile, capabilities: tuple[str, ...]) -> bool:
    return all(profile.capabilities.get(capability, 0.0) >= 0.5 for capability in capabilities)


def choose_provider(task: TaskEnvelope, providers: Iterable[ProviderProfile]) -> RoutingDecision:
    assessment = assess(task)
    if task.local_estimate > 0 and assessment.delegation_cost >= task.local_estimate:
        return RoutingDecision(Strategy.NO_DELEGATION, None, assessment, "delegation_cost_not_lower_than_local_estimate")
    eligible = [
        profile
        for profile in providers
        if profile.state in {ProviderState.HEALTHY, ProviderState.DEGRADED}
        and _meets(profile, assessment.capabilities)
        and profile.context_limit >= 0
    ]
    if not eligible:
        return RoutingDecision(Strategy.NO_DELEGATION, None, assessment, "no_healthy_capable_provider")

    def utility_cost(profile: ProviderProfile) -> float:
        capability = min(profile.capabilities.get(name, 0.0) for name in assessment.capabilities)
        utility = max(0.01, capability * max(0.01, profile.reliability))
        return (profile.estimated_cost + profile.latency_ms / 100_000) / utility

    selected = min(eligible, key=utility_cost)
    strategy = task.strategy if task.strategy != Strategy.NO_DELEGATION else Strategy.SINGLE
    return RoutingDecision(strategy, selected, assessment, "least_expensive_sufficiently_capable_provider")
