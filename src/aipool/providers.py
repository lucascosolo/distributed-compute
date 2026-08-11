"""Provider adapters. Transport-specific behavior stays in this module."""

from __future__ import annotations

import json
import os
import subprocess
import time
from dataclasses import dataclass
from typing import Callable, Mapping, Protocol
from urllib import error, request

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
) -> ProviderResult:
    return ProviderResult(
        provider_id=provider_id,
        success=False,
        error_kind=kind,
        error=message[:500],
        latency_ms=latency_ms,
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
class OpenAICompatibleAdapter:
    profile: ProviderProfile
    endpoint: str
    model: str
    api_key_env: str
    timeout_seconds: float = 30.0
    opener: Callable[..., object] = request.urlopen

    def complete(self, task: TaskEnvelope) -> ProviderResult:
        started = time.monotonic()
        api_key = os.environ.get(self.api_key_env)
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
            return _failure(self.profile.id, kind, f"HTTP {exc.code}", (time.monotonic() - started) * 1000)
        except (error.URLError, TimeoutError) as exc:
            return _failure(self.profile.id, ProviderErrorKind.UNAVAILABLE, str(exc), (time.monotonic() - started) * 1000)
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            return _failure(self.profile.id, ProviderErrorKind.INTERNAL, f"invalid provider response: {exc}", (time.monotonic() - started) * 1000)


class ProviderRegistry:
    def __init__(self, adapters: Mapping[str, ProviderAdapter] | None = None) -> None:
        self._adapters = dict(adapters or {})

    def register(self, adapter: ProviderAdapter) -> None:
        provider_id = adapter.profile.id
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
