"""Policy-first candidate discovery; candidates never become active implicitly."""

from __future__ import annotations

from dataclasses import dataclass, replace
from enum import Enum
from urllib.parse import urlsplit


class CandidateState(str, Enum):
    QUARANTINED = "quarantined"
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


def policy_rejection(candidate: CandidateProvider) -> str | None:
    text = f"{candidate.authorization} {candidate.source} {candidate.endpoint}".casefold()
    if any(marker in text for marker in _PROHIBITED_ACCESS):
        return "prohibited access or evasion language"
    return None


class CandidateRegistry:
    """In-memory quarantine registry for explicitly sourced candidates."""

    def __init__(self) -> None:
        self._candidates: dict[str, CandidateProvider] = {}

    def add(self, candidate: CandidateProvider) -> CandidateProvider:
        if candidate.id in self._candidates:
            raise ValueError(f"candidate already exists: {candidate.id}")
        rejection = policy_rejection(candidate)
        if rejection:
            candidate = replace(candidate, state=CandidateState.REJECTED, rejection_reason=rejection)
        self._candidates[candidate.id] = candidate
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
        activated = replace(candidate, state=CandidateState.APPROVED)
        self._candidates[candidate_id] = activated
        return activated
