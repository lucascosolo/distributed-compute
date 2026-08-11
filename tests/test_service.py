import json
import unittest

from aipool.domain import ProviderErrorKind, ProviderProfile, ProviderResult, ProviderState, Strategy, TaskEnvelope
from aipool.providers import FixtureAdapter, ProviderRegistry
from aipool.service import Coordinator
from aipool.storage import Store


def profile(provider_id: str, *, state: ProviderState = ProviderState.HEALTHY, **capabilities: float) -> ProviderProfile:
    return ProviderProfile(provider_id, provider_id, "fixture", capabilities=capabilities, reliability=0.9, state=state)


class ServiceTests(unittest.TestCase):
    def test_rate_limit_blocks_other_workers_on_same_transport(self) -> None:
        calls = {"first": 0, "second": 0}

        def limited(provider_id: str):
            def handler(_: TaskEnvelope) -> ProviderResult:
                calls[provider_id] += 1
                return ProviderResult(provider_id, success=False, error_kind=ProviderErrorKind.RATE_LIMITED)
            return handler

        def worker_profile(provider_id: str) -> ProviderProfile:
            return ProviderProfile(
                provider_id, provider_id, "fixture",
                capabilities={"classification": 0.9, "structured_json": 0.9},
                reliability=0.9, state=ProviderState.HEALTHY,
            )

        first = FixtureAdapter(worker_profile("first"), limited("first"))
        second = FixtureAdapter(worker_profile("second"), limited("second"))
        store = Store()
        self.addCleanup(store.close)
        outcome = Coordinator(ProviderRegistry({"first": first, "second": second}), store).submit(
            TaskEnvelope(task="classification", input_ref="synthetic", local_estimate=1.0)
        )
        self.assertFalse(outcome.success)
        self.assertEqual(calls, {"first": 1, "second": 0})

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
        self.assertEqual(outcome.orchestration_cost, 0.16)

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
        opinions = json.loads(outcome.output)
        self.assertEqual([item["provider_id"] for item in opinions["opinions"]], ["first", "second"])

    def test_consensus_accepts_two_matching_independent_results(self) -> None:
        calls = {provider_id: [0] for provider_id in ("first", "second", "third")}

        def response(provider_id: str, output: str):
            def complete(_: TaskEnvelope) -> str:
                calls[provider_id][0] += 1
                return output
            return complete

        adapters = {
            "first": FixtureAdapter(profile("first", classification=0.9, structured_json=0.9), response("first", '{"label":"docs"}')),
            "second": FixtureAdapter(profile("second", classification=0.9, structured_json=0.9), response("second", '{"label":"code"}')),
            "third": FixtureAdapter(profile("third", classification=0.9, structured_json=0.9), response("third", '{"label":"docs"}')),
        }
        store = Store()
        self.addCleanup(store.close)
        outcome = Coordinator(ProviderRegistry(adapters), store).submit(
            TaskEnvelope(task="classification", input_ref="artifact:consensus", requirements={"output": "json"}, strategy=Strategy.CONSENSUS, local_estimate=10)
        )
        self.assertTrue(outcome.success)
        self.assertTrue(outcome.valid)
        self.assertEqual(outcome.reason, "consensus")
        self.assertEqual(outcome.provider_id, "first")
        self.assertEqual(calls, {"first": [1], "second": [1], "third": [1]})
        self.assertEqual(outcome.orchestration_cost, 0.24)

    def test_consensus_disagreement_falls_back_to_native_model(self) -> None:
        adapters = {
            provider_id: FixtureAdapter(
                profile(provider_id, classification=0.9, structured_json=0.9),
                lambda _, provider_id=provider_id: '{"label":"' + provider_id + '"}',
            )
            for provider_id in ("first", "second", "third")
        }
        store = Store()
        self.addCleanup(store.close)
        outcome = Coordinator(ProviderRegistry(adapters), store).submit(
            TaskEnvelope(task="classification", input_ref="artifact:consensus-disagree", requirements={"output": "json"}, strategy=Strategy.CONSENSUS, local_estimate=10)
        )
        self.assertTrue(outcome.native_fallback)
        self.assertFalse(outcome.valid)
        self.assertEqual(outcome.reason, "consensus_disagreement")
        opinions = json.loads(outcome.output)
        self.assertEqual(len(opinions["opinions"]), 3)
        self.assertEqual({item["provider_id"] for item in opinions["opinions"]}, {"first", "second", "third"})

    def test_consensus_budget_gate_uses_all_three_provider_calls(self) -> None:
        adapters = {
            provider_id: FixtureAdapter(profile(provider_id, classification=0.9, structured_json=0.9), lambda _: '{"label":"docs"}')
            for provider_id in ("first", "second", "third")
        }
        store = Store()
        self.addCleanup(store.close)
        outcome = Coordinator(ProviderRegistry(adapters), store).submit(
            TaskEnvelope(task="classification", input_ref="artifact:budget", requirements={"output": "json"}, strategy=Strategy.CONSENSUS, local_estimate=0.23)
        )
        self.assertTrue(outcome.native_fallback)
        self.assertEqual(outcome.reason, "delegation_cost_not_lower_than_local_estimate")

    def test_map_runs_each_explicit_scope_and_aggregates_valid_results(self) -> None:
        calls = [0]

        def handler(task: TaskEnvelope) -> str:
            calls[0] += 1
            return json.dumps({"scope": task.requirements["scope"], "label": "docs"})

        adapter = FixtureAdapter(profile("mapper", classification=0.9, structured_json=0.9), handler)
        store = Store()
        self.addCleanup(store.close)
        outcome = Coordinator(ProviderRegistry({"mapper": adapter}), store).submit(
            TaskEnvelope(
                task="coding", input_ref="artifact:map", strategy=Strategy.MAP,
                requirements={"scopes": ["src", "tests"], "subtask_kind": "classification", "output": "json"},
                local_estimate=2.0,
            )
        )
        self.assertTrue(outcome.valid)
        self.assertEqual(outcome.strategy, Strategy.MAP)
        self.assertEqual(calls[0], 2)
        self.assertEqual(json.loads(outcome.output), [
            {"scope": "src", "output": '{"scope": "src", "label": "docs"}'},
            {"scope": "tests", "output": '{"scope": "tests", "label": "docs"}'},
        ])
        self.assertEqual(outcome.orchestration_cost, 0.16)

    def test_map_rotates_eligible_providers_across_independent_scopes(self) -> None:
        calls = []

        def handler(provider_id: str):
            def complete(task: TaskEnvelope) -> str:
                calls.append((provider_id, task.requirements["scope"]))
                return json.dumps({"scope": task.requirements["scope"], "provider": provider_id})
            return complete

        adapters = {
            provider_id: FixtureAdapter(profile(provider_id, classification=0.9, structured_json=0.9), handler(provider_id))
            for provider_id in ("first", "second")
        }
        store = Store()
        self.addCleanup(store.close)
        outcome = Coordinator(ProviderRegistry(adapters), store).submit(
            TaskEnvelope(
                task="coding", input_ref="artifact:rotating-map", strategy=Strategy.MAP,
                requirements={"scopes": ["src", "tests"], "subtask_kind": "classification", "output": "json"},
                local_estimate=2.0,
            )
        )
        self.assertTrue(outcome.valid)
        self.assertEqual({provider_id for provider_id, _ in calls}, {"first", "second"})

    def test_map_falls_back_if_a_subtask_cannot_be_delegated_economically(self) -> None:
        adapter = FixtureAdapter(profile("mapper", classification=0.9, structured_json=0.9), lambda _: '{"label":"docs"}')
        store = Store()
        self.addCleanup(store.close)
        outcome = Coordinator(ProviderRegistry({"mapper": adapter}), store).submit(
            TaskEnvelope(
                task="coding", input_ref="artifact:map-budget", strategy=Strategy.MAP,
                requirements={"scopes": ["a", "b"], "subtask_kind": "classification", "output": "json"},
                local_estimate=0.15,
            )
        )
        self.assertTrue(outcome.native_fallback)
        self.assertFalse(outcome.valid)
        self.assertEqual(outcome.reason, "map_subtask_native_fallback")

    def test_map_reduce_uses_mapped_outputs_in_a_bounded_reduce_stage(self) -> None:
        def handler(task: TaskEnvelope) -> str:
            if task.task == "summarization":
                return "Combined summary of the mapped document results."
            return json.dumps({"scope": task.requirements["scope"], "label": "docs"})

        capabilities = {"classification": 0.9, "structured_json": 0.9, "summarization": 0.9}
        adapter = FixtureAdapter(
            ProviderProfile("worker", "worker", "fixture", capabilities=capabilities, reliability=0.9, state=ProviderState.HEALTHY, max_complexity=2),
            handler,
        )
        store = Store()
        self.addCleanup(store.close)
        outcome = Coordinator(ProviderRegistry({"worker": adapter}), store).submit(
            TaskEnvelope(
                task="coding", input_ref="artifact:map-reduce", strategy=Strategy.MAP_REDUCE,
                requirements={"scopes": ["one", "two"], "subtask_kind": "classification", "reduce_kind": "summarization", "output": "json"},
                local_estimate=3.0,
            )
        )
        self.assertTrue(outcome.valid)
        self.assertEqual(outcome.strategy, Strategy.MAP_REDUCE)
        self.assertEqual(outcome.output, "Combined summary of the mapped document results.")
        self.assertEqual(outcome.reason, "map_reduce")


if __name__ == "__main__":
    unittest.main()
