import tempfile
import unittest

from aipool.artifacts import ArtifactStore
from aipool.domain import ProviderProfile, ProviderState, TaskEnvelope
from aipool.providers import FixtureAdapter, ProviderRegistry
from aipool.service import Coordinator
from aipool.storage import Store


class ArtifactsCacheTests(unittest.TestCase):
    def test_artifact_round_trip_and_integrity(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            artifacts = ArtifactStore(directory)
            reference = artifacts.put(b"hello")
            self.assertEqual(artifacts.get(reference), b"hello")
            with self.assertRaises(ValueError):
                artifacts.get("artifact:sha256:" + "0" * 63 + "g")

    def test_successful_result_is_cached(self) -> None:
        calls = [0]

        def handler(_: TaskEnvelope) -> str:
            calls[0] += 1
            return '{"label":"docs"}'

        profile = ProviderProfile(
            "p", "P", "fixture", capabilities={"classification": 0.9, "structured_json": 0.9},
            reliability=0.9, state=ProviderState.HEALTHY,
        )
        store = Store()
        self.addCleanup(store.close)
        coordinator = Coordinator(ProviderRegistry({"p": FixtureAdapter(profile, handler)}), store)
        task = TaskEnvelope(task="classification", input_ref="artifact:x", requirements={"output": "json"}, local_estimate=1)
        first = coordinator.submit(task)
        second = coordinator.submit(task)
        self.assertEqual(calls[0], 1)
        self.assertIsNone(first.reason)
        self.assertEqual(second.reason, "cache_hit")


if __name__ == "__main__":
    unittest.main()
