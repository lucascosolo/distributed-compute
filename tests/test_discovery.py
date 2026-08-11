import unittest
import tempfile
import sys
from pathlib import Path

from aipool.discovery import (
    CandidateProvider,
    CandidateRegistry,
    CandidateState,
    CommandCandidateProbe,
    ProbeResult,
    QuarantineProbePipeline,
    promote_lead,
)
from aipool.discovery_sources import DiscoveryLead
from aipool.storage import Store


class DiscoveryTests(unittest.TestCase):
    def candidate(self, **changes):
        values = {
            "id": "catalog:model-a",
            "name": "Model A",
            "source": "official-catalog",
            "transport": "openai-compatible",
            "endpoint": "https://provider.example/v1",
            "terms_url": "https://provider.example/terms",
            "authorization": "developer account and documented API access",
        }
        values.update(changes)
        return CandidateProvider(**values)

    def test_candidates_start_quarantined_and_require_explicit_activation(self) -> None:
        registry = CandidateRegistry()
        candidate = self.candidate()
        registry.add(candidate)
        self.assertEqual(registry.get(candidate.id).state, CandidateState.QUARANTINED)
        with self.assertRaisesRegex(ValueError, "explicit operator approval"):
            registry.activate(candidate.id)
        registry.mark_probed(candidate.id, self.passed_probe(candidate.id))
        self.assertEqual(registry.get(candidate.id).state, CandidateState.PROBED)
        registry.activate(candidate.id, operator_approved=True)
        self.assertEqual(registry.get(candidate.id).state, CandidateState.APPROVED)

    def test_policy_rejects_unsafe_access_language(self) -> None:
        registry = CandidateRegistry()
        candidate = self.candidate(authorization="bypass CAPTCHA and evade rate limits")
        registry.add(candidate)
        self.assertEqual(registry.get(candidate.id).state, CandidateState.REJECTED)
        self.assertIn("prohibited access", registry.get(candidate.id).rejection_reason)

    def test_candidate_rejects_credentials_embedded_in_endpoint(self) -> None:
        with self.assertRaisesRegex(ValueError, "credentials"):
            self.candidate(endpoint="https://user:password@provider.example/v1")

    def test_registry_rejects_duplicate_candidate_ids(self) -> None:
        registry = CandidateRegistry()
        registry.add(self.candidate())
        with self.assertRaisesRegex(ValueError, "already exists"):
            registry.add(self.candidate())

    def test_lead_promotion_creates_quarantined_browser_candidate(self) -> None:
        registry = CandidateRegistry()
        lead = DiscoveryLead(
            title="Public coding helper", source_url="https://directory.example/item",
            external_url="https://chat.example/", terms_url="https://chat.example/terms",
            transport_hint="browser-chat",
        )
        candidate = promote_lead(
            registry, lead,
            terms_review="Reviewed 2026-08-11: no explicit binding prohibition found",
        )
        self.assertEqual(candidate.state, CandidateState.QUARANTINED)
        self.assertEqual(candidate.transport, "browser-chat")

    def test_explicit_binding_prohibition_rejects_promoted_candidate(self) -> None:
        registry = CandidateRegistry()
        lead = DiscoveryLead(
            title="Prohibited helper", source_url="https://directory.example/item",
            external_url="https://chat.example/",
        )
        candidate = promote_lead(
            registry, lead, terms_review="Terms reviewed", terms_prohibited=True,
        )
        self.assertEqual(candidate.state, CandidateState.REJECTED)
        self.assertIn("binding prohibition", candidate.rejection_reason)

    def test_lead_without_external_endpoint_cannot_be_promoted(self) -> None:
        with self.assertRaisesRegex(ValueError, "external endpoint"):
            promote_lead(
                CandidateRegistry(),
                DiscoveryLead(title="Discussion", source_url="https://reddit.example/post"),
                terms_review="Terms reviewed",
            )

    def passed_probe(self, candidate_id: str) -> ProbeResult:
        return ProbeResult(
            candidate_id=candidate_id,
            available=True,
            authorized=True,
            context_length=4096,
            output_valid=True,
            latency_ms=120.0,
            restrictions_clear=True,
            cost_known=True,
            automation_supported=True,
        )

    def test_persistent_registry_survives_reopen(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "candidates.sqlite"
            first = Store(path)
            registry = CandidateRegistry(first)
            registry.add(self.candidate())
            first.close()

            second = Store(path)
            reopened = CandidateRegistry(second)
            self.assertEqual(reopened.get("catalog:model-a").state, CandidateState.QUARANTINED)
            second.close()

    def test_persistent_probe_evidence_survives_reopen(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "candidates.sqlite"
            first = Store(path)
            registry = CandidateRegistry(first)
            candidate = self.candidate()
            registry.add(candidate)
            registry.mark_probed(candidate.id, self.passed_probe(candidate.id))
            first.close()

            second = Store(path)
            reopened = CandidateRegistry(second)
            self.assertEqual(reopened.get(candidate.id).state, CandidateState.PROBED)
            self.assertTrue(reopened.probe_result(candidate.id).passed)
            second.close()

    def test_probe_pipeline_is_bounded_and_successful_candidates_need_approval(self) -> None:
        registry = CandidateRegistry()
        first = self.candidate()
        second = self.candidate(id="catalog:model-b")
        registry.add(first)
        registry.add(second)
        calls: list[str] = []

        def probe(candidate):
            calls.append(candidate.id)
            return self.passed_probe(candidate.id)

        reports = QuarantineProbePipeline(registry, probe, max_candidates=1).run()
        self.assertEqual(calls, [first.id])
        self.assertEqual(len(reports), 1)
        self.assertEqual(registry.get(first.id).state, CandidateState.PROBED)
        self.assertEqual(registry.get(second.id).state, CandidateState.QUARANTINED)
        with self.assertRaisesRegex(ValueError, "explicit operator approval"):
            registry.activate(first.id)
        registry.activate(first.id, operator_approved=True)

    def test_command_candidate_probe_receives_metadata_and_normalizes_result(self) -> None:
        command = (
            sys.executable, "-c",
            "import json,sys; c=json.load(sys.stdin); print(json.dumps({"
            "'candidate_id':c['id'],'available':True,'authorized':True,"
            "'context_length':4096,'output_valid':True,'latency_ms':3.5,"
            "'restrictions_clear':True,'cost_known':True,'automation_supported':True"
            "}))",
        )
        result = CommandCandidateProbe(command)(self.candidate())
        self.assertTrue(result.passed)
        self.assertEqual(result.candidate_id, "catalog:model-a")

    def test_command_candidate_probe_times_out_without_shell_execution(self) -> None:
        command = (sys.executable, "-c", "import time; time.sleep(1)")
        result = CommandCandidateProbe(command, timeout_seconds=0.01)(self.candidate())
        self.assertFalse(result.passed)
        self.assertIn("timed out", result.reason or "")

    def test_failed_probe_never_promotes_candidate(self) -> None:
        registry = CandidateRegistry()
        candidate = self.candidate()
        registry.add(candidate)

        def probe(_candidate):
            raise RuntimeError("temporary outage")

        reports = QuarantineProbePipeline(registry, probe).run()
        self.assertFalse(reports[0].passed)
        self.assertEqual(registry.get(candidate.id).state, CandidateState.QUARANTINED)
        with self.assertRaisesRegex(ValueError, "successful probe"):
            registry.activate(candidate.id, operator_approved=True)


if __name__ == "__main__":
    unittest.main()
