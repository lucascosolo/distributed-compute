"""Policy-first candidate discovery; candidates never become active implicitly."""

from __future__ import annotations

import json
import math
import subprocess
from dataclasses import asdict, dataclass, replace
from enum import Enum
from typing import Any, Callable
from urllib.parse import urlsplit


class CandidateState(str, Enum):
    QUARANTINED = "quarantined"
    PROBED = "probed"
    REJECTED = "rejected"
    APPROVED = "approved"


_PROHIBITED_ACCESS = (
    "bypass captcha", "captcha bypass", "bypass authentication", "auth bypass",
    "stolen session", "stolen credential", "hidden endpoint", "evade rate limit",
    "rate limit evasion", "bypass quota", "bypass safeguard",
)


@dataclass(frozen=True, slots=True)
class CandidateProvider:
    id: str
    name: str
    source: str
    transport: str
    endpoint: str
    terms_url: str
    authorization: str
    state: CandidateState = CandidateState.QUARANTINED
    rejection_reason: str | None = None

    def __post_init__(self) -> None:
        if not all(value.strip() for value in (self.id, self.name, self.source, self.transport, self.endpoint, self.authorization)):
            raise ValueError("candidate identity, source, transport, endpoint, and authorization are required")
        endpoint = urlsplit(self.endpoint)
        if endpoint.scheme not in {"http", "https"} or not endpoint.netloc:
            raise ValueError("candidate endpoint must be an absolute http or https URL")
        if endpoint.username or endpoint.password:
            raise ValueError("candidate endpoint must not contain credentials")
        if self.terms_url:
            terms = urlsplit(self.terms_url)
            if terms.scheme not in {"http", "https"} or not terms.netloc:
                raise ValueError("candidate terms_url must be an absolute http or https URL")
        if len(self.authorization) > 4_000:
            raise ValueError("candidate authorization basis is too large")


@dataclass(frozen=True, slots=True)
class ProbeResult:
    """Evidence from one harmless, operator-authorized quarantine probe."""

    candidate_id: str
    available: bool
    authorized: bool
    context_length: int
    output_valid: bool
    latency_ms: float
    restrictions_clear: bool
    cost_known: bool
    automation_supported: bool
    reason: str | None = None

    def __post_init__(self) -> None:
        if not self.candidate_id.strip():
            raise ValueError("probe candidate_id is required")
        if self.context_length < 0:
            raise ValueError("probe context_length cannot be negative")
        if not math.isfinite(float(self.latency_ms)) or self.latency_ms < 0:
            raise ValueError("probe latency_ms must be finite and non-negative")

    @property
    def passed(self) -> bool:
        return all((
            self.available,
            self.authorized,
            self.context_length > 0,
            self.output_valid,
            self.restrictions_clear,
            self.cost_known,
            self.automation_supported,
        ))

    def to_json(self) -> str:
        return json.dumps(asdict(self), sort_keys=True, separators=(",", ":"))


@dataclass(slots=True)
class CommandCandidateProbe:
    """Run one operator-owned, non-shell candidate probe.

    The command receives one JSON candidate record on stdin and must return one
    JSON ``ProbeResult`` object on stdout. It is the boundary for browser
    wrappers, including wrappers that use a native model to operate visible
    controls. Discovered URLs are data; this class never executes them.
    """

    command: tuple[str, ...]
    timeout_seconds: float = 120.0
    max_output_bytes: int = 64 * 1024

    def __post_init__(self) -> None:
        if not self.command:
            raise ValueError("candidate probe command is required")
        if self.timeout_seconds <= 0 or self.max_output_bytes <= 0:
            raise ValueError("candidate probe limits must be positive")

    def __call__(self, candidate: CandidateProvider) -> ProbeResult:
        payload = json.dumps(asdict(candidate), sort_keys=True, separators=(",", ":")).encode()
        try:
            completed = subprocess.run(
                self.command,
                input=payload,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=self.timeout_seconds,
                check=False,
                shell=False,
            )
        except subprocess.TimeoutExpired:
            return self._failed(candidate, "probe command timed out")
        except OSError:
            return self._failed(candidate, "probe command unavailable")
        if completed.returncode:
            return self._failed(candidate, "probe command failed")
        if len(completed.stdout) > self.max_output_bytes:
            return self._failed(candidate, "probe output exceeds limit")
        try:
            result = ProbeResult(**json.loads(completed.stdout))
            if result.candidate_id != candidate.id:
                raise ValueError("probe result candidate_id does not match candidate")
            return result
        except (TypeError, ValueError, KeyError, json.JSONDecodeError):
            return self._failed(candidate, "probe output is invalid")

    @staticmethod
    def _failed(candidate: CandidateProvider, reason: str) -> ProbeResult:
        return ProbeResult(
            candidate_id=candidate.id, available=False, authorized=False,
            context_length=0, output_valid=False, latency_ms=0.0,
            restrictions_clear=False, cost_known=False,
            automation_supported=False, reason=reason,
        )


def policy_rejection(candidate: CandidateProvider) -> str | None:
    text = f"{candidate.authorization} {candidate.source} {candidate.endpoint}".casefold()
    if any(marker in text for marker in _PROHIBITED_ACCESS):
        return "prohibited access or evasion language"
    return None


