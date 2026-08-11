import json
import unittest

from aipool.domain import (
    ProviderProfile,
    ProviderState,
    Strategy,
    TaskEnvelope,
    TaskKind,
)


class DomainTests(unittest.TestCase):
    def test_task_round_trip_and_stable_id(self) -> None:
        task = TaskEnvelope(
            task=TaskKind.CLASSIFICATION,
            input_ref="artifact:sha256:abc",
            requirements={"confidence": True, "labels": ["code", "docs"]},
            strategy=Strategy.CASCADE,
            local_estimate=0.2,
        )

        encoded = json.dumps(task.to_dict(), sort_keys=True)
        restored = TaskEnvelope.from_dict(json.loads(encoded))

        self.assertEqual(restored, task)
        self.assertEqual(restored.task_id, task.stable_id())

    def test_task_round_trip_preserves_agent_delegation_metadata(self) -> None:
        task = TaskEnvelope(
            task="coding", input_ref="artifact:repo", origin_provider_id="agent:claude",
            delegation_chain=("agent:claude",),
        )
        self.assertEqual(TaskEnvelope.from_dict(task.to_dict()), task)

    def test_task_rejects_secret_like_requirement(self) -> None:
        with self.assertRaisesRegex(ValueError, "secret-like"):
            TaskEnvelope(
                task="extract",
                input_ref="artifact:sha256:abc",
                requirements={"api_key": "must-not-be-here"},
            )

    def test_task_rejects_invalid_importance_and_cost(self) -> None:
        with self.assertRaises(ValueError):
            TaskEnvelope(task="extract", input_ref="x", importance=0)
        with self.assertRaises(ValueError):
            TaskEnvelope(task="extract", input_ref="x", max_cost=-1)

    def test_provider_profile_validates_state_and_reputation(self) -> None:
        profile = ProviderProfile(
            id="fixture",
            name="Fixture",
            transport="fixture",
            capabilities={"classification": 0.9},
            reliability=0.8,
            state=ProviderState.HEALTHY,
        )
        self.assertEqual(profile.state, ProviderState.HEALTHY)


if __name__ == "__main__":
    unittest.main()
