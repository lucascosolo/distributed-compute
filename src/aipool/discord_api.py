"""Small, read-only Discord bot API seam for operator setup verification."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Callable
from urllib import error, request


@dataclass(slots=True)
class DiscordApiClient:
    """Verify an operator-owned bot can see its configured test resources.

    This client deliberately performs no installation, invitation, member
    management, or message sending. The bot token is accepted only from the
    operator's process environment/configuration and is never returned.
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

    def _get(self, path: str) -> dict[str, object]:
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
        if not isinstance(payload, dict):
            raise ValueError("Discord API returned a non-object response")
        return payload
