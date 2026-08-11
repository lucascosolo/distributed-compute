"""Provider adapters. Transport-specific behavior stays in this module."""

from __future__ import annotations

import json
import os
import subprocess
import time
from dataclasses import dataclass
from typing import Callable, Mapping, Protocol
from urllib import error, request

from .artifacts import ArtifactStore
from .browser_ui import BrowserSession, TASK_PROMPT_TOKEN, UIAction, UIPlan, UIPlannerRequest
from .context import ContextPacket
from .domain import (
    ProviderErrorKind,
    ProviderProfile,
    ProviderResult,
    TaskEnvelope,
)


class ProviderAdapter(Protocol):
    profile: ProviderProfile

    def complete(self, task: TaskEnvelope) -> ProviderResult:
        """Complete one task without acquiring tools or interpreting output."""


def _failure(
    provider_id: str,
    kind: ProviderErrorKind,
    message: str,
    latency_ms: float,
    retry_after_seconds: float | None = None,
) -> ProviderResult:
    return ProviderResult(
        provider_id=provider_id,
        success=False,
        error_kind=kind,
        error=message[:500],
        latency_ms=latency_ms,
        retry_after_seconds=retry_after_seconds,
    )


def _is_login_wall(output: str) -> bool:
    """Recognize common browser auth walls without classifying normal answers."""
    text = output.strip().casefold()
    if not text:
        return False
    exact_phrases = (
        "sign in to continue", "log in to continue", "login required",
        "authentication required", "create an account to continue",
        "sign up to continue", "please log in", "please sign in",
    )
    if any(phrase in text for phrase in exact_phrases):
        return True
    return len(text) < 2_000 and ("<html" in text or "<!doctype" in text) and any(
        phrase in text for phrase in ("sign in", "log in", "login", "register")
    )


@dataclass(slots=True)
class FixtureAdapter:
    profile: ProviderProfile
    handler: Callable[[TaskEnvelope], str | ProviderResult]

    def complete(self, task: TaskEnvelope) -> ProviderResult:
        started = time.monotonic()
        try:
            result = self.handler(task)
            if isinstance(result, ProviderResult):
                return result
            return ProviderResult(
                provider_id=self.profile.id,
                output=str(result),
                latency_ms=(time.monotonic() - started) * 1000,
            )
        except Exception as exc:  # fixture failures must use the same boundary as real providers
            return _failure(
                self.profile.id,
                ProviderErrorKind.INTERNAL,
                str(exc),
                (time.monotonic() - started) * 1000,
            )


@dataclass(slots=True)
class CommandAdapter:
    profile: ProviderProfile
    command: tuple[str, ...]
    timeout_seconds: float = 30.0
    max_output_bytes: int = 1_000_000

    def complete(self, task: TaskEnvelope) -> ProviderResult:
        started = time.monotonic()
        if not self.command:
            return _failure(self.profile.id, ProviderErrorKind.UNAVAILABLE, "command is not configured", 0)
        payload = json.dumps(task.to_dict(), sort_keys=True).encode()
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
            return _failure(
                self.profile.id,
                ProviderErrorKind.TIMEOUT,
                "provider command timed out",
                (time.monotonic() - started) * 1000,
            )
        except OSError as exc:
            return _failure(
                self.profile.id,
                ProviderErrorKind.UNAVAILABLE,
                str(exc),
                (time.monotonic() - started) * 1000,
            )

        latency = (time.monotonic() - started) * 1000
        if completed.returncode:
            message = completed.stderr.decode(errors="replace").strip() or "provider command failed"
            return _failure(self.profile.id, ProviderErrorKind.INTERNAL, message, latency)
        if len(completed.stdout) > self.max_output_bytes:
            return _failure(self.profile.id, ProviderErrorKind.INTERNAL, "provider output exceeds limit", latency)
        return ProviderResult(provider_id=self.profile.id, output=completed.stdout.decode(errors="replace"), latency_ms=latency)


