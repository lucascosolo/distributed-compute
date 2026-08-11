import json
import unittest

from aipool.client import RemoteCoordinatorError, cancel_remote, enqueue_remote, get_remote_queue, submit_remote
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

    def test_submit_remote_can_send_cloudflare_access_service_token_headers(self) -> None:
        captured = {}

        def opener(req, timeout):
            captured["client_id"] = req.get_header("Cf-access-client-id")
            captured["client_secret"] = req.get_header("Cf-access-client-secret")
            return Response({"status": "queued"})

        submit_remote(
            "http://localhost", TaskEnvelope(task="classification", input_ref="x"),
            token=None,
            headers_extra={
                "CF-Access-Client-Id": "client-id",
                "CF-Access-Client-Secret": "client-secret",
            },
            opener=opener,
        )
        self.assertEqual(captured, {"client_id": "client-id", "client_secret": "client-secret"})

    def test_remote_rejects_empty_base_url(self) -> None:
        with self.assertRaises(RemoteCoordinatorError):
            submit_remote("", TaskEnvelope(task="classification", input_ref="x"), token=None, opener=lambda *_: None)

    def test_queue_client_uses_authenticated_endpoints(self) -> None:
        captured = []

        def opener(req, timeout):
            captured.append((req.full_url, req.get_method(), req.get_header("Idempotency-key")))
            return Response({"status": "queued"})

        task = TaskEnvelope(task="classification", input_ref="artifact:x")
        self.assertEqual(enqueue_remote("http://localhost", task, token="t", idempotency_key="k", opener=opener)["status"], "queued")
        self.assertEqual(get_remote_queue("http://localhost", task.task_id, token="t", opener=opener)["status"], "queued")
        self.assertEqual(cancel_remote("http://localhost", task.task_id, token="t", opener=opener)["status"], "queued")
        self.assertEqual(captured, [
            ("http://localhost/queue", "POST", "k"),
            (f"http://localhost/queue/{task.task_id}", "GET", None),
            (f"http://localhost/queue/{task.task_id}/cancel", "POST", None),
        ])


if __name__ == "__main__":
    unittest.main()
