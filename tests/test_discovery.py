import unittest

from aipool.discovery import CandidateProvider, CandidateRegistry, CandidateState


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


if __name__ == "__main__":
    unittest.main()