@dataclass(slots=True)
class AgentCommandAdapter:
    """Run an operator-owned Claude/Codex-style agent command.

    This is deliberately a local command bridge, not a remote provider API.
    The wrapper receives a bounded JSON envelope and must return only the task
    result on stdout. Credentials remain in the wrapper's local environment.
    """

    profile: ProviderProfile
    command: tuple[str, ...]
    timeout_seconds: float = 120.0
    max_output_bytes: int = 1_000_000

    def complete(self, task: TaskEnvelope) -> ProviderResult:
        started = time.monotonic()
        if not self.command:
            return _failure(self.profile.id, ProviderErrorKind.UNAVAILABLE, "agent command is not configured", 0)
        chain = tuple(task.delegation_chain)
        if self.profile.id not in chain:
            chain = (*chain, self.profile.id)
        delegated_task = TaskEnvelope(
            task=task.task, input_ref=task.input_ref, requirements=task.requirements,
            importance=task.importance, strategy=task.strategy, max_cost=task.max_cost,
            local_estimate=task.local_estimate, origin_provider_id=self.profile.id,
            delegation_chain=chain,
        )
        payload = json.dumps({
            "task": delegated_task.to_dict(),
            "bridge": {"provider_id": self.profile.id, "transport": self.profile.transport},
        }, sort_keys=True, separators=(",", ":")).encode()
        try:
            completed = subprocess.run(
                self.command, input=payload, stdout=subprocess.PIPE,
                stderr=subprocess.PIPE, timeout=self.timeout_seconds,
                check=False, shell=False,
            )
        except subprocess.TimeoutExpired:
            return _failure(self.profile.id, ProviderErrorKind.TIMEOUT, "agent command timed out", (time.monotonic() - started) * 1000)
        except OSError as exc:
            return _failure(self.profile.id, ProviderErrorKind.UNAVAILABLE, str(exc), (time.monotonic() - started) * 1000)
        latency = (time.monotonic() - started) * 1000
        if completed.returncode:
            message = completed.stderr.decode(errors="replace").strip() or "agent command failed"
            return _failure(self.profile.id, ProviderErrorKind.INTERNAL, message, latency)
        if len(completed.stdout) > self.max_output_bytes:
            return _failure(self.profile.id, ProviderErrorKind.INTERNAL, "agent output exceeds limit", latency)
        return ProviderResult(provider_id=self.profile.id, output=completed.stdout.decode(errors="replace"), latency_ms=latency)


@dataclass(slots=True)
class CandidateCommandAdapter:
    """Run an operator-owned wrapper for one approved candidate.

    The wrapper receives candidate metadata and a task envelope as JSON. This
    supports authorized community-bot bridges without making any platform
    part of the coordinator core.
    """

    profile: ProviderProfile
    candidate_metadata: Mapping[str, object]
    command: tuple[str, ...]
    timeout_seconds: float = 120.0
    max_output_bytes: int = 1_000_000

    def complete(self, task: TaskEnvelope) -> ProviderResult:
        started = time.monotonic()
        if not self.command:
            return _failure(self.profile.id, ProviderErrorKind.UNAVAILABLE, "candidate command is not configured", 0)
        payload = json.dumps({
            "candidate": dict(self.candidate_metadata), "task": task.to_dict(),
        }, sort_keys=True, separators=(",", ":")).encode()
        try:
            completed = subprocess.run(
                self.command, input=payload, stdout=subprocess.PIPE,
                stderr=subprocess.PIPE, timeout=self.timeout_seconds,
                check=False, shell=False,
            )
        except subprocess.TimeoutExpired:
            return _failure(self.profile.id, ProviderErrorKind.TIMEOUT, "candidate command timed out", (time.monotonic() - started) * 1000)
        except OSError as exc:
            return _failure(self.profile.id, ProviderErrorKind.UNAVAILABLE, str(exc), (time.monotonic() - started) * 1000)
        latency = (time.monotonic() - started) * 1000
        if completed.returncode:
            return _failure(self.profile.id, ProviderErrorKind.INTERNAL, "candidate command failed", latency)
        if len(completed.stdout) > self.max_output_bytes:
            return _failure(self.profile.id, ProviderErrorKind.INTERNAL, "candidate output exceeds limit", latency)
        return ProviderResult(
            provider_id=self.profile.id,
            output=completed.stdout.decode(errors="replace"),
            latency_ms=latency,
        )


