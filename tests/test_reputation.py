import unittest

from aipool.domain import ProviderProfile, ProviderState, TaskEnvelope
from aipool.providers import FixtureAdapter, ProviderRegistry
from aipool.service import Coordinator
from aipool.storage import Store


class ReputationTests(unittest.TestCase):
    def test_learned_capability_uses_conservative_prior(self) -> None:
        store = Store()
        self.addCleanup(store.close)
        provider = ProviderProfile(
            "p", "P", "fixture", capabilities={"classification": 0.9, "coding": 0.2},
            reliability=0.9, state=ProviderState.HEALTHY,
        )
        store.ensure_health(provider)
        store.record_observation(provider, ("classification",), False)
        learned = store.learned_capabilities(provider)
        self.assertAlmostEqual(learned["classification"], 0.675)
        self.assertAlmostEqual(learned["coding"], 0.2)

    def test_three_failures_can_drop_a_declared_capability_below_threshold(self) -> None:
        store = Store()
        self.addCleanup(store.close)
        provider = ProviderProfile("p", "P", "fixture", capabilities={"coding": 0.8}, state=ProviderState.HEALTHY)
        store.ensure_health(provider)
        store.record_observation(provider, ("coding",), False)
        store.record_observation(provider, ("coding",), False)
        store.record_observation(provider, ("coding",), False)
        self.assertLess(store.learned_capabilities(provider)["coding"], 0.5)

    def test_coordinator_uses_learned_capabilities_for_selection(self) -> None:
        store = Store()
        self.addCleanup(store.close)
        weak = ProviderProfile(
            "weak", "Weak", "fixture", capabilities={"classification": 0.8, "structured_json": 0.8},
            estimated_cost=0.01, state=ProviderState.HEALTHY,
        )
        strong = ProviderProfile(
            "strong", "Strong", "fixture", capabilities={"classification": 0.7, "structured_json": 0.7},
            estimated_cost=0.2, state=ProviderState.HEALTHY,
        )
        store.ensure_health(weak)
        for _ in range(3):
            store.record_observation(weak, ("classification", "structured_json"), False)
        coordinator = Coordinator(
            ProviderRegistry({
                "weak": FixtureAdapter(weak, lambda _: '{"label":"weak"}'),
                "strong": FixtureAdapter(strong, lambda _: '{"label":"strong"}'),
            }),
            store,
        )
        outcome = coordinator.submit(TaskEnvelope(
            task="classification", input_ref="artifact:x", requirements={"output": "json"}, local_estimate=1,
        ))
        self.assertEqual(outcome.provider_id, "strong")


if __name__ == "__main__":
    unittest.main()
