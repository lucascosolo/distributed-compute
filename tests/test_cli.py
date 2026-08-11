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

    def test_remote_mode_forwards_task_to_configured_gateway(self) -> None:
        task = json.dumps({"task": "classification", "input_ref": "artifact:x", "local_estimate": 1})
        output = io.StringIO()
        with patch.dict(os.environ, {"AIPOOL_MODE": "remote", "AIPOOL_BASE_URL": "http://127.0.0.1:8765", "AIPOOL_TOKEN": "token"}, clear=True), \
             patch("aipool.cli.submit_remote", return_value={"success": True, "valid": True, "output": "ok"}) as submit, \
             contextlib.redirect_stdout(output):
            code = main(["task", "--json", task])
        self.assertEqual(code, 0)
        submit.assert_called_once()
        self.assertEqual(json.loads(output.getvalue())["output"], "ok")

    def test_serve_uses_operator_local_host_port_and_token(self) -> None:
        output = io.StringIO()
        server = __import__("unittest").mock.Mock()
        with patch.dict(os.environ, {"AIPOOL_HOST": "127.0.0.1", "AIPOOL_PORT": "9876", "AIPOOL_TOKEN": "token"}, clear=True), \
             patch("aipool.cli.make_server", return_value=server) as make, \
             contextlib.redirect_stdout(output):
            code = main(["serve", "--db", ":memory:"])
        self.assertEqual(code, 0)
        make.assert_called_once()
        self.assertEqual(make.call_args.kwargs["host"], "127.0.0.1")
        self.assertEqual(make.call_args.kwargs["port"], 9876)
        self.assertEqual(make.call_args.kwargs["token"], "token")
        server.serve_forever.assert_called_once()


if __name__ == "__main__":
    unittest.main()
