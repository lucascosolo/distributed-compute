import json
import base64
import os
import threading
import unittest
import tempfile
from pathlib import Path
from http.client import HTTPConnection
from unittest.mock import patch

from aipool.domain import ProviderErrorKind, ProviderProfile, ProviderState
from aipool.gateway import make_server
from aipool.providers import FixtureAdapter, ProviderRegistry
from aipool.service import Coordinator
from aipool.storage import Store


class GatewayTests(unittest.TestCase):
    def setUp(self) -> None:
        self._secret_env = patch.dict(os.environ, {
            "HF_TOKEN": "", "AIPOOL_OPENAI_API_KEY": "", "AIPOOL_TOKEN": "",
        }, clear=False)
        self._secret_env.start()
        self.addCleanup(self._secret_env.stop)
        profile = ProviderProfile(
            "p", "P", "fixture", capabilities={"classification": 0.9, "structured_json": 0.9},
            reliability=0.9, state=ProviderState.HEALTHY,
        )
        self.store = Store()
        self.addCleanup(self.store.close)
        self.config_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.config_directory.cleanup)
        self.reloads = []
        coordinator = Coordinator(ProviderRegistry({"p": FixtureAdapter(profile, lambda _: '{"label":"docs"}')}), self.store)
        self.server = make_server(
            coordinator, port=0, token="test-token",
            config_path=Path(self.config_directory.name) / ".aipool.local",
            reload_callback=lambda: self.reloads.append(True),
        )
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.host, self.port = self.server.server_address[:2]

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)

    def request(self, method: str, path: str, payload: object | None = None, token: str | None = "test-token"):
        connection = HTTPConnection(self.host, self.port, timeout=2)
        headers = {"Authorization": f"Bearer {token}"} if token else {}
        body = json.dumps(payload).encode() if payload is not None else None
        if body is not None:
            headers["Content-Type"] = "application/json"
        connection.request(method, path, body, headers)
        response = connection.getresponse()
        data = json.loads(response.read())
        connection.close()
        return response.status, data

    def raw_request(self, method: str, path: str, payload: object | None = None, token: str | None = "test-token"):
        connection = HTTPConnection(self.host, self.port, timeout=2)
        headers = {"Authorization": f"Bearer {token}"} if token else {}
        body = json.dumps(payload).encode() if payload is not None else None
        if body is not None:
            headers["Content-Type"] = "application/json"
        connection.request(method, path, body, headers)
        response = connection.getresponse()
        data = response.read()
        content_type = response.getheader("Content-Type")
        status = response.status
        connection.close()
        return status, content_type, data

    def test_task_and_status_require_token(self) -> None:
        status, _ = self.request("GET", "/status", token=None)
        self.assertEqual(status, 401)
        status, data = self.request("GET", "/status")
        self.assertEqual(status, 200)
        self.assertEqual(data["providers"], 1)
        status, data = self.request("POST", "/task", {"task": "classification", "input_ref": "artifact:x", "local_estimate": 1})
        self.assertEqual(status, 200)
        self.assertTrue(data["success"])

    def test_authenticated_artifact_upload_and_read_round_trip(self) -> None:
        status, data = self.request("POST", "/artifact", {"content": base64.b64encode(b"shared context").decode()})
        self.assertEqual(status, 201)
        reference = data["reference"]
        self.assertTrue(reference.startswith("artifact:sha256:"))
        status, fetched = self.request("GET", "/artifact/" + reference.removeprefix("artifact:sha256:"))
        self.assertEqual(status, 200)
        self.assertEqual(base64.b64decode(fetched["content"]), b"shared context")

    def test_artifact_upload_requires_authentication(self) -> None:
        status, _ = self.request("POST", "/artifact", {"content": "YQ=="}, token=None)
        self.assertEqual(status, 401)

    def test_status_and_stats_expose_authenticated_operational_metrics(self) -> None:
        status, data = self.request("GET", "/status")
        self.assertEqual(status, 200)
        self.assertEqual(data["provider_states"][0]["id"], "p")
        self.assertEqual(data["provider_states"][0]["transport"], "fixture")
        self.assertIn("classification", data["provider_states"][0]["capabilities"])
        self.assertEqual(data["provider_states"][0]["state"], "healthy")
        self.assertIn("stats", data)
        status, data = self.request("GET", "/stats")
        self.assertEqual(status, 200)
        self.assertIn("tasks", data)
        status, _ = self.request("GET", "/stats", token=None)
        self.assertEqual(status, 401)

    def test_admin_readiness_is_redacted_and_does_not_probe_providers(self) -> None:
        status, data = self.request("GET", "/admin/readiness")
        self.assertEqual(status, 200)
        self.assertGreater(data["summary"]["total"], 0)
        self.assertEqual(len(data["providers"]), data["summary"]["total"])
        row = data["providers"][0]
        self.assertIn(row["state"], {"not_loaded", "healthy", "quarantined"})
        self.assertTrue(row["smoke_test_requires_approval"])
        self.assertIn("blocked_reasons", row)
        self.assertIn("preflight_status", row)
        pending = next(item for item in data["providers"] if item["provider_slug"] == "tokenrouter")
        self.assertEqual(pending["preflight_status"], "pending")
        self.assertIn("preflight_pending", pending["blocked_reasons"])
        self.assertNotIn("secret", json.dumps(data))

    def test_admin_readiness_reports_usage_for_loaded_catalog_provider(self) -> None:
        from aipool.provider_catalog import load_catalog

        provider = load_catalog()[0]
        profile = ProviderProfile(
            "catalog:" + provider.slug, provider.name, provider.transport,
            capabilities={"classification": 0.7}, state=ProviderState.QUARANTINED,
            quota_group="catalog:" + provider.provider_slug,
        )
        self.server.aipool_coordinator.registry.register(FixtureAdapter(profile, lambda _: "ok"))
        status, data = self.request("GET", "/admin/readiness")
        self.assertEqual(status, 200)
        row = next(item for item in data["providers"] if item["slug"] == provider.slug)
        self.assertTrue(row["loaded"])
        self.assertEqual(row["state"], "quarantined")
        self.assertEqual(row["requests_used"], 0)

    def test_admin_readiness_reports_redacted_last_failure_detail(self) -> None:
        from aipool.provider_catalog import load_catalog
        provider = load_catalog()[0]
        profile = ProviderProfile(
            "catalog:" + provider.slug, provider.name, provider.transport,
            capabilities={"classification": 0.7}, state=ProviderState.QUARANTINED,
        )
        coordinator = self.server.aipool_coordinator  # type: ignore[attr-defined]
        coordinator.registry.register(FixtureAdapter(profile, lambda _: "ok"))
        coordinator.health.failure(profile, ProviderErrorKind.AUTH, "HTTP 401")
        status, data = self.request("GET", "/admin/readiness")
        self.assertEqual(status, 200)
        row = next(item for item in data["providers"] if item["slug"] == provider.slug)
        self.assertEqual(row["last_failure_reason"], "HTTP 401")

    def test_saving_provider_credentials_clears_that_provider_hold(self) -> None:
        from aipool.provider_catalog import config_prefix, load_catalog
        provider = load_catalog()[0]
        profile = ProviderProfile(
            "catalog:" + provider.slug, provider.name, provider.transport,
            capabilities={"classification": 0.7}, state=ProviderState.QUARANTINED,
        )
        coordinator = self.server.aipool_coordinator  # type: ignore[attr-defined]
        coordinator.registry.register(FixtureAdapter(profile, lambda _: "ok"))
        coordinator.health.failure(profile, ProviderErrorKind.AUTH, "HTTP 401")
        status, data = self.request("POST", "/admin/config", {
            f"{config_prefix(provider)}_API_KEY": "replacement-key",
        })
        self.assertEqual(status, 200)
        self.assertTrue(data["reloaded"])
        row = next(item for item in self.request("GET", "/admin/readiness")[1]["providers"] if item["slug"] == provider.slug)
        self.assertEqual(row["state"], "quarantined")
        self.assertEqual(row["last_failure_reason"], "")

    def test_admin_panel_is_authenticated_and_does_not_echo_secret_values(self) -> None:
        status, _, _ = self.raw_request("GET", "/admin", token=None)
        self.assertEqual(status, 401)
        status, content_type, body = self.raw_request("GET", "/admin")
        self.assertEqual(status, 200)
        self.assertTrue(content_type.startswith("text/html"))
        self.assertIn(b"provider console", body)
        self.assertIn(b"savebar", body)
        self.assertIn(b"Readiness ", body)
        self.assertIn(b"No provider calls were made", body)
        self.assertIn(b"API key saved", body)
        self.assertIn(b"Setup needed", body)
        self.assertIn(b"View model details", body)
        self.assertIn(b"family-card", body)
        self.assertIn(b"Next step", body)
        self.assertIn(b"Recommendation", body)
        self.assertIn(b"Recorded detail", body)
        self.assertIn(b"provider contract note", body)
        self.assertIn(b"provider adapter, account metadata, endpoint, or model ID", body)
        self.assertIn(b"human review required", body)
        self.assertIn(b"Approve for bounded smoke test", body)
        self.assertIn(b"Run bounded smoke test", body)
        self.assertIn(b"Activate routing", body)
        self.assertIn(b"Disable routing", body)
        self.assertIn(b"Requests per window", body)
        self.assertIn(b"Tokens per window", body)
        self.assertIn(b"Quota guidance", body)
        self.assertIn(b"Cloudflare account ID", body)
        self.assertIn(b"Anonymous free access", body)
        status, data = self.request("GET", "/admin/config")
        self.assertEqual(status, 200)
        self.assertFalse(data["secrets"]["HF_TOKEN"])
        self.assertIn("quota_guidance", data["providers"][0])
        self.assertEqual(data["providers"][0]["quota_guidance"]["status"], "documented")
        self.assertIn("$0.10", data["providers"][0]["quota_guidance"]["summary"])

    def test_every_catalog_provider_is_exposed_for_token_entry(self) -> None:
        from aipool.provider_catalog import load_catalog

        status, snapshot = self.request("GET", "/admin/config")
        self.assertEqual(status, 200)
        catalog = load_catalog()
        self.assertEqual(
            {row["slug"] for row in snapshot["providers"]},
            {provider.slug for provider in catalog},
        )
        for provider in catalog:
            row = next(item for item in snapshot["providers"] if item["slug"] == provider.slug)
            self.assertEqual(row["provider_slug"], provider.provider_slug)
            self.assertIn("AIPOOL_PROVIDER_" + provider.provider_slug.upper().replace("-", "_") + "_API_KEY", snapshot["secrets"])
        self.assertIn("LLM7.io", {row["provider_name"] for row in snapshot["providers"]})

    def test_root_redirects_to_admin_panel(self) -> None:
        status, content_type, body = self.raw_request("GET", "/")
        self.assertEqual(status, 302)
        self.assertIsNone(content_type)
        self.assertEqual(body, b"")

    def test_admin_panel_persists_allowlisted_values_with_restricted_permissions(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            config_path = Path(directory) / ".aipool.local"
            self.server.shutdown()
            self.server.server_close()
            self.thread.join(timeout=2)
            coordinator = self.server.aipool_coordinator  # type: ignore[attr-defined]
            self.server = make_server(coordinator, port=0, token="test-token", config_path=config_path)
            self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
            self.thread.start()
            self.host, self.port = self.server.server_address[:2]
            status, data = self.request("POST", "/admin/config", {
                "AIPOOL_HF_MODEL": "openai/gpt-oss-20b",
                "AIPOOL_BROWSER_COMMAND": "/usr/local/bin/authorized-browser-wrapper",
                "AIPOOL_COMMAND": "/usr/local/bin/authorized-command-wrapper",
                "HF_TOKEN": "hf-secret",
                "AIPOOL_MODEL_HUGGING_FACE_INFERENCE_PROVIDERS_QWEN_QWEN3_8B_ENABLED": "1",
                "AIPOOL_PROVIDER_HUGGING_FACE_INFERENCE_PROVIDERS_API_KEY": "model-secret",
                "UNSAFE_SETTING": "must-not-persist",
            })
            self.assertEqual(status, 200)
            self.assertTrue(data["updated"])
            text = config_path.read_text()
            self.assertIn("AIPOOL_HF_MODEL=openai/gpt-oss-20b", text)
            self.assertIn("AIPOOL_BROWSER_COMMAND=/usr/local/bin/authorized-browser-wrapper", text)
            self.assertIn("AIPOOL_COMMAND=/usr/local/bin/authorized-command-wrapper", text)
            self.assertIn("HF_TOKEN=hf-secret", text)
            self.assertIn("AIPOOL_PROVIDER_HUGGING_FACE_INFERENCE_PROVIDERS_API_KEY=model-secret", text)
            self.assertNotIn("UNSAFE_SETTING", text)
            self.assertEqual(config_path.stat().st_mode & 0o777, 0o600)
            status, data = self.request("GET", "/admin/config")
            self.assertTrue(data["secrets"]["HF_TOKEN"])
            self.assertTrue(any(provider["has_api_key"] for provider in data["providers"]))
            self.assertNotIn("hf-secret", json.dumps(data))
            self.assertNotIn("model-secret", json.dumps(data))

    def test_api_key_defaults_provider_models_to_enabled(self) -> None:
        status, snapshot = self.request("GET", "/admin/config")
        self.assertEqual(status, 200)
        provider = snapshot["providers"][0]
        key = "AIPOOL_PROVIDER_" + provider["provider_slug"].upper().replace("-", "_") + "_API_KEY"
        status, data = self.request("POST", "/admin/config", {key: "example-key"})
        self.assertEqual(status, 200)
        self.assertTrue(data["updated"])
        self.assertTrue(data["reloaded"])
        self.assertFalse(data["restart_required"])
        self.assertTrue(self.reloads)
        status, snapshot = self.request("GET", "/admin/config")
        self.assertEqual(status, 200)
        family = [item for item in snapshot["providers"] if item["provider_slug"] == provider["provider_slug"]]
        self.assertTrue(family)
        self.assertTrue(all(item["enabled"] for item in family), family)

    def test_provider_family_quota_limits_are_configurable_without_secret_echo(self) -> None:
        status, snapshot = self.request("GET", "/admin/config")
        provider = snapshot["providers"][0]
        prefix = "AIPOOL_PROVIDER_" + provider["provider_slug"].upper().replace("-", "_")
        status, data = self.request("POST", "/admin/config", {
            prefix + "_REQUEST_LIMIT": "5",
            prefix + "_TOKEN_LIMIT": "1200",
            prefix + "_USAGE_WINDOW_SECONDS": "86400",
        })
        self.assertEqual(status, 200)
        self.assertTrue(data["updated"])
        status, snapshot = self.request("GET", "/admin/config")
        family = [item for item in snapshot["providers"] if item["provider_slug"] == provider["provider_slug"]]
        self.assertEqual(family[0]["request_limit"], "5")
        self.assertEqual(family[0]["token_limit"], "1200")
        self.assertEqual(family[0]["usage_window_seconds"], "86400")

    def test_cloudflare_account_id_is_configurable_and_redacted_as_nonsecret_metadata(self) -> None:
        status, snapshot = self.request("GET", "/admin/config")
        self.assertEqual(status, 200)
        provider = next(item for item in snapshot["providers"] if item["provider_name"] == "Cloudflare Workers AI")
        account_field = next(field for field in provider["config_fields"] if field["name"] == "account_id")
        self.assertFalse(account_field["present"])
        status, data = self.request("POST", "/admin/config", {
            account_field["key"]: "account-123",
        })
        self.assertEqual(status, 200)
        self.assertTrue(data["updated"])
        status, snapshot = self.request("GET", "/admin/config")
        provider = next(item for item in snapshot["providers"] if item["provider_name"] == "Cloudflare Workers AI")
        account_field = next(field for field in provider["config_fields"] if field["name"] == "account_id")
        self.assertTrue(account_field["present"])
        self.assertEqual(account_field["value"], "account-123")
        self.assertNotIn("account-123", json.dumps(snapshot["secrets"]))

    def test_configured_catalog_provider_has_explicit_bounded_smoke_test(self) -> None:
        from aipool.benchmark import BenchmarkResult
        from aipool.provider_catalog import load_catalog
        catalog_provider = load_catalog()[0]
        coordinator = self.server.aipool_coordinator  # type: ignore[attr-defined]
        profile = ProviderProfile(
            "catalog:" + catalog_provider.slug, catalog_provider.name, catalog_provider.transport,
            capabilities={"classification": 0.7}, state=ProviderState.QUARANTINED,
        )
        coordinator.registry.register(FixtureAdapter(profile, lambda _: "ok"))
        with patch.object(coordinator, "benchmark_provider", return_value=BenchmarkResult(
            profile.id, {"classification": 1.0}, 1, 1,
        )) as benchmark:
            status, data = self.request("POST", "/admin/provider/smoke-test", {
                "slug": catalog_provider.slug,
            })
        self.assertEqual(status, 400)
        self.assertIn("explicit operator approval", data["error"])
        with patch.object(coordinator, "benchmark_provider", return_value=BenchmarkResult(
            profile.id, {"classification": 1.0}, 1, 1,
        )) as benchmark:
            status, data = self.request("POST", "/admin/provider/smoke-test", {
                "slug": catalog_provider.slug, "operator_approved": True,
            })
        self.assertEqual(status, 200)
        self.assertEqual(data["provider_id"], profile.id)
        self.assertEqual(data["valid"], 1)
        benchmark.assert_called_once_with(profile.id)

    def test_individual_smoke_test_rejects_pending_provider_contract(self) -> None:
        from aipool.provider_catalog import load_catalog
        provider = next(item for item in load_catalog() if item.provider_name == "TokenRouter")
        profile = ProviderProfile(
            "catalog:" + provider.slug, provider.name, provider.transport,
            capabilities={"classification": 0.7}, state=ProviderState.QUARANTINED,
        )
        self.server.aipool_coordinator.registry.register(FixtureAdapter(profile, lambda _: "ok"))  # type: ignore[attr-defined]
        status, data = self.request("POST", "/admin/provider/smoke-test", {
            "slug": provider.slug, "operator_approved": True,
        })
        self.assertEqual(status, 409)
        self.assertIn("provider_preflight_pending", data["error"])

    def test_smoke_batch_plan_is_bounded_and_makes_no_provider_call(self) -> None:
        from aipool.provider_catalog import config_prefix, load_catalog, model_config_prefix
        provider = load_catalog()[0]
        profile = ProviderProfile(
            "catalog:" + provider.slug, provider.name, provider.transport,
            capabilities={"classification": 0.7}, state=ProviderState.QUARANTINED,
            quota_weight=provider.quota_weight,
        )
        self.server.aipool_coordinator.registry.register(FixtureAdapter(profile, lambda _: "ok"))  # type: ignore[attr-defined]
        Path(self.config_directory.name, ".aipool.local").write_text(
            f"{config_prefix(provider)}_API_KEY=key\n{model_config_prefix(provider)}_ENABLED=1\n"
        )
        status, plan = self.request("GET", "/admin/provider/smoke-batch-plan")
        self.assertEqual(status, 200)
        self.assertFalse(plan["network_calls_made"])
        self.assertTrue(plan["approval_required"])
        self.assertEqual(plan["expected_calls"], 3)
        self.assertEqual(plan["selected_models"][0]["slug"], provider.slug)

    def test_smoke_batch_plan_rejects_unbounded_parameters(self) -> None:
        status, data = self.request("GET", "/admin/provider/smoke-batch-plan?max_models=33")
        self.assertEqual(status, 400)
        self.assertIn("max_models", data["error"])

    def test_smoke_batch_requires_approval_and_runs_selected_models_sequentially(self) -> None:
        from aipool.benchmark import BenchmarkResult
        from aipool.provider_catalog import config_prefix, load_catalog, model_config_prefix
        provider = load_catalog()[0]
        profile = ProviderProfile(
            "catalog:" + provider.slug, provider.name, provider.transport,
            capabilities={"classification": 0.7}, state=ProviderState.QUARANTINED,
            quota_weight=provider.quota_weight,
        )
        coordinator = self.server.aipool_coordinator  # type: ignore[attr-defined]
        coordinator.registry.register(FixtureAdapter(profile, lambda _: "ok"))
        Path(self.config_directory.name, ".aipool.local").write_text(
            f"{config_prefix(provider)}_API_KEY=key\n{model_config_prefix(provider)}_ENABLED=1\n"
        )
        status, data = self.request("POST", "/admin/provider/smoke-batch", {"slugs": [provider.slug]})
        self.assertEqual(status, 400)
        self.assertIn("explicit operator approval", data["error"])
        with patch.object(coordinator, "benchmark_providers", return_value={
            "catalog:" + provider.slug: BenchmarkResult("catalog:" + provider.slug, {"classification": 1.0}, 1, 1),
        }) as benchmark:
            status, data = self.request("POST", "/admin/provider/smoke-batch", {
                "operator_approved": True, "slugs": [provider.slug], "cases": 1,
            })
        self.assertEqual(status, 200)
        self.assertTrue(data["sequential"])
        benchmark.assert_called_once()
        called_ids = benchmark.call_args.args[0]
        called_cases = benchmark.call_args.kwargs["cases"]
        self.assertEqual(called_ids, ("catalog:" + provider.slug,))
        self.assertEqual(len(called_cases), 1)

    def test_admin_model_discovery_is_protected_and_redacted(self) -> None:
        status, config = self.request("GET", "/admin/config")
        self.assertEqual(status, 200)
        slug = config["providers"][0]["slug"]
        with patch("aipool.gateway.discover_models") as discover:
            from aipool.model_discovery import ModelDiscovery
            discover.return_value = ModelDiscovery(True, ("model-a", "model-b"), "https://router.example/v1/models")
            status, data = self.request("GET", f"/admin/discover-models?slug={slug}")
        self.assertEqual(status, 200)
        self.assertEqual([model["id"] for model in data["models"]], ["model-a", "model-b"])
        discover.assert_called_once()
        self.assertNotIn("secret", json.dumps(data))
        status, snapshot = self.request("GET", "/admin/config")
        self.assertEqual(status, 200)
        self.assertEqual({row["model_id"] for row in snapshot["discovered_models"]}, {"model-a", "model-b"})

    def test_discovered_model_review_is_explicit_and_persisted(self) -> None:
        status, config = self.request("GET", "/admin/config")
        self.assertEqual(status, 200)
        slug = config["providers"][0]["slug"]
        with patch("aipool.gateway.discover_models") as discover:
            from aipool.model_discovery import ModelDiscovery
            discover.return_value = ModelDiscovery(True, ("review-me",), "https://router.example/v1/models")
            status, _ = self.request("GET", f"/admin/discover-models?slug={slug}")
        self.assertEqual(status, 200)
        status, snapshot = self.request("GET", "/admin/config")
        finding = next(row for row in snapshot["discovered_models"] if row["model_id"] == "review-me")
        self.assertEqual(finding["state"], "quarantined")
        status, data = self.request("POST", "/admin/discovered-model/review", {
            "model_key": finding["model_key"], "decision": "approve", "note": "Reviewed identity and bounded capability evidence.",
        })
        self.assertEqual(status, 200)
        self.assertEqual(data["state"], "approved")
        status, snapshot = self.request("GET", "/admin/config")
        finding = next(row for row in snapshot["discovered_models"] if row["model_id"] == "review-me")
        self.assertEqual(finding["state"], "approved")
        self.assertEqual(finding["review_note"], "Reviewed identity and bounded capability evidence.")

    def test_discovered_model_review_does_not_activate_routing(self) -> None:
        status, data = self.request("POST", "/admin/discovered-model/review", {
            "model_key": "missing", "decision": "approve", "note": "not found",
        })
        self.assertEqual(status, 404)
        self.assertEqual(data["error"], "unknown_discovered_model")

    def test_approved_discovered_model_can_run_bounded_smoke_test_without_activation(self) -> None:
        status, config = self.request("GET", "/admin/config")
        slug = config["providers"][0]["slug"]
        with patch("aipool.gateway.discover_models") as discover:
            from aipool.model_discovery import ModelDiscovery
            discover.return_value = ModelDiscovery(True, ("smoke-me",), "https://router.example/v1/models")
            self.request("GET", f"/admin/discover-models?slug={slug}")
        snapshot = self.request("GET", "/admin/config")[1]
        finding = next(row for row in snapshot["discovered_models"] if row["model_id"] == "smoke-me")
        self.request("POST", "/admin/discovered-model/review", {
            "model_key": finding["model_key"], "decision": "approve", "note": "bounded smoke test is warranted",
        })
        from aipool.benchmark import BenchmarkResult
        with patch("aipool.gateway.run_benchmark", return_value=BenchmarkResult(
            "discovered-test", {"classification": 1.0}, 1, 1,
        )):
            status, data = self.request("POST", "/admin/discovered-model/smoke-test", {
                "model_key": finding["model_key"],
            })
        self.assertEqual(status, 200)
        self.assertEqual(data["state"], "smoke_tested")
        self.assertNotIn(data["provider_id"], {adapter.profile.id for adapter in self.server.aipool_coordinator.registry.all()})
        snapshot = self.request("GET", "/admin/config")[1]
        finding = next(row for row in snapshot["discovered_models"] if row["model_id"] == "smoke-me")
        self.assertEqual(finding["probe_status"], "passed")

    def test_quarantined_discovered_model_cannot_be_smoke_tested(self) -> None:
        status, data = self.request("POST", "/admin/discovered-model/smoke-test", {
            "model_key": "missing", "note": "not found",
        })
        self.assertEqual(status, 404)
        self.assertEqual(data["error"], "unknown_discovered_model")

    def test_activation_requires_passed_smoke_test_and_supports_explicit_rollback(self) -> None:
        status, config = self.request("GET", "/admin/config")
        slug = config["providers"][0]["slug"]
        with patch("aipool.gateway.discover_models") as discover:
            from aipool.model_discovery import ModelDiscovery
            discover.return_value = ModelDiscovery(True, ("activate-me",), "https://router.example/v1/models")
            self.request("GET", f"/admin/discover-models?slug={slug}")
        finding = next(row for row in self.request("GET", "/admin/config")[1]["discovered_models"] if row["model_id"] == "activate-me")
        status, data = self.request("POST", "/admin/discovered-model/activate", {
            "model_key": finding["model_key"], "note": "must fail before evidence",
        })
        self.assertEqual(status, 400)
        self.assertIn("smoke test", data["error"])
        self.request("POST", "/admin/discovered-model/review", {
            "model_key": finding["model_key"], "decision": "approve", "note": "bounded test required",
        })
        from aipool.benchmark import BenchmarkResult
        with patch("aipool.gateway.run_benchmark", return_value=BenchmarkResult("probe", {"classification": 1.0}, 1, 1)):
            self.request("POST", "/admin/discovered-model/smoke-test", {"model_key": finding["model_key"]})
        status, data = self.request("POST", "/admin/discovered-model/activate", {
            "model_key": finding["model_key"], "note": "smoke evidence is sufficient for this bounded provider",
        })
        self.assertEqual(status, 200)
        self.assertEqual(data["state"], "active")
        self.assertTrue(data["reloaded"])
        status, data = self.request("POST", "/admin/discovered-model/deactivate", {
            "model_key": finding["model_key"], "note": "rollback for further review",
        })
        self.assertEqual(status, 200)
        self.assertEqual(data["state"], "smoke_tested")

    def test_queue_enqueue_status_and_cancel(self) -> None:
        task = {"task": "classification", "input_ref": "artifact:queued", "local_estimate": 1}
        status, queued = self.request("POST", "/queue", {"task": task, "idempotency_key": "queued-1"})
        self.assertEqual(status, 202)
        self.assertEqual(queued["status"], "queued")
        status, duplicate = self.request("POST", "/queue", {"task": task, "idempotency_key": "queued-1"})
        self.assertEqual(status, 202)
        self.assertEqual(duplicate["task_id"], queued["task_id"])
        status, record = self.request("GET", f"/queue/{queued['task_id']}")
        self.assertEqual(status, 200)
        self.assertEqual(record["status"], "queued")
        status, cancelled = self.request("POST", f"/queue/{queued['task_id']}/cancel", {})
        self.assertEqual(status, 200)
        self.assertEqual(cancelled["status"], "cancelled")
        status, _ = self.request("GET", "/queue/does-not-exist")
        self.assertEqual(status, 404)

    def test_queue_requires_token(self) -> None:
        status, _ = self.request("POST", "/queue", {"task": "classification", "input_ref": "x"}, token=None)
        self.assertEqual(status, 401)

    def test_non_loopback_requires_token(self) -> None:
        store = Store()
        self.addCleanup(store.close)
        with self.assertRaises(ValueError):
            make_server(Coordinator(ProviderRegistry(), store), host="0.0.0.0", port=0)


if __name__ == "__main__":
    unittest.main()
