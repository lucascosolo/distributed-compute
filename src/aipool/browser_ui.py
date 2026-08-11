"""Strict contracts for model-guided interaction with a browser chat UI."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Protocol


UIActionKind = Literal["click", "select", "fill", "submit", "wait"]
TASK_PROMPT_TOKEN = "__AIPOOL_PROMPT__"


@dataclass(frozen=True, slots=True)
class UIAction:
    kind: UIActionKind
    target: str = ""
    value: str = ""

    def __post_init__(self) -> None:
        if self.kind not in {"click", "select", "fill", "submit", "wait"}:
            raise ValueError("unsupported browser UI action")
        if self.kind != "wait" and not self.target.strip():
            raise ValueError("browser UI action target is required")
        if len(self.target) > 256 or len(self.value) > 16_000:
            raise ValueError("browser UI action is too large")
        target = self.target.casefold()
        if any(marker in target for marker in ("password", "credential", "secret", "token", "api key")):
            raise ValueError("browser UI action cannot target a credential control")
        if self.kind == "wait":
            try:
                seconds = float(self.value or self.target or "0")
            except ValueError as exc:
                raise ValueError("wait duration must be numeric") from exc
            if not 0 <= seconds <= 10:
                raise ValueError("wait duration must be between 0 and 10 seconds")


@dataclass(frozen=True, slots=True)
class UIPlan:
    actions: tuple[UIAction, ...]

    def __post_init__(self) -> None:
        if len(self.actions) > 8:
            raise ValueError("browser UI plan supports at most 8 actions")
        if not all(isinstance(action, UIAction) for action in self.actions):
            raise ValueError("browser UI plan contains an invalid action")


@dataclass(frozen=True, slots=True)
class UIPlannerRequest:
    prompt: str
    snapshot: str
    instruction: str

    def __post_init__(self) -> None:
        if not self.prompt.strip() or not self.snapshot.strip():
            raise ValueError("UI planner requires prompt and page snapshot")
        if len(self.snapshot) > 64_000:
            raise ValueError("browser page snapshot exceeds limit")


class BrowserSession(Protocol):
    """Operator-owned browser session with no credential or navigation API.

    A fresh isolated profile may be used for privacy and reproducibility, but
    callers must not rotate profiles or clear state to evade provider limits.
    """

    def snapshot(self) -> str: ...
    def click(self, target: str) -> None: ...
    def select(self, target: str, value: str) -> None: ...
    def fill(self, target: str, value: str) -> None: ...
    def submit(self) -> None: ...
    def wait(self, seconds: float) -> None: ...
    def read_response(self) -> str: ...