@dataclass(slots=True)
class BrowserChatAdapter:
    """Adapter seam for a reviewed public web chat UI.

    ``submit`` is supplied by an operator-owned browser session or a local
    wrapper. The coordinator never logs in, bypasses a challenge, discovers
    hidden endpoints, or decides that a public page overrides the provider's
    terms or applicable law.
    """

    profile: ProviderProfile
    submit: Callable[[str], str]
    artifacts: ArtifactStore | None = None
    max_prompt_chars: int = 12_000
    max_output_chars: int = 1_000_000

    def complete(self, task: TaskEnvelope) -> ProviderResult:
        started = time.monotonic()
        try:
            packet = ContextPacket.from_task(task, self.artifacts, max_chars=self.max_prompt_chars)
            output = self.submit(packet.render())
            if not isinstance(output, str):
                raise ValueError("browser transport must return text")
            if _is_login_wall(output):
                return _failure(
                    self.profile.id, ProviderErrorKind.AUTH,
                    "browser session reached a login wall", (time.monotonic() - started) * 1000,
                )
            if len(output) > self.max_output_chars:
                return _failure(
                    self.profile.id, ProviderErrorKind.INTERNAL,
                    "provider output exceeds limit", (time.monotonic() - started) * 1000,
                )
            return ProviderResult(
                provider_id=self.profile.id, output=output,
                latency_ms=(time.monotonic() - started) * 1000,
            )
        except Exception as exc:
            return _failure(
                self.profile.id, ProviderErrorKind.INTERNAL, str(exc),
                (time.monotonic() - started) * 1000,
            )


@dataclass(slots=True)
class BrowserCommandAdapter:
    """Run an operator-supplied browser wrapper with a rendered chat prompt."""

    profile: ProviderProfile
    command: tuple[str, ...]
    artifacts: ArtifactStore | None = None
    timeout_seconds: float = 120.0
    max_prompt_chars: int = 12_000
    max_output_bytes: int = 1_000_000

    def complete(self, task: TaskEnvelope) -> ProviderResult:
        started = time.monotonic()
        if not self.command:
            return _failure(self.profile.id, ProviderErrorKind.UNAVAILABLE, "browser command is not configured", 0)
        try:
            prompt = ContextPacket.from_task(task, self.artifacts, max_chars=self.max_prompt_chars).render()
            completed = subprocess.run(
                self.command,
                input=prompt.encode(),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=self.timeout_seconds,
                check=False,
                shell=False,
            )
        except subprocess.TimeoutExpired:
            return _failure(self.profile.id, ProviderErrorKind.TIMEOUT, "browser wrapper timed out", (time.monotonic() - started) * 1000)
        except (OSError, ValueError) as exc:
            return _failure(self.profile.id, ProviderErrorKind.UNAVAILABLE, str(exc), (time.monotonic() - started) * 1000)
        latency = (time.monotonic() - started) * 1000
        if completed.returncode:
            message = completed.stderr.decode(errors="replace").strip() or "browser wrapper failed"
            return _failure(self.profile.id, ProviderErrorKind.INTERNAL, message, latency)
        if len(completed.stdout) > self.max_output_bytes:
            return _failure(self.profile.id, ProviderErrorKind.INTERNAL, "provider output exceeds limit", latency)
        output = completed.stdout.decode(errors="replace")
        if _is_login_wall(output):
            return _failure(self.profile.id, ProviderErrorKind.AUTH, "browser session reached a login wall", latency)
        return ProviderResult(
            provider_id=self.profile.id,
            output=output,
            latency_ms=latency,
        )


