import json
import os
import sys
import unittest
from unittest.mock import patch

from aipool.domain import ProviderErrorKind, ProviderProfile, ProviderState, TaskEnvelope
from aipool.artifacts import ArtifactStore
from aipool.providers import BrowserChatAdapter, BrowserCommandAdapter
import tempfile
from pathlib import Path
from aipool.providers import CommandAdapter, FixtureAdapter, OpenAICompatibleAdapter, ProviderRegistry


def task() -> TaskEnvelope:
    return TaskEnvelope(task="classify", input_ref="artifact:sha256:test")


class BrowserChatAdapterTests(unittest.TestCase):
    def test_browser_adapter_sends_rendered_context_without_an_api_key(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            artifacts = ArtifactStore(Path(directory))
            reference = artifacts.put(b"source code")
            prompts: list[str] = []
            profile = ProviderProfile(
                id="public-chat:fixture", name="Public chat", transport="browser-chat",
                capabilities={"summarization": 0.7}, context_limit=4096,
                reliability=0.8, estimated_cost=0.0, state=ProviderState.HEALTHY,
                max_complexity=2,
            )
            adapter = BrowserChatAdapter(profile, lambda prompt: prompts.append(prompt) or "summary", artifacts)
            result = adapter.complete(TaskEnvelope(task="summarization", input_ref=reference))
            self.assertTrue(result.success)
            self.assertEqual(result.output, "summary")
            self.assertIn("source code", prompts[0])
            self.assertNotIn("Authorization:", prompts[0])

    def test_browser_command_adapter_can_feed_a_local_browser_wrapper(self) -> None:
        profile = ProviderProfile(
            id="public-chat:command", name="Public chat wrapper", transport="browser-chat",
            capabilities={"summarization": 0.7}, context_limit=4096,
            reliability=0.8, estimated_cost=0.0, state=ProviderState.HEALTHY,
            max_complexity=2,
        )
        adapter = BrowserCommandAdapter(
            profile,
            (sys.executable, "-c", "import sys; print('browser result')"),
        )
        result = adapter.complete(TaskEnvelope(task="summarization", input_ref="public-page"))
        self.assertTrue(result.success)
        self.assertEqual(result.output.strip(), "browser result")


class ProvidersTests(unittest.TestCase):
    def test_fixture_normalizes_text_output(self) -> None:
        adapter = FixtureAdapter(
            ProviderProfile("fixture", "Fixture", "fixture", state=ProviderState.HEALTHY),
            lambda _: '{"label":"docs"}',
        )
        result = adapter.complete(task())
        self.assertTrue(result.success)
        self.assertEqual(result.output, '{"label":"docs"}')

    def test_command_adapter_uses_stdin_without_shell(self) -> None:
        adapter = CommandAdapter(
            ProviderProfile("cmd", "Command", "cli", state=ProviderState.HEALTHY),
            (sys.executable, "-c", "import sys; print(sys.stdin.read())"),
        )
        result = adapter.complete(task())
        self.assertTrue(result.success)
        self.assertEqual(json.loads(result.output)["input_ref"], "artifact:sha256:test")

    def test_command_timeout_is_normalized(self) -> None:
        adapter = CommandAdapter(
            ProviderProfile("cmd", "Command", "cli", state=ProviderState.HEALTHY),
            (sys.executable, "-c", "import time; time.sleep(1)"),
            timeout_seconds=0.01,
        )
        result = adapter.complete(task())
        self.assertFalse(result.success)
        self.assertEqual(result.error_kind, ProviderErrorKind.TIMEOUT)

    def test_openai_adapter_requires_configured_key(self) -> None:
        adapter = OpenAICompatibleAdapter(
            ProviderProfile("api", "API", "openai", state=ProviderState.HEALTHY),
            "https://example.invalid/v1/chat/completions",
            "model",
            "AIPOOL_TEST_MISSING_KEY",
        )
        with patch.dict(os.environ, {}, clear=True):
            result = adapter.complete(task())
        self.assertFalse(result.success)
        self.assertEqual(result.error_kind, ProviderErrorKind.AUTH)

    def test_registry_rejects_duplicate_ids(self) -> None:
        profile = ProviderProfile("fixture", "Fixture", "fixture")
        first = FixtureAdapter(profile, lambda _: "ok")
        registry = ProviderRegistry({"fixture": first})
        with self.assertRaisesRegex(ValueError, "already registered"):
            registry.register(FixtureAdapter(profile, lambda _: "again"))

    def test_registry_rejects_key_mismatch_and_malformed_capabilities(self) -> None:
        profile = ProviderProfile("profile-id", "Provider", "fixture")
        with self.assertRaisesRegex(ValueError, "mapping key"):
            ProviderRegistry({"different-id": FixtureAdapter(profile, lambda _: "ok")})
        with self.assertRaisesRegex(ValueError, "capability score"):
            ProviderRegistry().register(FixtureAdapter(
                ProviderProfile("bad", "Bad", "fixture", capabilities={"coding": 1.1}),
                lambda _: "ok",
            ))


if __name__ == "__main__":
    unittest.main()
