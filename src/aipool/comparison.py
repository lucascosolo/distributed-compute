"""Baseline-vs-coordinator measurements for controlled delegation experiments."""

from __future__ import annotations

import time
from dataclasses import asdict, dataclass
from typing import Callable, Iterable, Any

from .benchmark import BenchmarkCase
from .context import ContextPacket
from .domain import ProviderResult, TaskEnvelope
from .quality import validate_output
from .service import Coordinator
from .artifacts import ArtifactStore


BaselineRunner = Callable[[ContextPacket], str | ProviderResult]


@dataclass(frozen=True, slots=True)
class ComparisonRecord:
    case_name: str
    task_id: str
    baseline_output: str | None
    distributed_output: str | None
    baseline_valid: bool
    distributed_valid: bool
    native_fallback: bool
    baseline_latency_ms: float
    distributed_latency_ms: float
    distributed_cost: float
    local_estimate: float
    context_chars: int

    @property
    def distributed_cheaper(self) -> bool:
        return (
            not self.native_fallback
            and self.distributed_valid
            and self.local_estimate > 0
            and self.distributed_cost < self.local_estimate
        )


@dataclass(frozen=True, slots=True)
class ComparisonReport:
    records: tuple[ComparisonRecord, ...]

    @property
    def baseline_valid_rate(self) -> float:
        return sum(record.baseline_valid for record in self.records) / len(self.records)

    @property
    def distributed_valid_rate(self) -> float:
        return sum(record.distributed_valid for record in self.records) / len(self.records)

    @property
    def quality_regressions(self) -> int:
        return sum(record.baseline_valid and not record.distributed_valid for record in self.records)

    @property
    def distributed_cheaper_count(self) -> int:
        return sum(record.distributed_cheaper for record in self.records)

    @property
    def total_distributed_cost(self) -> float:
        return sum(record.distributed_cost for record in self.records)

    def to_dict(self) -> dict[str, Any]:
        return {
            "baseline_valid_rate": self.baseline_valid_rate,
            "distributed_valid_rate": self.distributed_valid_rate,
            "quality_regressions": self.quality_regressions,
            "distributed_cheaper_count": self.distributed_cheaper_count,
            "total_distributed_cost": self.total_distributed_cost,
            "records": [
                {**asdict(record), "distributed_cheaper": record.distributed_cheaper}
                for record in self.records
            ],
        }


def _valid(case: BenchmarkCase, output: str | None) -> bool:
    if not isinstance(output, str):
        return False
    task_kind = case.task.task.value if hasattr(case.task.task, "value") else str(case.task.task)
    report = validate_output(
        output,
        require_json=case.task.requirements.get("output") == "json",
        task=task_kind,
    )
    return report.valid and bool(case.accepts(output))


def run_comparison(
    cases: Iterable[BenchmarkCase],
    baseline: BaselineRunner,
    coordinator: Coordinator,
    *,
    artifacts: ArtifactStore | None = None,
) -> ComparisonReport:
    """Run each case once through a native runner and once through the pool.

    The same :class:`ContextPacket` is given to the baseline runner that the
    browser/API adapters can render, while the coordinator receives the
    original task envelope. This makes context-transfer loss measurable rather
    than silently comparing different inputs.
    """
    selected = tuple(cases)
    if not selected or len(selected) > 32:
        raise ValueError("comparison must contain between 1 and 32 cases")
    names = [case.name for case in selected]
    if len(set(names)) != len(names):
        raise ValueError("comparison case names must be unique")

    records: list[ComparisonRecord] = []
    for case in selected:
        packet = ContextPacket.from_task(case.task, artifacts)
        baseline_started = time.monotonic()
        try:
            baseline_result = baseline(packet)
            if isinstance(baseline_result, ProviderResult):
                baseline_output = baseline_result.output if baseline_result.success else None
            elif isinstance(baseline_result, str):
                baseline_output = baseline_result
            else:
                baseline_output = None
        except Exception:
            baseline_output = None
        baseline_latency = (time.monotonic() - baseline_started) * 1000

        distributed_started = time.monotonic()
        outcome = coordinator.submit(case.task)
        distributed_latency = (time.monotonic() - distributed_started) * 1000
        records.append(ComparisonRecord(
            case_name=case.name,
            task_id=case.task.task_id,
            baseline_output=baseline_output,
            distributed_output=outcome.output,
            baseline_valid=_valid(case, baseline_output),
            distributed_valid=bool(
                not outcome.native_fallback and outcome.success and _valid(case, outcome.output)
            ),
            native_fallback=outcome.native_fallback,
            baseline_latency_ms=baseline_latency,
            distributed_latency_ms=distributed_latency,
            distributed_cost=outcome.orchestration_cost,
            local_estimate=case.task.local_estimate,
            context_chars=len(packet.render()),
        ))
    return ComparisonReport(tuple(records))