@dataclass(slots=True)
class ModelGuidedBrowserAdapter:
    """Use the native model to operate visible, bounded chat-UI controls.

    The planner may select a model and visible options, but it cannot navigate,
    authenticate, execute JavaScript, or access credentials. Operators should
    account for planner-model cost in the profile's estimated cost.
    """

    profile: ProviderProfile
    session: BrowserSession
    planner: Callable[[UIPlannerRequest], UIPlan]
    artifacts: ArtifactStore | None = None
    max_prompt_chars: int = 12_000
    max_planner_calls: int = 2
    max_actions: int = 8

    def __post_init__(self) -> None:
        if self.max_planner_calls < 1 or self.max_actions < 1:
            raise ValueError("browser planner and action budgets must be positive")

    def complete(self, task: TaskEnvelope) -> ProviderResult:
        started = time.monotonic()
        try:
            packet = ContextPacket.from_task(task, self.artifacts, max_chars=self.max_prompt_chars)
            prompt = packet.render()
            actions_used = 0
            submitted = False
            for _ in range(self.max_planner_calls):
                snapshot = self.session.snapshot()
                if _is_login_wall(snapshot):
                    return _failure(self.profile.id, ProviderErrorKind.AUTH, "browser session reached a login wall", (time.monotonic() - started) * 1000)
                plan = self.planner(UIPlannerRequest(
                    prompt=prompt,
                    snapshot=snapshot,
                    instruction=(
                        "Use only visible controls to select the best available model/options, "
                        "fill the task prompt, and submit it. Never log in, register, or bypass a limit. "
                        "Use __AIPOOL_PROMPT__ as the fill value for the task prompt."
                    ),
                ))
                if not isinstance(plan, UIPlan):
                    raise ValueError("browser planner must return UIPlan")
                if actions_used + len(plan.actions) > self.max_actions:
                    raise ValueError("browser action budget exceeded")
                for action in plan.actions:
                    actions_used += 1
                    self._apply(action, prompt)
                    if action.kind == "submit":
                        submitted = True
                if submitted:
                    break
            if not submitted:
                return _failure(self.profile.id, ProviderErrorKind.INVALID_REQUEST, "browser planner did not submit the task", (time.monotonic() - started) * 1000)
            output = self.session.read_response()
            if not isinstance(output, str):
                raise ValueError("browser session must return text")
            if _is_login_wall(output):
                return _failure(self.profile.id, ProviderErrorKind.AUTH, "browser session reached a login wall", (time.monotonic() - started) * 1000)
            return ProviderResult(self.profile.id, output=output, latency_ms=(time.monotonic() - started) * 1000)
        except Exception as exc:
            return _failure(self.profile.id, ProviderErrorKind.INTERNAL, str(exc), (time.monotonic() - started) * 1000)

    def _apply(self, action: UIAction, prompt: str) -> None:
        value = prompt if action.value == TASK_PROMPT_TOKEN else action.value
        if action.kind == "click":
            self.session.click(action.target)
        elif action.kind == "select":
            self.session.select(action.target, value)
        elif action.kind == "fill":
            if action.value != TASK_PROMPT_TOKEN:
                raise ValueError("browser prompt fields must use the task prompt token")
            self.session.fill(action.target, value)
        elif action.kind == "submit":
            self.session.submit()
        else:
            seconds = float(action.value or action.target or "0")
            self.session.wait(seconds)


