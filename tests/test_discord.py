import json
import unittest
from urllib.request import Request

from aipool.discord_api import DiscordApiClient


class Response:
    def __init__(self, payload):
        self.payload = json.dumps(payload).encode()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self):
        return self.payload


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


if __name__ == "__main__":
    unittest.main()
