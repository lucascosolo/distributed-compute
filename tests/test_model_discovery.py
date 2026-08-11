import json
import unittest

from aipool.model_discovery import discover_models, models_endpoint


class _Response:
    def __init__(self, payload: object):
        self.payload = json.dumps(payload).encode()

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self, *_args):
        return self.payload


class ModelDiscoveryTests(unittest.TestCase):
    def test_models_endpoint_normalizes_chat_completion_url(self) -> None:
        self.assertEqual(
            models_endpoint("https://router.example/v1/chat/completions"),
            "https://router.example/v1/models",
        )

    def test_discovery_normalizes_openai_model_records(self) -> None:
        requests = []

        def opener(req, timeout):
            requests.append((req, timeout))
            return _Response({"data": [{"id": "model-a"}, {"id": "model-a"}, {"id": "model-b"}]})

        result = discover_models("https://router.example/v1/chat/completions", "secret", opener=opener)
        self.assertTrue(result.success)
        self.assertEqual(result.models, ("model-a", "model-b"))
        self.assertEqual(requests[0][0].full_url, "https://router.example/v1/models")
        self.assertEqual(requests[0][0].get_header("Authorization"), "Bearer secret")

    def test_missing_key_does_not_make_request(self) -> None:
        result = discover_models("https://router.example/v1", "")
        self.assertFalse(result.success)
        self.assertEqual(result.error, "api_key_not_configured")
