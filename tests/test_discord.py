import json
import tempfile
import unittest
from pathlib import Path
from urllib.request import Request

from aipool.artifacts import ArtifactStore
from aipool.discord_api import DiscordApiClient, DiscordChannelAdapter
from aipool.domain import ProviderProfile, ProviderState, TaskEnvelope


class Response:
    def __init__(self, payload, headers=None):
        self.payload = json.dumps(payload).encode()
        self.headers = headers or {}

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self):
        return self.payload

    def getheader(self, name, default=None):
        return self.headers.get(name, default)


class DiscordApiTests(unittest.TestCase):
    def test_check_reads_bot_guild_and_channel_without_exposing_token(self) -> None:
        requests: list[Request] = []
        payloads = iter((
            {"id": "bot-id", "username": "aipool"},
            {"id": "guild-id", "name": "Test server"},
            {"id": "channel-id", "name": "aipool-test", "type": 0},
        ))

        def opener(req, timeout):
            requests.append(req)
            return Response(next(payloads))

        result = DiscordApiClient(
            "bot-secret", "guild-id", "channel-id", opener=opener,
        ).check()
        self.assertEqual(result["bot"]["id"], "bot-id")
        self.assertEqual(result["guild"]["name"], "Test server")
        self.assertEqual(result["channel"]["name"], "aipool-test")
        self.assertTrue(all(req.get_header("Authorization") == "Bot bot-secret" for req in requests))
        self.assertTrue(all("bot-secret" not in req.full_url for req in requests))

    def test_check_requires_all_operator_identifiers(self) -> None:
        with self.assertRaisesRegex(ValueError, "guild_id"):
            DiscordApiClient("token", "", "channel")

    def test_list_bots_returns_only_bot_members_and_never_tokens(self) -> None:
        requests: list[Request] = []

        def opener(req, timeout):
            requests.append(req)
            return Response([
                {"user": {"id": "human", "username": "human", "bot": False}},
                {"user": {"id": "worker", "username": "worker-bot", "bot": True}},
            ])

        result = DiscordApiClient("bot-secret", "guild", "channel", opener=opener).list_bots()
        self.assertEqual(result, [{"id": "worker", "username": "worker-bot"}])
        self.assertIn("/guilds/guild/members?limit=1000", requests[0].full_url)
        self.assertNotIn("bot-secret", requests[0].full_url)

    def test_list_bots_rejects_unbounded_member_request(self) -> None:
        client = DiscordApiClient("token", "guild", "channel")
        with self.assertRaisesRegex(ValueError, "between 1 and 1000"):
            client.list_bots(1001)
        with self.assertRaisesRegex(ValueError, "pages must be between"):
            client.list_bots(max_pages=11)

    def test_list_bots_follows_a_full_page_to_find_later_bots(self) -> None:
        requests: list[Request] = []
        first_page = [{"user": {"id": "100", "username": "first", "bot": True}}]
        second_page = [{"user": {"id": "200", "username": "second", "bot": True}}]

        def opener(req, timeout):
            requests.append(req)
            return Response(first_page if len(requests) == 1 else second_page)

        result = DiscordApiClient("secret", "guild", "channel", opener=opener).list_bots(limit=1)
        self.assertEqual([bot["id"] for bot in result], ["100", "200"])
        self.assertIn("after=100", requests[1].full_url)

    def test_channel_adapter_sends_bounded_task_and_waits_for_selected_bot(self) -> None:
        requests: list[Request] = []
        responses = iter((
            Response({"id": "controller-message"}),
            Response([]),
            Response([{
                "id": "reply", "author": {"id": "worker-bot", "bot": True},
                "content": "bounded answer",
            }]),
        ))

        def opener(req, timeout):
            requests.append(req)
            return next(responses)

        adapter = DiscordChannelAdapter(
            ProviderProfile(
                "discord-worker", "Discord worker", "discord",
                state=ProviderState.HEALTHY, reliability=0.5, max_complexity=1,
            ),
            token="controller-secret", channel_id="channel-id", target_bot_id="worker-bot",
            opener=opener, sleep=lambda _: None,
        )
        result = adapter.complete(TaskEnvelope("classification", "synthetic-input"))
        self.assertTrue(result.success)
        self.assertEqual(result.output, "bounded answer")
        self.assertEqual(requests[0].method, "POST")
        sent = json.loads(requests[0].data)
        self.assertLessEqual(len(sent["content"]), 2000)
        self.assertTrue(sent["content"].startswith("<@worker-bot> "))
        self.assertNotIn("controller-secret", requests[0].full_url)
        self.assertIn("after=controller-message", requests[-1].full_url)

    def test_channel_adapter_transfers_bounded_artifact_context(self) -> None:
        requests: list[Request] = []
        responses = iter((
            Response({"id": "controller-message"}),
            Response([{"id": "reply", "author": {"id": "worker-bot"}, "content": "ok"}]),
        ))

        def opener(req, timeout):
            requests.append(req)
            return next(responses)

        with tempfile.TemporaryDirectory() as directory:
            artifacts = ArtifactStore(Path(directory))
            reference = artifacts.put(b"def add(a, b):\n    return a + b\n")
            adapter = DiscordChannelAdapter(
                ProviderProfile("discord-worker", "Discord worker", "discord", state=ProviderState.HEALTHY),
                token="secret", channel_id="channel", target_bot_id="worker-bot",
                artifacts=artifacts, opener=opener, sleep=lambda _: None,
            )
            result = adapter.complete(TaskEnvelope(
                "review", reference, requirements={"objective": "Review this code"},
            ))
        self.assertTrue(result.success)
        content = json.loads(requests[0].data)["content"]
        self.assertIn("Review this code", content)
        self.assertIn("def add(a, b)", content)
        self.assertIn("CONTEXT_DATA", content)

    def test_channel_adapter_does_not_use_selected_controller_as_worker(self) -> None:
        with self.assertRaisesRegex(ValueError, "different"):
            DiscordChannelAdapter(
                ProviderProfile("discord", "Discord", "discord", state=ProviderState.HEALTHY),
                token="secret", channel_id="channel", target_bot_id="controller",
                controller_bot_id="controller",
            )

    def test_channel_adapter_maps_rate_limit_and_does_not_retry_automatically(self) -> None:
        from urllib.error import HTTPError

        calls = 0

        def opener(req, timeout):
            nonlocal calls
            calls += 1
            raise HTTPError(req.full_url, 429, "limited", {"Retry-After": "42"}, None)

        result = DiscordChannelAdapter(
            ProviderProfile("discord", "Discord", "discord", state=ProviderState.HEALTHY),
            token="secret", channel_id="channel", target_bot_id="worker",
            opener=opener,
        ).complete(TaskEnvelope("classification", "input"))
        self.assertFalse(result.success)
        self.assertEqual(result.error_kind.value, "rate_limited")
        self.assertEqual(result.retry_after_seconds, 42.0)
        self.assertEqual(calls, 1)


if __name__ == "__main__":
    unittest.main()
