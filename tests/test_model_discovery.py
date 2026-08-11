import json
import unittest

from aipool.model_discovery import classify_model, discover_models, models_endpoint


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
    def test_classification_is_conservative_and_explainable(self) -> None:
        coder = classify_model("Qwen/Qwen3-Coder-30B-Instruct")
        self.assertEqual(coder["power"], "strong")
        self.assertIn("coding", coder["capabilities"])
        self.assertEqual(classify_model("mystery-model")["metadata_confidence"], "low")

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

    def test_discovery_accepts_aion_style_models_envelope_without_key(self) -> None:
        requests = []

        def opener(req, timeout):
            requests.append((req, timeout))
            return _Response({"models": [{"id": "aion-labs/aion-2.0"}, {"id": "aion-labs/aion-2.5"}]})

        result = discover_models("https://api.aionlabs.ai/v1", "", api_key_optional=True, opener=opener)
        self.assertTrue(result.success)
        self.assertEqual(result.models, ("aion-labs/aion-2.0", "aion-labs/aion-2.5"))
        self.assertIsNone(requests[0][0].get_header("Authorization"))

    def test_discovery_optional_key_still_sends_auth_when_available(self) -> None:
        requests = []

        def opener(req, timeout):
            requests.append((req, timeout))
            return _Response({"data": [{"id": "free-model"}]})

        result = discover_models("https://api.kilo.ai/api/gateway", "kilo-key", api_key_optional=True, opener=opener)
        self.assertTrue(result.success)
        self.assertEqual(requests[0][0].get_header("Authorization"), "Bearer kilo-key")

    def test_discovery_accepts_a_large_bounded_catalog(self) -> None:
        records = [{"id": f"model-{index:05d}-with-a-long-enough-name"} for index in range(12000)]

        def opener(req, timeout):
            return _Response({"data": records})

        result = discover_models("https://api.kilo.ai/api/gateway", "kilo-key", opener=opener)
        self.assertTrue(result.success)
        self.assertEqual(len(result.models), 512)
