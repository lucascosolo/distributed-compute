"""Small, bounded capability benchmark runner."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Iterable

from .domain import ProviderErrorKind, TaskEnvelope
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
    stopped_error: ProviderErrorKind | None = None
    retry_after_seconds: float | None = None
    failure_kind: ProviderErrorKind | None = None
    failure_reason: str | None = None


def default_cases() -> tuple[BenchmarkCase, ...]:
    return (
        BenchmarkCase(
            "classification_json", "classification",
            TaskEnvelope(
                task="classification",
                input_ref="Synthetic text: The package was released under an open-source license.",
                requirements={
                    "objective": "Classify the text as documentation, code, or other. Return one JSON object with a label field.",
                    "output": "json",
                },
            ),
            lambda output: bool(output.strip()),
        ),
        BenchmarkCase(
            "extraction_json", "extraction",
            TaskEnvelope(
                task="extraction",
                input_ref="Synthetic record: Name: Ada Lovelace; Role: mathematician.",
                requirements={
                    "objective": "Extract the person's name and role as one JSON object.",
                    "output": "json",
                },
            ),
            lambda output: "name" in output.casefold() or "value" in output.casefold(),
        ),
        BenchmarkCase(
            "summary", "summarization",
            TaskEnvelope(
                task="summarization",
                input_ref="Synthetic memo: The small library moved its release process to weekly automated builds. Maintainers reported faster fixes and fewer manual deployment errors.",
                requirements={"objective": "Summarize the memo in one or two concise sentences."},
            ),
            lambda output: len(output.strip()) >= 20,
        ),
    )


def route_cases(route_id: str, count: int = 1) -> tuple[BenchmarkCase, ...]:
    """Choose bounded smoke cases that match a task-specialized route."""
    if not 1 <= count <= 3:
        raise ValueError("benchmark case count must be between 1 and 3")
    route = route_id.casefold()
    if "coding" in route:
        cases = (
            BenchmarkCase(
                "coding_patch", "coding",
                TaskEnvelope(
                    task="coding",
                    input_ref="Synthetic Python: def add(a, b): return a - b",
                    requirements={"objective": "Return only the corrected function and no explanation."},
                ),
                lambda output: "def" in output and "return" in output,
            ),
        )
    elif "reasoning" in route or "pro-reasoning" in route:
        cases = (
            BenchmarkCase(
                "review_finding", "reasoning",
                TaskEnvelope(
                    task="review",
                    input_ref="Synthetic code: if user_id == admin_id: grant_admin()",
                    requirements={"objective": "Identify the security problem in one concise sentence."},
                ),
                lambda output: len(output.strip()) >= 20,
            ),
        )
    else:
        cases = default_cases()
    return cases[:count]


def run_benchmark(adapter: ProviderAdapter, cases: Iterable[BenchmarkCase] | None = None) -> BenchmarkResult:
    selected = tuple(cases or default_cases())
    if not selected or len(selected) > 32:
        raise ValueError("benchmark must contain between 1 and 32 cases")
    observations: dict[str, list[float]] = {}
    valid = 0
    stopped_error: ProviderErrorKind | None = None
    retry_after_seconds: float | None = None
    failure_kind: ProviderErrorKind | None = None
    failure_reason: str | None = None
    attempts = 0
    for case in selected:
        attempts += 1
        result = adapter.complete(case.task)
        if not result.success and failure_reason is None:
            failure_kind = result.error_kind or ProviderErrorKind.INTERNAL
            failure_reason = (result.error or "provider returned an unsuccessful result")[:300]
        report = validate_output(
            result.output,
            require_json=case.task.requirements.get("output") == "json",
            task=case.task.task.value if hasattr(case.task.task, "value") else str(case.task.task),
        ) if result.success else None
        passed = bool(report and report.valid and case.accepts(result.output))
        if not passed and failure_reason is None:
            failure_kind = result.error_kind or ProviderErrorKind.INTERNAL
            failure_reason = f"benchmark validation: {report.reason if report else 'provider returned unsuccessful result'}"
        if passed:
            valid += 1
        capabilities = [case.capability]
        if case.task.requirements.get("output") == "json":
            capabilities.append("structured_json")
        for capability in capabilities:
            observations.setdefault(capability, []).append(float(passed))
        if result.error_kind in {ProviderErrorKind.RATE_LIMITED, ProviderErrorKind.AUTH}:
            stopped_error = result.error_kind
            retry_after_seconds = result.retry_after_seconds
            break
    scores = {
        capability: sum(values) / len(values)
        for capability, values in observations.items()
    }
    return BenchmarkResult(
        adapter.profile.id, scores, attempts, valid,
        stopped_error=stopped_error, retry_after_seconds=retry_after_seconds,
        failure_kind=failure_kind, failure_reason=failure_reason,
    )
