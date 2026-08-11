import unittest

from aipool.benchmark import default_cases, run_benchmark
from aipool.domain import ProviderErrorKind, ProviderProfile, ProviderState, ProviderResult
from aipool.providers import FixtureAdapter


class BenchmarkTests(unittest.TestCase):
    def test_default_cases_include_context_and_explicit_objectives(self) -> None:
        cases = default_cases()
        self.assertEqual(len(cases), 3)
        for case in cases:
            self.assertTrue(case.task.requirements["objective"])
            self.assertNotIn("benchmark:", case.task.input_ref)

    def test_benchmark_scores_capabilities_separately(self) -> None:
        profile = ProviderProfile(
            "fixture", "Fixture", "fixture",
            capabilities={"classification": 1.0, "extraction": 1.0, "summarization": 1.0},
            reliability=1.0, state=ProviderState.HEALTHY,
        )

        def handler(task):
            if task.task == "summarization":
                return "This is a sufficiently long summary of the benchmark input."
            if task.task == "classification":
                return '{"label":"docs"}'
            return "unrelated output"

        result = run_benchmark(FixtureAdapter(profile, handler))
        self.assertEqual(result.attempts, 3)
        self.assertEqual(result.valid, 2)
        self.assertEqual(result.scores["classification"], 1.0)
        self.assertEqual(result.scores["extraction"], 0.0)
        self.assertEqual(result.scores["summarization"], 1.0)

    def test_failed_provider_attempts_still_count(self) -> None:
        profile = ProviderProfile("broken", "Broken", "fixture", state=ProviderState.HEALTHY)
        adapter = FixtureAdapter(profile, lambda _: ProviderResult("broken", success=False, error="offline"))
        result = run_benchmark(adapter, default_cases()[:1])
        self.assertEqual(result.attempts, 1)
        self.assertEqual(result.valid, 0)

    def test_rate_limit_stops_remaining_cases_and_preserves_retry(self) -> None:
        profile = ProviderProfile("limited", "Limited", "fixture", state=ProviderState.HEALTHY)
        calls = []

        def handler(task):
            calls.append(task.task)
            return ProviderResult(
                "limited", success=False, error_kind=ProviderErrorKind.RATE_LIMITED,
                retry_after_seconds=37,
            )

        result = run_benchmark(FixtureAdapter(profile, handler))
        self.assertEqual(result.attempts, 1)
        self.assertEqual(len(calls), 1)
        self.assertEqual(result.stopped_error, ProviderErrorKind.RATE_LIMITED)
        self.assertEqual(result.retry_after_seconds, 37)
        self.assertEqual(result.scores["classification"], 0.0)

    def test_json_benchmarks_record_structured_output_capability(self) -> None:
        profile = ProviderProfile("json", "JSON", "fixture", state=ProviderState.HEALTHY)
        result = run_benchmark(FixtureAdapter(profile, lambda _: '{"name":"Ada"}'), default_cases()[:2])
        self.assertEqual(result.scores["classification"], 1.0)
        self.assertEqual(result.scores["extraction"], 1.0)
        self.assertEqual(result.scores["structured_json"], 1.0)


if __name__ == "__main__":
    unittest.main()
