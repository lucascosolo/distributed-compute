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
            TaskEnvelope(
                task="classification",
                input_ref="Synthetic text: The package was released under an open-source license.",
                requirements={
                    "objective": "Classify the text as documentation, code, or other. Return one JSON object with a label field.",
                    "output": "json",
                },
            ),
            lambda output: output.strip().startswith("{") or output.strip().startswith("["),
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


def run_benchmark(adapter: ProviderAdapter, cases: Iterable[BenchmarkCase] | None = None) -> BenchmarkResult:
    selected = tuple(cases or default_cases())
    if not selected or len(selected) > 32:
        raise ValueError("benchmark must contain between 1 and 32 cases")
    observations: dict[str, list[float]] = {}
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
        capabilities = [case.capability]
        if case.task.requirements.get("output") == "json":
            capabilities.append("structured_json")
        for capability in capabilities:
            observations.setdefault(capability, []).append(float(passed))
    scores = {
        capability: sum(values) / len(values)
        for capability, values in observations.items()
    }
    return BenchmarkResult(adapter.profile.id, scores, len(selected), valid)
