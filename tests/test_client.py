import json
import unittest

from aipool.client import RemoteCoordinatorError, submit_remote
from aipool.domain import TaskEnvelope


class Response:
    def __init__(self, payload: object):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None

    def read(self):
        return json.dumps(self.payload).encode()


class ClientTests(unittest.TestCase):
    def test_submit_remote_sends_task_and_bearer_token(self) -> None:
        captured = {}

        def opener(request, timeout):
            captured["url"] = request.full_url
            captured["authorization"] = request.get_header("Authorization")
            captured["timeout"] = timeout
            captured["body"] = json.loads(request.data)
            return Response({"success": True, "valid": True, "output": "ok"})

        result = submit_remote(
            "http://127.0.0.1:8765/",
            TaskEnvelope(task="classification", input_ref="artifact:x", local_estimate=1),
            token="secret",
            opener=opener,
        )
        self.assertTrue(result["success"])
        self.assertEqual(captured["url"], "http://127.0.0.1:8765/task")
        self.assertEqual(captured["authorization"], "Bearer secret")
        self.assertEqual(captured["body"]["task"], "classification")

    def test_remote_rejects_empty_base_url(self) -> None:
        with self.assertRaises(RemoteCoordinatorError):
            submit_remote("", TaskEnvelope(task="classification", input_ref="x"), token=None, opener=lambda *_: None)


if __name__ == "__main__":
    unittest.main()
