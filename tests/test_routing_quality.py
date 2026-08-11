import unittest

from aipool.domain import ProviderProfile, ProviderState, Strategy, TaskEnvelope
from aipool.quality import validate_output
from aipool.routing import choose_provider


def profile(provider_id: str, *, cost: float, classification: float = 0.0, coding: float = 0.0, reliability: float = 0.9, max_complexity: int = 1) -> ProviderProfile:
    return ProviderProfile(
        provider_id,
        provider_id,
        "fixture",
        capabilities={"classification": classification, "structured_json": classification, "coding": coding, "instruction_following": coding},
        reliability=reliability,
        estimated_cost=cost,
        state=ProviderState.HEALTHY,
        max_complexity=max_complexity,
    )


class RoutingQualityTests(unittest.TestCase):
    def test_routine_task_chooses_cheaper_sufficient_provider(self) -> None:
        task = TaskEnvelope(task="classification", input_ref="artifact:x", local_estimate=1.0)
        decision = choose_provider(task, [profile("expensive", cost=0.5, classification=0.9), profile("cheap", cost=0.01, classification=0.7)])
        self.assertEqual(decision.provider.id, "cheap")
        self.assertEqual(decision.strategy, Strategy.SINGLE)

    def test_router_refuses_when_inline_is_cheaper(self) -> None:
        task = TaskEnvelope(task="classification", input_ref="x", local_estimate=0.01)
        decision = choose_provider(task, [profile("cheap", cost=0.0, classification=0.9)])
        self.assertEqual(decision.strategy, Strategy.NO_DELEGATION)
        self.assertIsNone(decision.provider)

    def test_router_requires_native_estimate(self) -> None:
        task = TaskEnvelope(task="classification", input_ref="artifact:x")
        decision = choose_provider(task, [profile("cheap", cost=0.0, classification=0.9)])
        self.assertEqual(decision.reason, "native_work_estimate_required")

    def test_verification_budget_is_compared_with_native_work(self) -> None:
        task = TaskEnvelope(task="classification", input_ref="artifact:x", strategy=Strategy.VERIFY, local_estimate=0.1)
        decision = choose_provider(task, [profile("cheap", cost=0.0, classification=0.9)])
        self.assertEqual(decision.reason, "delegation_cost_not_lower_than_local_estimate")

    def test_low_complexity_provider_cannot_receive_complex_implementation(self) -> None:
        task = TaskEnvelope(task="coding", input_ref="artifact:x", local_estimate=10.0)
        helper = profile("helper", cost=0.0, coding=1.0, max_complexity=1)
        decision = choose_provider(task, [helper])
        self.assertEqual(decision.reason, "no_healthy_capable_provider")

    def test_router_escalates_coding_to_capable_provider(self) -> None:
        task = TaskEnvelope(task="coding", input_ref="artifact:x", local_estimate=10.0)
        decision = choose_provider(task, [profile("classifier", cost=0.001, classification=0.9), profile("coder", cost=0.2, coding=0.9, max_complexity=4)])
        self.assertEqual(decision.provider.id, "coder")

    def test_quality_gate_rejects_garbage_and_malformed_json(self) -> None:
        self.assertFalse(validate_output("I am an AI assistant. How may I assist?", task="classification").valid)
        self.assertFalse(validate_output("{not json", require_json=True).valid)
        self.assertTrue(validate_output('{"label":"docs"}', require_json=True).valid)

    def test_quality_gate_rejects_refusal_only(self) -> None:
        report = validate_output("I cannot complete that request.")
        self.assertEqual(report.reason, "refusal_only")


if __name__ == "__main__":
    unittest.main()
