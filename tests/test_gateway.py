import json
import threading
import unittest
from http.client import HTTPConnection

from aipool.domain import ProviderProfile, ProviderState
from aipool.gateway import make_server
from aipool.providers import FixtureAdapter, ProviderRegistry
from aipool.service import Coordinator
from aipool.storage import Store


class GatewayTests(unittest.TestCase):
    def setUp(self) -> None:
        profile = ProviderProfile(
            "p", "P", "fixture", capabilities={"classification": 0.9, "structured_json": 0.9},
            reliability=0.9, state=ProviderState.HEALTHY,
        )
        self.store = Store()
        self.addCleanup(self.store.close)
        coordinator = Coordinator(ProviderRegistry({"p": FixtureAdapter(profile, lambda _: '{"label":"docs"}')}), self.store)
        self.server = make_server(coordinator, port=0, token="test-token")
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

    def test_task_and_status_require_token(self) -> None:
        status, _ = self.request("GET", "/status", token=None)
        self.assertEqual(status, 401)
        status, data = self.request("GET", "/status")
        self.assertEqual(status, 200)
        self.assertEqual(data["providers"], 1)
        status, data = self.request("POST", "/task", {"task": "classification", "input_ref": "artifact:x", "local_estimate": 1})
        self.assertEqual(status, 200)
        self.assertTrue(data["success"])

    def test_non_loopback_requires_token(self) -> None:
        store = Store()
        self.addCleanup(store.close)
        with self.assertRaises(ValueError):
            make_server(Coordinator(ProviderRegistry(), store), host="0.0.0.0", port=0)


if __name__ == "__main__":
    unittest.main()
