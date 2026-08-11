"""Provider request/token window accounting used before dispatch."""

from __future__ import annotations

import math
import time

from .domain import ProviderProfile
from .storage import Store


class UsageManager:
    def __init__(self, store: Store, *, clock=time.time) -> None:
        self.store = store
        self.clock = clock

    @staticmethod
    def window(profile: ProviderProfile, now: float) -> tuple[float, float]:
        start = math.floor(now / profile.usage_window_seconds) * profile.usage_window_seconds
        return start, start + profile.usage_window_seconds

    def reserve(self, profile: ProviderProfile, *, now: float | None = None) -> tuple[bool, float]:
        current = self.clock() if now is None else now
        start, end = self.window(profile, current)
        requests, tokens = self.store.usage(profile.id, start)
        if (profile.request_limit and requests >= profile.request_limit) or (profile.token_limit and tokens >= profile.token_limit):
            return False, end
        self.store.reserve_usage(profile.id, start)
        return True, end

    def record_tokens(self, profile: ProviderProfile, tokens: int, *, now: float | None = None) -> float:
        current = self.clock() if now is None else now
        start, end = self.window(profile, current)
        self.store.add_usage_tokens(profile.id, start, tokens)
        return end