def promote_lead(
    registry: "CandidateRegistry",
    lead: Any,
    *,
    terms_review: str,
    terms_prohibited: bool = False,
) -> CandidateProvider:
    """Turn a lead with a concrete endpoint into a quarantined candidate.

    The operator supplies the terms-review record. The coordinator does not
    attempt to decide whether a provider's terms form a binding contract.
    """
    if not terms_review.strip():
        raise ValueError("terms review is required")
    if not lead.external_url:
        raise ValueError("lead has no external endpoint")
    candidate = CandidateProvider(
        id=f"discovered:{lead.lead_id}", name=lead.title, source=lead.source_url,
        transport=lead.transport_hint or "browser-chat", endpoint=lead.external_url,
        terms_url=lead.terms_url, authorization=terms_review,
        state=CandidateState.REJECTED if terms_prohibited else CandidateState.QUARANTINED,
        rejection_reason="explicit binding prohibition in reviewed terms" if terms_prohibited else None,
    )
    return registry.add(candidate)


class CandidateRegistry:
    """Candidate registry; optional Store persistence survives process restarts."""

    def __init__(self, store: Any | None = None) -> None:
        self._store = store
        self._candidates: dict[str, CandidateProvider] = {}
        self._probes: dict[str, ProbeResult] = {}
        if store is not None:
            for row in store.candidate_rows():
                candidate = CandidateProvider(
                    id=str(row["candidate_id"]), name=str(row["name"]),
                    source=str(row["source"]), transport=str(row["transport"]),
                    endpoint=str(row["endpoint"]), terms_url=str(row["terms_url"]),
                    authorization=str(row["authorization"]),
                    state=CandidateState(str(row["state"])),
                    rejection_reason=row["rejection_reason"],
                )
                self._candidates[candidate.id] = candidate
                probe_json = store.candidate_probe(candidate.id)
                if probe_json:
                    self._probes[candidate.id] = ProbeResult(**json.loads(probe_json))

    def add(self, candidate: CandidateProvider) -> CandidateProvider:
        if candidate.id in self._candidates:
            raise ValueError(f"candidate already exists: {candidate.id}")
        rejection = policy_rejection(candidate)
        if rejection:
            candidate = replace(candidate, state=CandidateState.REJECTED, rejection_reason=rejection)
        self._candidates[candidate.id] = candidate
        if self._store is not None:
            self._store.save_candidate(candidate)
        return candidate

    def get(self, candidate_id: str) -> CandidateProvider:
        try:
            return self._candidates[candidate_id]
        except KeyError as exc:
            raise KeyError(f"unknown candidate: {candidate_id}") from exc

    def all(self) -> tuple[CandidateProvider, ...]:
        return tuple(self._candidates.values())

    def activate(self, candidate_id: str, *, operator_approved: bool = False) -> CandidateProvider:
        candidate = self.get(candidate_id)
        if candidate.state == CandidateState.REJECTED:
            raise ValueError("rejected candidates cannot be activated")
        if not operator_approved:
            raise ValueError("explicit operator approval is required")
        probe = self._probes.get(candidate_id)
        if candidate.state != CandidateState.PROBED or probe is None or not probe.passed:
            raise ValueError("successful probe is required before activation")
        activated = replace(candidate, state=CandidateState.APPROVED)
        self._candidates[candidate_id] = activated
        if self._store is not None:
            self._store.save_candidate(activated)
        return activated

    def mark_probed(self, candidate_id: str, result: ProbeResult) -> CandidateProvider:
        candidate = self.get(candidate_id)
        if result.candidate_id != candidate_id:
            raise ValueError("probe result candidate_id does not match candidate")
        if candidate.state == CandidateState.REJECTED:
            raise ValueError("rejected candidates cannot be probed")
        self._probes[candidate_id] = result
        updated = replace(candidate, state=CandidateState.PROBED if result.passed else CandidateState.QUARANTINED)
        self._candidates[candidate_id] = updated
        if self._store is not None:
            self._store.save_candidate(updated)
            self._store.save_candidate_probe(candidate_id, result.to_json())
        return updated

    def probe_result(self, candidate_id: str) -> ProbeResult | None:
        return self._probes.get(candidate_id)


class QuarantineProbePipeline:
    """Run at most one injected, harmless probe per candidate in a bounded batch.

    The injected function owns the authorized transport. This class never fetches
    arbitrary endpoints or turns discovered metadata into executable code.
    """

    def __init__(self, registry: CandidateRegistry,
                 probe: Callable[[CandidateProvider], ProbeResult], *, max_candidates: int = 3) -> None:
        if max_candidates < 1:
            raise ValueError("max_candidates must be positive")
        self.registry = registry
        self.probe = probe
        self.max_candidates = max_candidates

    def run(self) -> tuple[ProbeResult, ...]:
        results: list[ProbeResult] = []
        candidates = [candidate for candidate in self.registry.all()
                      if candidate.state == CandidateState.QUARANTINED]
        for candidate in candidates[:self.max_candidates]:
            try:
                result = self.probe(candidate)
                if not isinstance(result, ProbeResult):
                    raise TypeError("probe must return ProbeResult")
                if result.candidate_id != candidate.id:
                    raise ValueError("probe result candidate_id does not match candidate")
            except Exception as exc:  # isolate one provider failure from the batch
                result = ProbeResult(
                    candidate_id=candidate.id, available=False, authorized=False,
                    context_length=0, output_valid=False, latency_ms=0.0,
                    restrictions_clear=False, cost_known=False,
                    automation_supported=False,
                    reason=f"probe failed: {type(exc).__name__}",
                )
            self.registry.mark_probed(candidate.id, result)
            results.append(result)
        return tuple(results)
