"""Bounded Discord API seams for setup checks and authorized test workers."""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import Callable
from urllib import error, parse, request

from .artifacts import ArtifactStore
from .context import ContextPacket
from .domain import ProviderErrorKind, ProviderProfile, ProviderResult, TaskEnvelope


@dataclass(slots=True)
class DiscordApiClient:
    """Verify an operator-owned bot can see its configured test resources.

    This client deliberately performs no installation, invitation, or member
    management. The bot token is accepted only from the operator's process
    environment/configuration and is never returned.
    """

    token: str
    guild_id: str
    channel_id: str
    api_base_url: str = "https://discord.com/api/v10"
    timeout_seconds: float = 15.0
    opener: Callable[..., object] = request.urlopen

    def __post_init__(self) -> None:
        if not self.token.strip():
            raise ValueError("Discord bot token is required")
        if not self.guild_id.strip():
            raise ValueError("Discord guild_id is required")
        if not self.channel_id.strip():
            raise ValueError("Discord channel_id is required")
        if self.timeout_seconds <= 0:
            raise ValueError("Discord timeout must be positive")

    def check(self) -> dict[str, dict[str, object]]:
        bot = self._get("/users/@me")
        guild = self._get(f"/guilds/{self.guild_id}")
        channel = self._get(f"/channels/{self.channel_id}")
        try:
            return {
                "bot": {"id": str(bot["id"]), "username": str(bot.get("username", ""))},
                "guild": {"id": str(guild["id"]), "name": str(guild.get("name", ""))},
                "channel": {
                    "id": str(channel["id"]), "name": str(channel.get("name", "")),
                    "type": channel.get("type"),
                },
            }
        except (KeyError, TypeError) as exc:
            raise ValueError("Discord API returned an invalid resource response") from exc

    def list_bots(self, limit: int = 1000, max_pages: int = 10) -> list[dict[str, str]]:
        """Return visible bot members, paging without sending anything."""
        if not 1 <= limit <= 1000:
            raise ValueError("Discord member limit must be between 1 and 1000")
        if not 1 <= max_pages <= 10:
            raise ValueError("Discord member pages must be between 1 and 10")
        bots: list[dict[str, str]] = []
        seen_bot_ids: set[str] = set()
        after = "0"
        for _ in range(max_pages):
            query = {"limit": str(limit)}
            if after != "0":
                query["after"] = after
            payload = self._get_raw(f"/guilds/{self.guild_id}/members?{parse.urlencode(query)}")
            if not isinstance(payload, list):
                raise ValueError("Discord members response is not a list")
            for member in payload:
                if not isinstance(member, dict):
                    continue
                user = member.get("user")
                if not isinstance(user, dict) or not user.get("bot"):
                    continue
                bot_id = user.get("id")
                if not isinstance(bot_id, str) or not bot_id:
                    continue
                if bot_id not in seen_bot_ids:
                    seen_bot_ids.add(bot_id)
                    bots.append({"id": bot_id, "username": str(user.get("username", ""))})
            if len(payload) < limit:
                break
            last = payload[-1].get("user") if isinstance(payload[-1], dict) else None
            next_after = last.get("id") if isinstance(last, dict) else None
            if not isinstance(next_after, str) or not next_after or next_after == after:
                break
            after = next_after
        return bots

    def _get(self, path: str) -> dict[str, object]:
        payload = self._get_raw(path)
        if not isinstance(payload, dict):
            raise ValueError("Discord API returned a non-object response")
        return payload

    def _get_raw(self, path: str) -> object:
        req = request.Request(
            self.api_base_url.rstrip("/") + path,
            headers={"Authorization": f"Bot {self.token}", "User-Agent": "aipool/0.1"},
            method="GET",
        )
        try:
            with self.opener(req, timeout=self.timeout_seconds) as response:  # type: ignore[attr-defined]
                payload = json.loads(response.read())
        except error.HTTPError as exc:
            raise ValueError(f"Discord API returned HTTP {exc.code}") from exc
        except (error.URLError, TimeoutError) as exc:
            raise ValueError("Discord API is unavailable") from exc
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            raise ValueError("Discord API returned invalid JSON") from exc
        return payload


