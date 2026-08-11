"""Explicit decomposition of large work into bounded, routable subtasks."""

from __future__ import annotations

from collections.abc import Iterable

from .domain import Strategy, TaskEnvelope


MAX_SCOPES = 32
ALLOWED_SUBTASKS = {"inventory", "classification", "extraction", "summarization"}


def split_task(task: TaskEnvelope, scopes: Iterable[str], *, subtask_kind: str) -> tuple[TaskEnvelope, ...]:
    """Create bounded map tasks from caller-provided scopes.

    The native model or another trusted planner must provide the scopes and the
    simpler subtask kind. This function does not invent implementation plans or
    turn a complex coding request into instructions for an unqualified provider.
    """
    if subtask_kind not in ALLOWED_SUBTASKS:
        raise ValueError(f"subtask_kind must be one of {sorted(ALLOWED_SUBTASKS)}")
    normalized = tuple(scope.strip() for scope in scopes)
    if not normalized or len(normalized) > MAX_SCOPES:
        raise ValueError(f"scope count must be between 1 and {MAX_SCOPES}")
    if any(not scope or len(scope) > 512 for scope in normalized):
        raise ValueError("each scope must be non-empty and at most 512 characters")
    if len(set(normalized)) != len(normalized):
        raise ValueError("scopes must be unique")

    base_requirements = dict(task.requirements)
    for key in ("scope", "scopes", "subtask_kind", "reduce_kind", "mapped_outputs"):
        base_requirements.pop(key, None)
    estimate = task.local_estimate / len(normalized)
    return tuple(
        TaskEnvelope(
            task=subtask_kind,
            input_ref=task.input_ref,
            requirements={**base_requirements, "parent_task": str(task.task), "scope": scope},
            importance=task.importance,
            strategy=Strategy.SINGLE,
            max_cost=task.max_cost / len(normalized),
            local_estimate=estimate,
        )
        for scope in normalized
    )
