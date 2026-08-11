import contextlib
import io
import json
import os
import unittest
from unittest.mock import patch

from aipool.cli import main
from aipool.storage import Store


class CliTests(unittest.TestCase):
    def test_task_returns_compact_structured_result(self) -> None:
        task = json.dumps({
            "task": "classification",
            "input_ref": "artifact:x",
            "requirements": {"output": "json"},
            "local_estimate": 1,
        })
        output = io.StringIO()
        with patch.dict(os.environ, {"AIPOOL_FIXTURE_OUTPUT": '{"label":"docs"}'}, clear=True), contextlib.redirect_stdout(output):
            code = main(["task", "--json", task])
        result = json.loads(output.getvalue())
        self.assertEqual(code, 0)
        self.assertTrue(result["success"])
        self.assertEqual(result["provider_id"], "fixture")
        self.assertEqual(result["output"], '{"label":"docs"}')

    def test_invalid_task_returns_error_code_two(self) -> None:
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            code = main(["task", "--json", "not-json"])
        self.assertEqual(code, 2)
        self.assertFalse(json.loads(output.getvalue())["success"])

    def test_stats_is_compact_and_reads_persisted_metrics(self) -> None:
        with __import__("tempfile").TemporaryDirectory() as directory:
            database = directory + "/stats.sqlite"
            store = Store(database)
            from aipool.domain import Strategy, TaskOutcome
            store.record_outcome(TaskOutcome("task-1", Strategy.SINGLE, "fixture", "ok", True, True, delegated_compute_saved=0.8))
            store.close()
            output = io.StringIO()
            with patch.dict(os.environ, {"AIPOOL_DB": database}, clear=True), contextlib.redirect_stdout(output):
                code = main(["stats"])
            result = json.loads(output.getvalue())
            self.assertEqual(code, 0)
            self.assertEqual(result["tasks"], 1)
            self.assertEqual(result["delegated_compute_saved"], 0.8)


if __name__ == "__main__":
    unittest.main()
