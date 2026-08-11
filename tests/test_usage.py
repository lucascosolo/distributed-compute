import unittest

from aipool.domain import ProviderProfile, ProviderState
from aipool.domain import TaskEnvelope
from aipool.providers import FixtureAdapter, ProviderRegistry
from aipool.service import Coordinator
from aipool.storage import Store
from aipool.usage import UsageManager


class UsageTests(unittest.TestCase):
    def test_request_limit_blocks_until_window_rolls_over(self) -> None:
        now = [100.0]
        store = Store()
        self.addCleanup(store.close)
        manager = UsageManager(store, clock=lambda: now[0])
        profile = ProviderProfile("p", "P", "fixture", state=ProviderState.HEALTHY, request_limit=1, usage_window_seconds=60)
        self.assertEqual(manager.reserve(profile), (True, 120.0))
        self.assertEqual(manager.reserve(profile), (False, 120.0))
        now[0] = 121.0
        self.assertEqual(manager.reserve(profile), (True, 180.0))

    def test_token_limit_blocks_after_observed_usage(self) -> None:
        now = [100.0]
        store = Store()
        self.addCleanup(store.close)
        manager = UsageManager(store, clock=lambda: now[0])
        profile = ProviderProfile("p", "P", "fixture", state=ProviderState.HEALTHY, token_limit=10, usage_window_seconds=60)
        self.assertTrue(manager.reserve(profile)[0])
        manager.record_tokens(profile, 10)
        self.assertFalse(manager.reserve(profile)[0])

    def test_coordinator_holds_provider_after_request_limit(self) -> None:
        store = Store()
        self.addCleanup(store.close)
        profile = ProviderProfile(
            "p", "P", "fixture", capabilities={"classification": 1.0, "structured_json": 1.0},
            reliability=1.0, state=ProviderState.HEALTHY, request_limit=1,
        )
        calls = []
        adapter = FixtureAdapter(profile, lambda _: calls.append(True) or "ok")
        coordinator = Coordinator(ProviderRegistry({"p": adapter}), store)
        first = coordinator.submit(TaskEnvelope(task="classification", input_ref="a", local_estimate=1))
        second = coordinator.submit(TaskEnvelope(task="classification", input_ref="b", local_estimate=1))
        self.assertTrue(first.success)
        self.assertTrue(second.native_fallback)
        self.assertEqual(len(calls), 1)
        self.assertEqual(store.health("p")["state"], ProviderState.RATE_LIMITED.value)


if __name__ == "__main__":
    unittest.main()
