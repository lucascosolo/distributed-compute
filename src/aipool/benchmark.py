"""Small, bounded capability benchmark runner."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Iterable

from .domain import TaskEnvelope
from .providers import ProviderAdapter
from .quality import validate_output


@dataclass(frozen=True, slots=True)
class BenchmarkCase:
    name: str
    capability: str
    task: TaskEnvelope
    accepts: Callable[[str], bool]


@dataclass(frozen=True, slots=True)
class BenchmarkResult:
    provider_id: str
    scores: dict[str, float]
    attempts: int
    valid: int


def default_cases() -> tuple[BenchmarkCase, ...]:
    return (
        BenchmarkCase(
            "classification_json", "classification",
            TaskEnvelope(task="classification", input_ref="benchmark:classification", requirements={"output": "json"}),
            lambda output: output.strip().startswith("{") or output.strip().startswith("["),
        ),
        BenchmarkCase(
            "extraction_json", "extraction",
            TaskEnvelope(task="extraction", input_ref="benchmark:extraction", requirements={"output": "json"}),
            lambda output: "name" in output.casefold() or "value" in output.casefold(),
        ),
        BenchmarkCase(
            "summary", "summarization",
            TaskEnvelope(task="summarization", input_ref="benchmark:summary"),
            lambda output: len(output.strip()) >= 20,
        ),
    )


def run_benchmark(adapter: ProviderAdapter, cases: Iterable[BenchmarkCase] | None = None) -> BenchmarkResult:
    selected = tuple(cases or default_cases())
    scores: dict[str, float] = {}
    valid = 0
    for case in selected:
        result = adapter.complete(case.task)
        report = validate_output(
            result.output,
            require_json=case.task.requirements.get("output") == "json",
            task=case.task.task.value if hasattr(case.task.task, "value") else str(case.task.task),
        ) if result.success else None
        passed = bool(report and report.valid and case.accepts(result.output))
        if passed:
            valid += 1
        scores[case.capability] = scores.get(case.capability, 0.0) + float(passed)
    for capability in scores:
        scores[capability] /= sum(1 for case in selected if case.capability == capability)
    return BenchmarkResult(adapter.profile.id, scores, len(selected), valid)
