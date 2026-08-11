"""Stable, serializable contracts shared by routing, adapters, and transports."""

from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any, Mapping


class TaskKind(str, Enum):
    INVENTORY = "inventory"
    CLASSIFICATION = "classification"
    EXTRACTION = "extraction"
    SUMMARIZATION = "summarization"
    CODING = "coding"
    REVIEW = "review"
    RESEARCH = "research"


class Strategy(str, Enum):
    SINGLE = "single"
    VERIFY = "verify"
    CONSENSUS = "consensus"
    MAP = "map"
    MAP_REDUCE = "map_reduce"
    CASCADE = "cascade"
    NO_DELEGATION = "no_delegation"


class ProviderState(str, Enum):
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    RATE_LIMITED = "rate_limited"
    AUTH_REQUIRED = "auth_required"
    BROKEN = "broken"
    QUARANTINED = "quarantined"
    DISABLED = "disabled"


class ProviderErrorKind(str, Enum):
    TIMEOUT = "timeout"
    RATE_LIMITED = "rate_limited"
    AUTH = "auth"
    UNAVAILABLE = "unavailable"
    INVALID_REQUEST = "invalid_request"
    INTERNAL = "internal"


def _normalize(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(k): _normalize(value[k]) for k in sorted(value, key=str)}
    if isinstance(value, (list, tuple)):
        return [_normalize(item) for item in value]
    if isinstance(value, Enum):
        return value.value
    return value


_SECRET_KEY = re.compile(r"(?:key|token|secret|password|credential|authorization)", re.I)


def _reject_secrets(value: Any, path: str = "root") -> None:
    if isinstance(value, Mapping):
        for key, item in value.items():
            if _SECRET_KEY.search(str(key)):
                raise ValueError(f"secret-like field is not allowed in task data: {path}.{key}")
            _reject_secrets(item, f"{path}.{key}")
    elif isinstance(value, (list, tuple)):
        for index, item in enumerate(value):
            _reject_secrets(item, f"{path}[{index}]")


@dataclass(frozen=True, slots=True)
class TaskEnvelope:
    task: TaskKind | str
    input_ref: str
    requirements: Mapping[str, Any] = field(default_factory=dict)
    importance: int = 1
    strategy: Strategy = Strategy.SINGLE
    max_cost: float = 0.0
    local_estimate: float = 0.0
    task_id: str = ""

    def __post_init__(self) -> None:
        if not self.input_ref or len(self.input_ref) > 4096:
            raise ValueError("input_ref must be non-empty and at most 4096 characters")
        if not 1 <= self.importance <= 5:
            raise ValueError("importance must be between 1 and 5")
        if self.max_cost < 0 or self.local_estimate < 0:
            raise ValueError("cost estimates cannot be negative")
        _reject_secrets(self.requirements)
        if not self.task_id:
            object.__setattr__(self, "task_id", self.stable_id())

    def normalized(self) -> dict[str, Any]:
        return _normalize(
            {
                "task": self.task.value if isinstance(self.task, Enum) else self.task,
                "input_ref": self.input_ref,
                "requirements": self.requirements,
                "importance": self.importance,
                "strategy": self.strategy.value,
                "max_cost": self.max_cost,
                "local_estimate": self.local_estimate,
            }
        )

    def stable_id(self) -> str:
        payload = json.dumps(self.normalized(), sort_keys=True, separators=(",", ":"))
        return "task_" + hashlib.sha256(payload.encode()).hexdigest()[:24]

    def to_dict(self) -> dict[str, Any]:
        return {**self.normalized(), "task_id": self.task_id}

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "TaskEnvelope":
        return cls(
            task=TaskKind(data["task"]) if data["task"] in TaskKind._value2member_map_ else str(data["task"]),
            input_ref=str(data["input_ref"]),
            requirements=dict(data.get("requirements", {})),
            importance=int(data.get("importance", 1)),
            strategy=Strategy(data.get("strategy", Strategy.SINGLE.value)),
            max_cost=float(data.get("max_cost", 0.0)),
            local_estimate=float(data.get("local_estimate", 0.0)),
            task_id=str(data.get("task_id", "")),
        )


@dataclass(frozen=True, slots=True)
class ProviderProfile:
    id: str
    name: str
    transport: str
    capabilities: Mapping[str, float] = field(default_factory=dict)
    context_limit: int = 0
    latency_ms: float = 0.0
    reliability: float = 0.0
    estimated_cost: float = 0.0
    quota_weight: float = 1.0
    concurrency_limit: int = 1
    state: ProviderState = ProviderState.QUARANTINED
    max_complexity: int = 1
    request_limit: int = 0
    token_limit: int = 0
    usage_window_seconds: float = 60.0

    def __post_init__(self) -> None:
        if not self.id or not self.name or not self.transport:
            raise ValueError("provider id, name, and transport are required")
        if self.context_limit < 0 or self.concurrency_limit < 1:
            raise ValueError("provider limits are invalid")
        for field_name in ("latency_ms", "estimated_cost", "quota_weight"):
            value = float(getattr(self, field_name))
            if not math.isfinite(value) or value < 0:
                raise ValueError(f"{field_name} must be a finite non-negative number")
        if self.request_limit < 0 or self.token_limit < 0 or self.usage_window_seconds <= 0:
            raise ValueError("provider usage limits are invalid")
        if not 1 <= self.max_complexity <= 5:
            raise ValueError("max_complexity must be between 1 and 5")
        for field_name in ("reliability",):
            value = getattr(self, field_name)
            if not 0 <= value <= 1:
                raise ValueError(f"{field_name} must be between 0 and 1")
        for capability, score in self.capabilities.items():
            value = float(score)
            if not capability or not math.isfinite(value) or not 0 <= value <= 1:
                raise ValueError(f"capability score for {capability!r} must be between 0 and 1")


@dataclass(frozen=True, slots=True)
class ProviderResult:
    provider_id: str
    output: str = ""
    success: bool = True
    error_kind: ProviderErrorKind | None = None
    error: str | None = None
    latency_ms: float = 0.0
    worker_tokens: int = 0
    retry_after_seconds: float | None = None


@dataclass(frozen=True, slots=True)
class TaskOutcome:
    task_id: str
    strategy: Strategy
    provider_id: str | None
    output: str | None
    success: bool
    valid: bool
    reason: str | None = None
    orchestration_cost: float = 0.0
    delegated_compute_saved: float = 0.0
    worker_tokens: int = 0
    native_fallback: bool = False
