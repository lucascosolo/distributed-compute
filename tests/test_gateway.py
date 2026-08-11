import json
import os
import threading
import unittest
import tempfile
from pathlib import Path
from http.client import HTTPConnection
from unittest.mock import patch

from aipool.domain import ProviderProfile, ProviderState
from aipool.gateway import make_server
from aipool.providers import FixtureAdapter, ProviderRegistry
from aipool.service import Coordinator
from aipool.storage import Store


class GatewayTests(unittest.TestCase):
    def setUp(self) -> None:
        self._secret_env = patch.dict(os.environ, {
            "HF_TOKEN": "", "AIPOOL_OPENAI_API_KEY": "", "AIPOOL_TOKEN": "",
            "AIPOOL_DISCORD_BOT_TOKEN": "",
        }, clear=False)
        self._secret_env.start()
        self.addCleanup(self._secret_env.stop)
        profile = ProviderProfile(
            "p", "P", "fixture", capabilities={"classification": 0.9, "structured_json": 0.9},
            reliability=0.9, state=ProviderState.HEALTHY,
        )
        self.store = Store()
        self.addCleanup(self.store.close)
        self.config_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.config_directory.cleanup)
        coordinator = Coordinator(ProviderRegistry({"p": FixtureAdapter(profile, lambda _: '{"label":"docs"}')}), self.store)
        self.server = make_server(
            coordinator, port=0, token="test-token",
            config_path=Path(self.config_directory.name) / ".aipool.local",
        )
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.host, self.port = self.server.server_address[:2]

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)

    def request(self, method: str, path: str, payload: object | None = None, token: str | None = "test-token"):
        connection = HTTPConnection(self.host, self.port, timeout=2)
        headers = {"Authorization": f"Bearer {token}"} if token else {}
        body = json.dumps(payload).encode() if payload is not None else None
        if body is not None:
            headers["Content-Type"] = "application/json"
        connection.request(method, path, body, headers)
        response = connection.getresponse()
        data = json.loads(response.read())
        connection.close()
        return response.status, data

    def raw_request(self, method: str, path: str, payload: object | None = None, token: str | None = "test-token"):
        connection = HTTPConnection(self.host, self.port, timeout=2)
        headers = {"Authorization": f"Bearer {token}"} if token else {}
        body = json.dumps(payload).encode() if payload is not None else None
        if body is not None:
            headers["Content-Type"] = "application/json"
        connection.request(method, path, body, headers)
        response = connection.getresponse()
        data = response.read()
        content_type = response.getheader("Content-Type")
        status = response.status
        connection.close()
        return status, content_type, data

    def test_task_and_status_require_token(self) -> None:
        status, _ = self.request("GET", "/status", token=None)
        self.assertEqual(status, 401)
        status, data = self.request("GET", "/status")
        self.assertEqual(status, 200)
        self.assertEqual(data["providers"], 1)
        status, data = self.request("POST", "/task", {"task": "classification", "input_ref": "artifact:x", "local_estimate": 1})
        self.assertEqual(status, 200)
        self.assertTrue(data["success"])

    def test_status_and_stats_expose_authenticated_operational_metrics(self) -> None:
        status, data = self.request("GET", "/status")
        self.assertEqual(status, 200)
        self.assertEqual(data["provider_states"][0]["id"], "p")
        self.assertEqual(data["provider_states"][0]["transport"], "fixture")
        self.assertIn("classification", data["provider_states"][0]["capabilities"])
        self.assertEqual(data["provider_states"][0]["state"], "healthy")
        self.assertIn("stats", data)
        status, data = self.request("GET", "/stats")
        self.assertEqual(status, 200)
        self.assertIn("tasks", data)
        status, _ = self.request("GET", "/stats", token=None)
        self.assertEqual(status, 401)

    def test_admin_panel_is_authenticated_and_does_not_echo_secret_values(self) -> None:
        status, _, _ = self.raw_request("GET", "/admin", token=None)
        self.assertEqual(status, 401)
        status, content_type, body = self.raw_request("GET", "/admin")
        self.assertEqual(status, 200)
        self.assertTrue(content_type.startswith("text/html"))
        self.assertIn(b"provider console", body)
        status, data = self.request("GET", "/admin/config")
        self.assertEqual(status, 200)
        self.assertFalse(data["secrets"]["HF_TOKEN"])

    def test_admin_panel_persists_allowlisted_values_with_restricted_permissions(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            config_path = Path(directory) / ".aipool.local"
            self.server.shutdown()
            self.server.server_close()
            self.thread.join(timeout=2)
            coordinator = self.server.aipool_coordinator  # type: ignore[attr-defined]
            self.server = make_server(coordinator, port=0, token="test-token", config_path=config_path)
            self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
            self.thread.start()
            self.host, self.port = self.server.server_address[:2]
            status, data = self.request("POST", "/admin/config", {
                "AIPOOL_HF_MODEL": "openai/gpt-oss-20b",
                "AIPOOL_BROWSER_COMMAND": "/usr/local/bin/authorized-browser-wrapper",
                "AIPOOL_COMMAND": "/usr/local/bin/authorized-discord-wrapper",
                "AIPOOL_DISCORD_APPLICATION_ID": "123456789012345678",
                "AIPOOL_DISCORD_GUILD_ID": "234567890123456789",
                "AIPOOL_DISCORD_CHANNEL_ID": "345678901234567890",
                "AIPOOL_DISCORD_BOT_TOKEN": "discord-secret",
                "HF_TOKEN": "hf-secret",
                "AIPOOL_MODEL_HUGGING_FACE_INFERENCE_PROVIDERS_QWEN_QWEN3_8B_ENABLED": "1",
                "AIPOOL_PROVIDER_HUGGING_FACE_INFERENCE_PROVIDERS_API_KEY": "model-secret",
                "UNSAFE_SETTING": "must-not-persist",
            })
            self.assertEqual(status, 200)
            self.assertTrue(data["updated"])
            text = config_path.read_text()
            self.assertIn("AIPOOL_HF_MODEL=openai/gpt-oss-20b", text)
            self.assertIn("AIPOOL_BROWSER_COMMAND=/usr/local/bin/authorized-browser-wrapper", text)
            self.assertIn("AIPOOL_COMMAND=/usr/local/bin/authorized-discord-wrapper", text)
            self.assertIn("AIPOOL_DISCORD_APPLICATION_ID=123456789012345678", text)
            self.assertIn("AIPOOL_DISCORD_BOT_TOKEN=discord-secret", text)
            self.assertIn("HF_TOKEN=hf-secret", text)
            self.assertIn("AIPOOL_PROVIDER_HUGGING_FACE_INFERENCE_PROVIDERS_API_KEY=model-secret", text)
            self.assertNotIn("UNSAFE_SETTING", text)
            self.assertEqual(config_path.stat().st_mode & 0o777, 0o600)
            status, data = self.request("GET", "/admin/config")
            self.assertTrue(data["secrets"]["HF_TOKEN"])
            self.assertTrue(data["secrets"]["AIPOOL_DISCORD_BOT_TOKEN"])
            self.assertTrue(any(provider["has_api_key"] for provider in data["providers"]))
            self.assertNotIn("hf-secret", json.dumps(data))
            self.assertNotIn("discord-secret", json.dumps(data))
            self.assertNotIn("model-secret", json.dumps(data))

    def test_admin_model_discovery_is_protected_and_redacted(self) -> None:
        status, config = self.request("GET", "/admin/config")
        self.assertEqual(status, 200)
        slug = config["providers"][0]["slug"]
        with patch("aipool.gateway.discover_models") as discover:
            from aipool.model_discovery import ModelDiscovery
            discover.return_value = ModelDiscovery(True, ("model-a", "model-b"), "https://router.example/v1/models")
            status, data = self.request("GET", f"/admin/discover-models?slug={slug}")
        self.assertEqual(status, 200)
        self.assertEqual([model["id"] for model in data["models"]], ["model-a", "model-b"])
        discover.assert_called_once()
        self.assertNotIn("secret", json.dumps(data))
        status, snapshot = self.request("GET", "/admin/config")
        self.assertEqual(status, 200)
        self.assertEqual({row["model_id"] for row in snapshot["discovered_models"]}, {"model-a", "model-b"})

    def test_queue_enqueue_status_and_cancel(self) -> None:
        task = {"task": "classification", "input_ref": "artifact:queued", "local_estimate": 1}
        status, queued = self.request("POST", "/queue", {"task": task, "idempotency_key": "queued-1"})
        self.assertEqual(status, 202)
        self.assertEqual(queued["status"], "queued")
        status, duplicate = self.request("POST", "/queue", {"task": task, "idempotency_key": "queued-1"})
        self.assertEqual(status, 202)
        self.assertEqual(duplicate["task_id"], queued["task_id"])
        status, record = self.request("GET", f"/queue/{queued['task_id']}")
        self.assertEqual(status, 200)
        self.assertEqual(record["status"], "queued")
        status, cancelled = self.request("POST", f"/queue/{queued['task_id']}/cancel", {})
        self.assertEqual(status, 200)
        self.assertEqual(cancelled["status"], "cancelled")
        status, _ = self.request("GET", "/queue/does-not-exist")
        self.assertEqual(status, 404)

    def test_queue_requires_token(self) -> None:
        status, _ = self.request("POST", "/queue", {"task": "classification", "input_ref": "x"}, token=None)
        self.assertEqual(status, 401)

    def test_non_loopback_requires_token(self) -> None:
        store = Store()
        self.addCleanup(store.close)
        with self.assertRaises(ValueError):
            make_server(Coordinator(ProviderRegistry(), store), host="0.0.0.0", port=0)


if __name__ == "__main__":
    unittest.main()
