import unittest

from aipool.domain import Strategy, TaskEnvelope
from aipool.scoping import split_task


class ScopingTests(unittest.TestCase):
    def test_split_task_creates_bounded_routable_subtasks(self) -> None:
        parent = TaskEnvelope(
            task="coding", input_ref="artifact:repo", requirements={"output": "json"},
            importance=3, max_cost=0.9, local_estimate=3.0,
        )
        tasks = split_task(parent, ["src/api", "src/cli", "tests"], subtask_kind="classification")
        self.assertEqual(len(tasks), 3)
        self.assertEqual([task.requirements["scope"] for task in tasks], ["src/api", "src/cli", "tests"])
        self.assertTrue(all(task.strategy == Strategy.SINGLE for task in tasks))
        self.assertTrue(all(task.local_estimate == 1.0 for task in tasks))
        self.assertTrue(all(task.requirements["parent_task"] == "coding" for task in tasks))

    def test_split_rejects_unqualified_subtask_kind_and_duplicate_scopes(self) -> None:
        parent = TaskEnvelope(task="review", input_ref="artifact:repo", local_estimate=2)
        with self.assertRaises(ValueError):
            split_task(parent, ["src"], subtask_kind="coding")
        with self.assertRaises(ValueError):
            split_task(parent, ["src", "src"], subtask_kind="classification")


if __name__ == "__main__":
    unittest.main()
