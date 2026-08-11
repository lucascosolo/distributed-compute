import json
import os
import sys
import unittest
from unittest.mock import patch

from aipool.domain import ProviderErrorKind, ProviderProfile, ProviderState, TaskEnvelope
from aipool.artifacts import ArtifactStore
from aipool.providers import BrowserChatAdapter, BrowserCommandAdapter
from aipool.browser_ui import UIAction, UIPlan
from aipool.providers import ModelGuidedBrowserAdapter
import tempfile
from pathlib import Path
from aipool.providers import CandidateCommandAdapter, CommandAdapter, FixtureAdapter, HuggingFaceInferenceAdapter, OpenAICompatibleAdapter, ProviderRegistry


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

    def test_browser_chat_login_wall_is_an_authentication_failure(self) -> None:
        profile = ProviderProfile("browser", "Browser", "browser-chat", state=ProviderState.HEALTHY)
        adapter = BrowserChatAdapter(profile, lambda _: "Please sign in to continue")
        result = adapter.complete(TaskEnvelope(task="summarization", input_ref="public-page"))
        self.assertFalse(result.success)
        self.assertEqual(result.error_kind, ProviderErrorKind.AUTH)

    def test_browser_command_login_wall_is_an_authentication_failure(self) -> None:
        profile = ProviderProfile("browser", "Browser", "browser-chat", state=ProviderState.HEALTHY)
        adapter = BrowserCommandAdapter(
            profile,
            (sys.executable, "-c", "print('Create an account to continue')"),
        )
        result = adapter.complete(TaskEnvelope(task="summarization", input_ref="public-page"))
        self.assertFalse(result.success)
        self.assertEqual(result.error_kind, ProviderErrorKind.AUTH)

    def test_model_guided_browser_adapter_selects_model_and_submits_context(self) -> None:
        class Session:
            def __init__(self):
                self.actions = []

            def snapshot(self):
                return "visible controls: model select, prompt textbox, Send button"

            def select(self, target, value):
                self.actions.append(("select", target, value))

            def fill(self, target, value):
                self.actions.append(("fill", target, value))

            def click(self, target):
                self.actions.append(("click", target))

            def submit(self):
                self.actions.append(("submit",))

            def wait(self, seconds):
                self.actions.append(("wait", seconds))

            def read_response(self):
                return "useful browser result"

        session = Session()
        requests = []

        def planner(planning_request):
            requests.append(planning_request)
            return UIPlan((
                UIAction("select", "model select", "strong-free-model"),
                UIAction("fill", "prompt textbox", "__AIPOOL_PROMPT__"),
                UIAction("submit", "Send"),
            ))

        profile = ProviderProfile("guided", "Guided browser", "browser-chat", state=ProviderState.HEALTHY)
        adapter = ModelGuidedBrowserAdapter(profile, session, planner)
        result = adapter.complete(TaskEnvelope(
            task="summarization", input_ref="public-page",
            requirements={"objective": "Summarize this page"},
        ))
        self.assertTrue(result.success)
        self.assertEqual(result.output, "useful browser result")
        self.assertEqual(session.actions[0], ("select", "model select", "strong-free-model"))
        self.assertTrue(any(action[0] == "submit" for action in session.actions))
        self.assertIn("Summarize this page", next(iter(requests)).prompt)

    def test_model_guided_browser_adapter_rejects_login_snapshot(self) -> None:
        class Session:
            def snapshot(self):
                return "Sign in to continue"

        profile = ProviderProfile("guided", "Guided browser", "browser-chat", state=ProviderState.HEALTHY)
        adapter = ModelGuidedBrowserAdapter(profile, Session(), lambda _: UIPlan(()))
        result = adapter.complete(TaskEnvelope(task="summarization", input_ref="public-page"))
        self.assertFalse(result.success)
        self.assertEqual(result.error_kind, ProviderErrorKind.AUTH)

    def test_ui_plan_is_bounded_and_rejects_credential_controls(self) -> None:
        with self.assertRaisesRegex(ValueError, "at most"):
            UIPlan(tuple(UIAction("click", "button") for _ in range(9)))
        with self.assertRaisesRegex(ValueError, "credential"):
            UIAction("fill", "password field", "anything")


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

    def test_candidate_command_adapter_sends_candidate_and_task_metadata(self) -> None:
        adapter = CandidateCommandAdapter(
            ProviderProfile("candidate", "Candidate", "discord-bot", state=ProviderState.HEALTHY),
            {"id": "candidate", "endpoint": "https://discord.example/bot"},
            (sys.executable, "-c", "import json,sys; p=json.load(sys.stdin); print(json.dumps(p['task']))"),
        )
        result = adapter.complete(task())
        self.assertTrue(result.success)
        self.assertEqual(json.loads(result.output)["task"], "classify")

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

    def test_huggingface_adapter_uses_router_and_hf_token(self) -> None:
        class Response:
            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def read(self):
                return json.dumps({
                    "choices": [{"message": {"content": "HF result"}}],
                    "usage": {"total_tokens": 17},
                }).encode()

        requests = []

        def opener(req, timeout):
            requests.append((req, timeout))
            return Response()

        adapter = HuggingFaceInferenceAdapter(
            ProviderProfile("hf", "Hugging Face", "huggingface", state=ProviderState.HEALTHY),
            "openai/gpt-oss-20b",
            opener=opener,
        )
        with patch.dict(os.environ, {"HF_TOKEN": "hf-test"}, clear=True):
            result = adapter.complete(task())
        self.assertTrue(result.success)
        self.assertEqual(result.output, "HF result")
        self.assertEqual(result.worker_tokens, 17)
        self.assertEqual(requests[0][0].full_url, "https://router.huggingface.co/v1/chat/completions")
        self.assertEqual(requests[0][0].get_header("Authorization"), "Bearer hf-test")
        self.assertEqual(json.loads(requests[0][0].data)["model"], "openai/gpt-oss-20b")

    def test_huggingface_adapter_holds_on_rate_limit(self) -> None:
        class RateLimited:
            def __enter__(self):
                raise __import__("urllib.error", fromlist=["HTTPError"]).HTTPError(
                    "https://router.huggingface.co", 429, "slow down", {"Retry-After": "12"}, None,
                )

            def __exit__(self, *args):
                return False

        adapter = HuggingFaceInferenceAdapter(
            ProviderProfile("hf", "Hugging Face", "huggingface", state=ProviderState.HEALTHY),
            "model",
            opener=lambda *args, **kwargs: RateLimited(),
        )
        with patch.dict(os.environ, {"HF_TOKEN": "hf-test"}, clear=True):
            result = adapter.complete(task())
        self.assertFalse(result.success)
        self.assertEqual(result.error_kind, ProviderErrorKind.RATE_LIMITED)
        self.assertEqual(result.retry_after_seconds, 12.0)

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
