"""Persistent provider health and exponential backoff policy."""

from __future__ import annotations

import time
from dataclasses import replace
from typing import Callable, Iterable

from .domain import ProviderErrorKind, ProviderProfile, ProviderState
from .storage import Store


class HealthManager:
    def __init__(self, store: Store, *, clock: Callable[[], float] = time.time, base_backoff: float = 5.0, max_backoff: float = 300.0) -> None:
        self.store = store
        self.clock = clock
        self.base_backoff = base_backoff
        self.max_backoff = max_backoff

    def profiles(self, profiles: Iterable[ProviderProfile]) -> list[ProviderProfile]:
        now = self.clock()
        effective = []
        for profile in profiles:
            record = self.store.health(profile.id)
            if record is None:
                self.store.ensure_health(profile)
                effective.append(profile)
                continue
            state = ProviderState(record["state"])
            if state in {ProviderState.RATE_LIMITED, ProviderState.BROKEN} and float(record["next_probe_at"]) <= now:
                state = ProviderState.DEGRADED
                self.store.set_health(profile.id, state=state, next_probe_at=now)
            effective.append(replace(profile, state=state))
        return effective

    def success(self, provider: ProviderProfile) -> None:
        self.store.set_health(
            provider.id,
            state=ProviderState.HEALTHY,
            failure_streak=0,
            next_probe_at=0,
            last_success=self.clock(),
            last_failure_reason=None,
        )

    def failure(
        self,
        provider: ProviderProfile,
        kind: ProviderErrorKind | None,
        reason: str,
        *,
        retry_after_seconds: float | None = None,
    ) -> None:
        record = self.store.health(provider.id) or {"failure_streak": 0}
        streak = int(record["failure_streak"]) + 1
        if kind == ProviderErrorKind.AUTH:
            state = ProviderState.AUTH_REQUIRED
            delay = self.max_backoff
        elif kind == ProviderErrorKind.RATE_LIMITED:
            state = ProviderState.RATE_LIMITED
            delay = min(self.max_backoff, self.base_backoff * (2 ** (streak - 1)))
            if retry_after_seconds is not None:
                delay = min(self.max_backoff, max(delay, retry_after_seconds))
        elif streak >= 3:
            state = ProviderState.BROKEN
            delay = min(self.max_backoff, self.base_backoff * (2 ** (streak - 3)))
        else:
            state = ProviderState.DEGRADED
            delay = self.base_backoff
        self.store.set_health(
            provider.id,
            state=state,
            failure_streak=streak,
            next_probe_at=self.clock() + delay,
            last_failure_reason=reason[:500],
        )

    def hold(self, provider: ProviderProfile, until: float, reason: str) -> None:
        self.store.set_health(
            provider.id,
            state=ProviderState.RATE_LIMITED,
            next_probe_at=until,
            last_failure_reason=reason[:500],
        )
