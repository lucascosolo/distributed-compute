import contextlib
import io
import json
import os
import tempfile
import unittest
from unittest.mock import patch

from aipool.cli import _build_registry, main
from aipool.domain import ProviderState
from aipool.storage import Store


class CliTests(unittest.TestCase):
    def test_discovered_discord_workers_start_quarantined_until_benchmark(self) -> None:
        fake = __import__("unittest").mock.Mock()
        fake.return_value.list_bots.return_value = [{"id": "worker", "username": "worker"}]
        with tempfile.TemporaryDirectory() as directory:
            with patch.dict(os.environ, {
                "AIPOOL_DISCORD_BOT_TOKEN": "secret", "AIPOOL_DISCORD_GUILD_ID": "guild",
                "AIPOOL_DISCORD_CHANNEL_ID": "channel", "AIPOOL_DISCORD_APPLICATION_ID": "controller",
                "AIPOOL_ARTIFACT_ROOT": directory,
            }, clear=True), patch("aipool.cli.DiscordApiClient", fake):
                registry = _build_registry(__import__("argparse").Namespace(command="task"))
        self.assertEqual(registry.get("discord-worker:worker").profile.state, ProviderState.QUARANTINED)

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

    def test_discord_check_uses_operator_config_without_printing_token(self) -> None:
        output = io.StringIO()
        fake = __import__("unittest").mock.Mock()
        fake.return_value.check.return_value = {
            "bot": {"id": "bot", "username": "aipool"},
            "guild": {"id": "guild", "name": "Test"},
            "channel": {"id": "channel", "name": "aipool-test", "type": 0},
        }
        with patch.dict(os.environ, {
            "AIPOOL_DISCORD_BOT_TOKEN": "secret",
            "AIPOOL_DISCORD_GUILD_ID": "guild",
            "AIPOOL_DISCORD_CHANNEL_ID": "channel",
        }, clear=True), patch("aipool.cli.DiscordApiClient", fake), contextlib.redirect_stdout(output):
            code = main(["discord", "check"])
        self.assertEqual(code, 0)
        self.assertNotIn("secret", output.getvalue())
        fake.assert_called_once_with("secret", "guild", "channel")

    def test_discord_hold_disables_exact_discovered_username_without_sending(self) -> None:
        output = io.StringIO()
        fake = __import__("unittest").mock.Mock()
        fake.return_value.list_bots.return_value = [{"id": "hana", "username": "Hana"}]
        with tempfile.TemporaryDirectory() as directory:
            database = directory + "/discord.sqlite"
            with patch.dict(os.environ, {
                "AIPOOL_DISCORD_BOT_TOKEN": "secret", "AIPOOL_DISCORD_GUILD_ID": "guild",
                "AIPOOL_DISCORD_CHANNEL_ID": "channel",
            }, clear=True), patch("aipool.cli.DiscordApiClient", fake), contextlib.redirect_stdout(output):
                code = main([
                    "discord", "hold", "--username", "Hana",
                    "--reason", "bot requires a user slash-command interaction", "--db", database,
            ])
            self.assertEqual(code, 0)
            self.assertEqual(json.loads(output.getvalue())["state"], "disabled")
            store = Store(database)
            self.assertEqual(store.health("discord-worker:hana")["state"], "disabled")
            store.close()
        fake.assert_called_once_with("secret", "guild", "channel")

    def test_task_can_use_an_operator_supplied_browser_wrapper_without_api_key(self) -> None:
        task = json.dumps({
            "task": "summarization", "input_ref": "public-page", "local_estimate": 1,
        })
        output = io.StringIO()
        command = f"{os.sys.executable} -c \"import sys; print('browser summary')\""
        with patch.dict(os.environ, {"AIPOOL_BROWSER_COMMAND": command}, clear=True), contextlib.redirect_stdout(output):
            code = main(["task", "--json", task])
        result = json.loads(output.getvalue())
        self.assertEqual(code, 0)
        self.assertEqual(result["provider_id"], "browser-chat")
        self.assertEqual(result["output"].strip(), "browser summary")

    def test_invalid_task_returns_error_code_two(self) -> None:
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            code = main(["task", "--json", "not-json"])
        self.assertEqual(code, 2)
        self.assertFalse(json.loads(output.getvalue())["success"])

    def test_discover_persists_bounded_public_chatbot_leads(self) -> None:
        from aipool.discovery_sources import DiscoveryLead
        with __import__("tempfile").TemporaryDirectory() as directory:
            database = directory + "/discovery.sqlite"
            fake_source = __import__("unittest").mock.Mock()
            fake_source.collect.return_value = (DiscoveryLead(
                title="public assistant", source_url="https://reddit.example/post",
                external_url="https://assistant.example/",
            ),)
            output = io.StringIO()
            with patch.dict(os.environ, {}, clear=True), \
                 patch("aipool.cli.RedditSearchSource", return_value=fake_source), \
                 contextlib.redirect_stdout(output):
                code = main(["discover", "--query", "free chatbot", "--db", database])
            result = json.loads(output.getvalue())
            self.assertEqual(code, 0)
            self.assertEqual(result["leads"][0]["title"], "public assistant")
            store = Store(database)
            self.assertEqual(len(store.discovery_lead_rows()), 1)
            store.close()

    def test_candidate_promote_requires_terms_review_and_keeps_quarantine(self) -> None:
        from aipool.discovery_sources import DiscoveryLead, LeadRegistry
        with __import__("tempfile").TemporaryDirectory() as directory:
            database = directory + "/promotion.sqlite"
            store = Store(database)
            lead = DiscoveryLead(
                title="candidate", source_url="https://reddit.example/post",
                external_url="https://chat.example/",
            )
            stored = LeadRegistry(store).add(lead, now=1.0)
            store.close()
            output = io.StringIO()
            with patch.dict(os.environ, {}, clear=True), contextlib.redirect_stdout(output):
                code = main([
                    "candidate", "promote", stored.lead_id, "--db", database,
                    "--terms-review", "reviewed: no explicit binding prohibition",
                ])
            result = json.loads(output.getvalue())
            self.assertEqual(code, 0)
            self.assertEqual(result["state"], "quarantined")

    def test_candidate_probe_runs_configured_command_and_persists_evidence(self) -> None:
        from aipool.discovery import CandidateProvider, CandidateRegistry
        with __import__("tempfile").TemporaryDirectory() as directory:
            database = directory + "/probe.sqlite"
            store = Store(database)
            CandidateRegistry(store).add(CandidateProvider(
                id="candidate:one", name="Candidate", source="https://source.example/",
                transport="browser-chat", endpoint="https://chat.example/", terms_url="",
                authorization="operator reviewed; no explicit prohibition found",
            ))
            store.close()
            command = (
                f'{os.sys.executable} -c "import json,sys; c=json.load(sys.stdin); '
                "print(json.dumps({'candidate_id':c['id'],'available':True,'authorized':True,"
                "'context_length':1024,'output_valid':True,'latency_ms':1.0,"
                "'restrictions_clear':True,'cost_known':True,'automation_supported':True}))\""
            )
            output = io.StringIO()
            with patch.dict(os.environ, {}, clear=True), contextlib.redirect_stdout(output):
                code = main(["candidate", "probe", "--probe-command", command, "--db", database])
            self.assertEqual(code, 0)
            self.assertTrue(json.loads(output.getvalue())["reports"][0]["output_valid"])
            reopened = Store(database)
            self.assertEqual(reopened.candidate_rows()[0]["state"], "probed")
            reopened.close()

    def test_candidate_activate_requires_explicit_approval_and_passing_probe(self) -> None:
        from aipool.discovery import CandidateProvider, CandidateRegistry, ProbeResult
        with __import__("tempfile").TemporaryDirectory() as directory:
            database = directory + "/activate.sqlite"
            store = Store(database)
            registry = CandidateRegistry(store)
            candidate = CandidateProvider(
                id="candidate:approved", name="Candidate", source="https://source.example/",
                transport="browser-chat", endpoint="https://chat.example/", terms_url="",
                authorization="operator reviewed",
            )
            registry.add(candidate)
            registry.mark_probed(candidate.id, ProbeResult(
                candidate_id=candidate.id, available=True, authorized=True,
                context_length=1024, output_valid=True, latency_ms=1.0,
                restrictions_clear=True, cost_known=True, automation_supported=True,
            ))
            store.close()
            output = io.StringIO()
            with patch.dict(os.environ, {}, clear=True), contextlib.redirect_stdout(output):
                code = main(["candidate", "activate", candidate.id, "--db", database])
            self.assertEqual(code, 2)
            output = io.StringIO()
            with patch.dict(os.environ, {}, clear=True), contextlib.redirect_stdout(output):
                code = main(["candidate", "activate", candidate.id, "--operator-approved", "--db", database])
            self.assertEqual(code, 0)
            self.assertEqual(json.loads(output.getvalue())["state"], "approved")

    def test_candidate_list_reports_quarantine_and_approval_state(self) -> None:
        from aipool.discovery import CandidateProvider, CandidateRegistry
        with __import__("tempfile").TemporaryDirectory() as directory:
            database = directory + "/list.sqlite"
            store = Store(database)
            CandidateRegistry(store).add(CandidateProvider(
                id="candidate:list", name="Candidate", source="https://source.example/",
                transport="telegram-bot", endpoint="https://t.me/example_bot", terms_url="",
                authorization="operator reviewed",
            ))
            store.close()
            output = io.StringIO()
            with patch.dict(os.environ, {}, clear=True), contextlib.redirect_stdout(output):
                code = main(["candidate", "list", "--db", database])
            self.assertEqual(code, 0)
            record = json.loads(output.getvalue())["candidates"][0]
            self.assertEqual(record["state"], "quarantined")
            self.assertEqual(record["transport"], "telegram-bot")
            self.assertFalse(record["probe_passed"])

    def test_candidate_benchmark_requires_approval_and_persists_scores(self) -> None:
        from aipool.discovery import CandidateProvider, CandidateRegistry, ProbeResult
        with __import__("tempfile").TemporaryDirectory() as directory:
            database = directory + "/benchmark.sqlite"
            store = Store(database)
            registry = CandidateRegistry(store)
            candidate = CandidateProvider(
                id="candidate:bench", name="Candidate", source="https://source.example/",
                transport="discord-bot", endpoint="https://discord.example/bot", terms_url="",
                authorization="operator reviewed",
            )
            registry.add(candidate)
            registry.mark_probed(candidate.id, ProbeResult(
                candidate_id=candidate.id, available=True, authorized=True,
                context_length=1024, output_valid=True, latency_ms=1.0,
                restrictions_clear=True, cost_known=True, automation_supported=True,
            ))
            registry.activate(candidate.id, operator_approved=True)
            store.close()
            command = f'{os.sys.executable} -c "print(\'{{\\\"name\\\":\\\"Ada\\\"}}\')"'
            output = io.StringIO()
            with patch.dict(os.environ, {}, clear=True), contextlib.redirect_stdout(output):
                code = main(["candidate", "benchmark", candidate.id, "--command", command, "--db", database])
            self.assertEqual(code, 0)
            result = json.loads(output.getvalue())
            self.assertEqual(result["attempts"], 3)
            self.assertEqual(result["valid"], 2)

    def test_discover_can_ingest_a_supplied_reddit_thread(self) -> None:
        from aipool.discovery_sources import DiscoveryLead
        with __import__("tempfile").TemporaryDirectory() as directory:
            output = io.StringIO()
            fake_source = __import__("unittest").mock.Mock()
            fake_source.collect.return_value = (DiscoveryLead(
                title="thread recommendation", source_url="https://reddit.example/comment",
                external_url="https://chat.example/",
            ),)
            with patch.dict(os.environ, {}, clear=True), \
                 patch("aipool.cli.RedditThreadSource", return_value=fake_source), \
                 contextlib.redirect_stdout(output):
                code = main([
                    "discover", "--thread-url",
                    "https://www.reddit.com/r/ChatGPT/comments/t/thread/",
                    "--db", directory + "/thread.sqlite",
                ])
            self.assertEqual(code, 0)
            self.assertEqual(json.loads(output.getvalue())["leads"][0]["external_url"], "https://chat.example/")

    def test_discover_can_ingest_a_supplied_article_page(self) -> None:
        from aipool.discovery_sources import DiscoveryLead
        with __import__("tempfile").TemporaryDirectory() as directory:
            output = io.StringIO()
            fake_source = __import__("unittest").mock.Mock()
            fake_source.collect.return_value = (DiscoveryLead(
                title="article link", source_url="https://article.example/",
                external_url="https://chat.example/",
            ),)
            with patch.dict(os.environ, {}, clear=True), \
                 patch("aipool.cli.HtmlPageSource", return_value=fake_source), \
                 contextlib.redirect_stdout(output):
                code = main([
                    "discover", "--page-url", "https://article.example/",
                    "--db", directory + "/page.sqlite",
                ])
            self.assertEqual(code, 0)
            self.assertEqual(json.loads(output.getvalue())["leads"][0]["title"], "article link")

    def test_discover_can_import_local_candidate_catalog(self) -> None:
        with __import__("tempfile").TemporaryDirectory() as directory:
            catalog = directory + "/catalog.json"
            with open(catalog, "w") as handle:
                json.dump({"items": [{"name": "candidate", "url": "https://chat.example/"}]}, handle)
            output = io.StringIO()
            with patch.dict(os.environ, {}, clear=True), contextlib.redirect_stdout(output):
                code = main(["discover", "--catalog-file", catalog, "--db", directory + "/catalog.sqlite"])
            self.assertEqual(code, 0)
            result = json.loads(output.getvalue())
            self.assertEqual(result["leads"][0]["source_kind"], "local-catalog")

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

    def test_queue_submit_status_and_cancel_use_local_queue(self) -> None:
        with __import__("tempfile").TemporaryDirectory() as directory:
            database = directory + "/queue.sqlite"
            task = json.dumps({"task": "classification", "input_ref": "artifact:x", "local_estimate": 1})
            submitted = io.StringIO()
            with patch.dict(os.environ, {}, clear=True), contextlib.redirect_stdout(submitted):
                self.assertEqual(main(["queue", "submit", "--db", database, "--json", task, "--idempotency-key", "operator-1"]), 0)
            record = json.loads(submitted.getvalue())
            self.assertEqual(record["status"], "queued")
            self.assertEqual(record["idempotency_key"], "operator-1")

            status = io.StringIO()
            with patch.dict(os.environ, {}, clear=True), contextlib.redirect_stdout(status):
                self.assertEqual(main(["queue", "status", "--db", database, record["task_id"]]), 0)
            self.assertEqual(json.loads(status.getvalue())["task_id"], record["task_id"])

            cancelled = io.StringIO()
            with patch.dict(os.environ, {}, clear=True), contextlib.redirect_stdout(cancelled):
                self.assertEqual(main(["queue", "cancel", "--db", database, record["task_id"]]), 0)
            self.assertEqual(json.loads(cancelled.getvalue())["status"], "cancelled")

    def test_queue_commands_forward_to_remote_gateway(self) -> None:
        task = json.dumps({"task": "classification", "input_ref": "artifact:x", "local_estimate": 1})
        with patch.dict(os.environ, {"AIPOOL_MODE": "remote", "AIPOOL_BASE_URL": "http://gateway", "AIPOOL_TOKEN": "token"}, clear=True), \
             patch("aipool.cli.enqueue_remote", return_value={"task_id": "t1", "status": "queued"}) as enqueue, \
             patch("aipool.cli.get_remote_queue", return_value={"task_id": "t1", "status": "running"}) as get_queue, \
             patch("aipool.cli.cancel_remote", return_value={"task_id": "t1", "status": "cancelled"}) as cancel:
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                self.assertEqual(main(["queue", "submit", "--json", task, "--idempotency-key", "k1"]), 0)
                self.assertEqual(main(["queue", "status", "t1"]), 0)
                self.assertEqual(main(["queue", "cancel", "t1"]), 0)
        self.assertEqual(enqueue.call_args.kwargs["idempotency_key"], "k1")
        self.assertEqual(get_queue.call_args.args[1], "t1")
        self.assertEqual(cancel.call_args.args[1], "t1")

    def test_queue_status_missing_task_returns_not_found(self) -> None:
        with __import__("tempfile").TemporaryDirectory() as directory:
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                code = main(["queue", "status", "--db", directory + "/queue.sqlite", "missing"])
        self.assertEqual(code, 1)
        self.assertEqual(json.loads(output.getvalue())["error"], "queue task not found")


if __name__ == "__main__":
    unittest.main()
