import unittest

from aipool.benchmark import BenchmarkCase
from aipool.comparison import run_comparison
from aipool.domain import ProviderProfile, ProviderState, TaskEnvelope
from aipool.providers import FixtureAdapter, ProviderRegistry
from aipool.service import Coordinator
from aipool.storage import Store


class ComparisonTests(unittest.TestCase):
    def make_case(self, *, local_estimate=1.0):
        return BenchmarkCase(
            "small_summary",
            "summarization",
            TaskEnvelope(
                task="summarization", input_ref="artifact:source",
                requirements={"objective": "Write a short summary"},
                local_estimate=local_estimate,
            ),
            lambda output: output.strip() == "useful result",
        )

    def coordinator(self, output="useful result"):
        store = Store()
        self.addCleanup(store.close)
        profile = ProviderProfile(
            "free-chat", "Free chat", "fixture",
            capabilities={"summarization": 0.8}, reliability=0.8,
            estimated_cost=0.01, state=ProviderState.HEALTHY, max_complexity=2,
        )
        return Coordinator(
            ProviderRegistry({"free-chat": FixtureAdapter(profile, lambda _: output)}),
            store,
        )

    def test_report_compares_quality_cost_latency_and_context(self) -> None:
        report = run_comparison(
            (self.make_case(),),
            lambda packet: "useful result",
            self.coordinator(),
        )
        record = report.records[0]
        self.assertTrue(record.baseline_valid)
        self.assertTrue(record.distributed_valid)
        self.assertTrue(record.distributed_cheaper)
        self.assertGreater(record.context_chars, 0)
        self.assertEqual(report.baseline_valid_rate, 1.0)
        self.assertEqual(report.distributed_valid_rate, 1.0)
        self.assertEqual(report.quality_regressions, 0)

    def test_native_fallback_is_reported_as_not_a_distributed_answer(self) -> None:
        report = run_comparison(
            (self.make_case(local_estimate=0.0),),
            lambda packet: "useful result",
            self.coordinator(),
        )
        record = report.records[0]
        self.assertTrue(record.baseline_valid)
        self.assertFalse(record.distributed_valid)
        self.assertTrue(record.native_fallback)
        self.assertFalse(record.distributed_cheaper)

    def test_failed_baseline_and_distributed_quality_are_counted(self) -> None:
        report = run_comparison(
            (self.make_case(),),
            lambda packet: "not the expected answer",
            self.coordinator(output="refusal"),
        )
        record = report.records[0]
        self.assertFalse(record.baseline_valid)
        self.assertFalse(record.distributed_valid)
        self.assertEqual(report.quality_regressions, 0)

    def test_duplicate_case_names_and_empty_suites_are_rejected(self) -> None:
        coordinator = self.coordinator()
        with self.assertRaisesRegex(ValueError, "between 1 and 32"):
            run_comparison((), lambda packet: "ok", coordinator)
        case = self.make_case()
        with self.assertRaisesRegex(ValueError, "unique"):
            run_comparison((case, case), lambda packet: "ok", coordinator)


if __name__ == "__main__":
    unittest.main()
