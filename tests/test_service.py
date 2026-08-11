import unittest

from aipool.domain import ProviderProfile, ProviderState, Strategy, TaskEnvelope
from aipool.providers import FixtureAdapter, ProviderRegistry
from aipool.service import Coordinator
from aipool.storage import Store


def profile(provider_id: str, *, state: ProviderState = ProviderState.HEALTHY, **capabilities: float) -> ProviderProfile:
    return ProviderProfile(provider_id, provider_id, "fixture", capabilities=capabilities, reliability=0.9, state=state)


class ServiceTests(unittest.TestCase):
    def test_invalid_first_provider_falls_back_and_records_scores(self) -> None:
        first = FixtureAdapter(profile("bad", classification=0.8, structured_json=0.8), lambda _: "I cannot do that")
        second = FixtureAdapter(profile("good", classification=0.8, structured_json=0.8), lambda _: '{"label":"docs"}')
        store = Store()
        self.addCleanup(store.close)
        coordinator = Coordinator(ProviderRegistry({"bad": first, "good": second}), store)
        outcome = coordinator.submit(TaskEnvelope(task="classification", input_ref="artifact:x", requirements={"output": "json"}, local_estimate=1.0))
        self.assertTrue(outcome.success)
        self.assertEqual(outcome.provider_id, "good")
        self.assertEqual(store.observation("bad", "classification"), (1, 0))
        self.assertEqual(store.observation("good", "classification"), (1, 1))

    def test_unhealthy_provider_is_bypassed(self) -> None:
        disabled = FixtureAdapter(profile("disabled", classification=1.0, structured_json=1.0, state=ProviderState.DISABLED), lambda _: "bad")
        store = Store()
        self.addCleanup(store.close)
        outcome = Coordinator(ProviderRegistry({"disabled": disabled}), store).submit(
            TaskEnvelope(task="classification", input_ref="x", local_estimate=1.0)
        )
        self.assertTrue(outcome.success)
        self.assertTrue(outcome.native_fallback)
        self.assertEqual(outcome.reason, "no_healthy_capable_provider")

    def test_no_delegation_records_zero_provider(self) -> None:
        adapter = FixtureAdapter(profile("cheap", classification=1.0, structured_json=1.0), lambda _: "ok")
        store = Store()
        self.addCleanup(store.close)
        outcome = Coordinator(ProviderRegistry({"cheap": adapter}), store).submit(
            TaskEnvelope(task="classification", input_ref="x", local_estimate=0.001)
        )
        self.assertTrue(outcome.success)
        self.assertIsNone(outcome.provider_id)
        self.assertTrue(outcome.native_fallback)

    def test_verify_requires_independent_matching_result(self) -> None:
        first_calls = [0]
        second_calls = [0]
        first = FixtureAdapter(profile("first", classification=0.9, structured_json=0.9), lambda _: (first_calls.__setitem__(0, first_calls[0] + 1) or '{"label":"docs"}'))
        second = FixtureAdapter(profile("second", classification=0.9, structured_json=0.9), lambda _: (second_calls.__setitem__(0, second_calls[0] + 1) or '{"label":"docs"}'))
        store = Store()
        self.addCleanup(store.close)
        outcome = Coordinator(ProviderRegistry({"first": first, "second": second}), store).submit(
            TaskEnvelope(task="classification", input_ref="artifact:verify", requirements={"output": "json"}, strategy=Strategy.VERIFY, local_estimate=2)
        )
        self.assertTrue(outcome.success)
        self.assertTrue(outcome.valid)
        self.assertEqual(outcome.reason, "verified")
        self.assertEqual(first_calls[0], 1)
        self.assertEqual(second_calls[0], 1)

    def test_verify_disagreement_falls_back_to_native_model(self) -> None:
        first = FixtureAdapter(profile("first", classification=0.9, structured_json=0.9), lambda _: '{"label":"docs"}')
        second = FixtureAdapter(profile("second", classification=0.9, structured_json=0.9), lambda _: '{"label":"code"}')
        store = Store()
        self.addCleanup(store.close)
        outcome = Coordinator(ProviderRegistry({"first": first, "second": second}), store).submit(
            TaskEnvelope(task="classification", input_ref="artifact:disagree", requirements={"output": "json"}, strategy=Strategy.VERIFY, local_estimate=2)
        )
        self.assertTrue(outcome.native_fallback)
        self.assertFalse(outcome.valid)
        self.assertEqual(outcome.reason, "verification_disagreement")


if __name__ == "__main__":
    unittest.main()