@dataclass(slots=True)
class DiscordChannelAdapter:
    """Use an operator-owned controller bot to query one selected worker bot.

    This is intentionally a narrow, human-configured transport: it sends one
    bounded synthetic/task envelope to one configured channel, then polls only
    messages after the controller's own message until the exact configured bot
    replies. It cannot install bots, use user tokens, rotate profiles, or
    select arbitrary recipients. A worker must be explicitly invited and its
    bot ID must be configured by the operator.
    """

    profile: ProviderProfile
    token: str
    channel_id: str
    target_bot_id: str
    controller_bot_id: str = ""
    api_base_url: str = "https://discord.com/api/v10"
    timeout_seconds: float = 15.0
    max_prompt_chars: int = 1900
    poll_seconds: float = 1.0
    max_wait_seconds: float = 30.0
    message_prefix: str = ""
    artifacts: ArtifactStore | None = None
    opener: Callable[..., object] = request.urlopen
    sleep: Callable[[float], None] = time.sleep
    clock: Callable[[], float] = time.monotonic

    def __post_init__(self) -> None:
        for name, value in (("token", self.token), ("channel_id", self.channel_id), ("target_bot_id", self.target_bot_id)):
            if not value.strip():
                raise ValueError(f"Discord {name} is required")
        if self.controller_bot_id and self.target_bot_id == self.controller_bot_id:
            raise ValueError("Discord target bot must be different from controller bot")
        if self.timeout_seconds <= 0 or self.max_prompt_chars < 1 or self.poll_seconds < 0 or self.max_wait_seconds <= 0:
            raise ValueError("Discord adapter limits are invalid")

    def complete(self, task: TaskEnvelope) -> ProviderResult:
        started = self.clock()
        try:
            packet_limit = self.max_prompt_chars - len(self.message_prefix)
            if packet_limit < 256:
                raise ValueError("Discord message prefix leaves too little room for context")
            content = self.message_prefix + ContextPacket.from_task(
                task, self.artifacts, max_chars=packet_limit,
            ).render()
        except (TypeError, ValueError, OSError) as exc:
            return _discord_failure(self.profile.id, ProviderErrorKind.INVALID_REQUEST, str(exc), started, self.clock)
        if len(content) > self.max_prompt_chars:
            return _discord_failure(self.profile.id, ProviderErrorKind.INVALID_REQUEST, "Discord task envelope exceeds message limit", started, self.clock)
        try:
            sent = self._request("POST", f"/channels/{self.channel_id}/messages", {"content": content})
            if not isinstance(sent, dict):
                raise ValueError("Discord send response is not an object")
            message_id = sent.get("id")
            if not isinstance(message_id, str) or not message_id:
                raise ValueError("Discord send response has no message ID")
            deadline = self.clock() + self.max_wait_seconds
            while self.clock() <= deadline:
                messages = self._request(
                    "GET", f"/channels/{self.channel_id}/messages?{parse.urlencode({'after': message_id, 'limit': '100'})}",
                )
                if not isinstance(messages, list):
                    raise ValueError("Discord messages response is not a list")
                for message in messages:
                    if not isinstance(message, dict):
                        continue
                    author = message.get("author")
                    if not isinstance(author, dict) or str(author.get("id", "")) != self.target_bot_id:
                        continue
                    output = message.get("content")
                    if isinstance(output, str) and output.strip():
                        return ProviderResult(self.profile.id, output=output, latency_ms=(self.clock() - started) * 1000)
                self.sleep(min(self.poll_seconds, max(0.0, deadline - self.clock())))
            return _discord_failure(self.profile.id, ProviderErrorKind.TIMEOUT, "Discord worker did not reply before timeout", started, self.clock)
        except _DiscordHttpFailure as exc:
            return _discord_failure(self.profile.id, exc.kind, exc.message, started, self.clock, exc.retry_after_seconds)
        except (error.URLError, TimeoutError) as exc:
            return _discord_failure(self.profile.id, ProviderErrorKind.UNAVAILABLE, str(exc), started, self.clock)
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            return _discord_failure(self.profile.id, ProviderErrorKind.INTERNAL, str(exc), started, self.clock)

    def _request(self, method: str, path: str, body: dict[str, object] | None = None) -> object:
        data = json.dumps(body, separators=(",", ":")).encode() if body is not None else None
        headers = {"Authorization": f"Bot {self.token}", "User-Agent": "aipool/0.1"}
        if body is not None:
            headers["Content-Type"] = "application/json"
        req = request.Request(self.api_base_url.rstrip("/") + path, data=data, headers=headers, method=method)
        try:
            with self.opener(req, timeout=self.timeout_seconds) as response:  # type: ignore[attr-defined]
                payload = json.loads(response.read())
        except error.HTTPError as exc:
            retry = None
            if exc.headers:
                try:
                    raw = exc.headers.get("Retry-After")
                    retry = max(0.0, float(raw)) if raw is not None else None
                except (TypeError, ValueError):
                    retry = None
            kind = ProviderErrorKind.RATE_LIMITED if exc.code == 429 else ProviderErrorKind.AUTH if exc.code in (401, 403) else ProviderErrorKind.INTERNAL
            raise _DiscordHttpFailure(kind, f"Discord API returned HTTP {exc.code}", retry) from exc
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            raise ValueError("Discord API returned invalid JSON") from exc
        return payload


@dataclass(frozen=True, slots=True)
class _DiscordHttpFailure(Exception):
    kind: ProviderErrorKind
    message: str
    retry_after_seconds: float | None = None


def _discord_failure(
    provider_id: str, kind: ProviderErrorKind, message: str, started: float,
    clock: Callable[[], float], retry_after_seconds: float | None = None,
) -> ProviderResult:
    return ProviderResult(
        provider_id=provider_id, success=False, error_kind=kind, error=message[:500],
        latency_ms=(clock() - started) * 1000, retry_after_seconds=retry_after_seconds,
    )