@dataclass(slots=True)
class OpenAICompatibleAdapter:
    profile: ProviderProfile
    endpoint: str
    model: str
    api_key_env: str
    timeout_seconds: float = 30.0
    opener: Callable[..., object] = request.urlopen
    static_api_key: str = ""

    def complete(self, task: TaskEnvelope) -> ProviderResult:
        started = time.monotonic()
        api_key = self.static_api_key or os.environ.get(self.api_key_env)
        if not api_key:
            return _failure(self.profile.id, ProviderErrorKind.AUTH, "configured API key is unavailable", 0)
        body = json.dumps(
            {
                "model": self.model,
                "messages": [{"role": "user", "content": json.dumps(task.to_dict(), separators=(",", ":"))}],
            }
        ).encode()
        req = request.Request(
            self.endpoint,
            data=body,
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            method="POST",
        )
        try:
            with self.opener(req, timeout=self.timeout_seconds) as response:  # type: ignore[attr-defined]
                raw = response.read()
            data = json.loads(raw)
            output = data["choices"][0]["message"]["content"]
            if not isinstance(output, str):
                raise ValueError("response content is not text")
            return ProviderResult(
                provider_id=self.profile.id,
                output=output,
                latency_ms=(time.monotonic() - started) * 1000,
                worker_tokens=int(data.get("usage", {}).get("total_tokens", 0)),
            )
        except error.HTTPError as exc:
            kind = ProviderErrorKind.RATE_LIMITED if exc.code == 429 else ProviderErrorKind.AUTH if exc.code in (401, 403) else ProviderErrorKind.INTERNAL
            retry_after = None
            if exc.code == 429:
                raw_retry_after = exc.headers.get("Retry-After") if exc.headers else None
                try:
                    retry_after = max(0.0, float(raw_retry_after)) if raw_retry_after is not None else None
                except (TypeError, ValueError):
                    retry_after = None
            return _failure(
                self.profile.id,
                kind,
                f"HTTP {exc.code}",
                (time.monotonic() - started) * 1000,
                retry_after_seconds=retry_after,
            )
        except (error.URLError, TimeoutError) as exc:
            return _failure(self.profile.id, ProviderErrorKind.UNAVAILABLE, str(exc), (time.monotonic() - started) * 1000)
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            return _failure(self.profile.id, ProviderErrorKind.INTERNAL, f"invalid provider response: {exc}", (time.monotonic() - started) * 1000)


@dataclass(slots=True)
class HuggingFaceInferenceAdapter:
    """Hugging Face Inference Providers via their OpenAI-compatible router.

    This is intentionally separate from browser candidates. It requires an
    operator-supplied token and therefore is optional; the no-key HuggingChat
    page remains a browser transport candidate with its own terms and limits.
    """

    profile: ProviderProfile
    model: str
    api_key_env: str = "HF_TOKEN"
    endpoint: str = "https://router.huggingface.co/v1/chat/completions"
    timeout_seconds: float = 30.0
    opener: Callable[..., object] = request.urlopen

    def complete(self, task: TaskEnvelope) -> ProviderResult:
        return OpenAICompatibleAdapter(
            self.profile,
            self.endpoint,
            self.model,
            self.api_key_env,
            timeout_seconds=self.timeout_seconds,
            opener=self.opener,
        ).complete(task)


class ProviderRegistry:
    def __init__(self, adapters: Mapping[str, ProviderAdapter] | None = None) -> None:
        self._adapters: dict[str, ProviderAdapter] = {}
        for provider_id, adapter in (adapters or {}).items():
            if provider_id != adapter.profile.id:
                raise ValueError("provider mapping key must match profile id")
            self.register(adapter)

    def register(self, adapter: ProviderAdapter) -> None:
        if not callable(getattr(adapter, "complete", None)):
            raise ValueError("provider adapter must expose a callable complete method")
        provider_id = adapter.profile.id
        if not provider_id.strip():
            raise ValueError("provider id must not be blank")
        if provider_id in self._adapters:
            raise ValueError(f"provider already registered: {provider_id}")
        self._adapters[provider_id] = adapter

    def get(self, provider_id: str) -> ProviderAdapter:
        try:
            return self._adapters[provider_id]
        except KeyError as exc:
            raise KeyError(f"unknown provider: {provider_id}") from exc

    def all(self) -> tuple[ProviderAdapter, ...]:
        return tuple(self._adapters.values())
