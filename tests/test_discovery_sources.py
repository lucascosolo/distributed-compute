import tempfile
import unittest
from pathlib import Path

from aipool.discovery_sources import DiscoveryLead, DiscoveryRunner, LeadRegistry, RedditSearchSource
from aipool.storage import Store


class DiscoverySourceTests(unittest.TestCase):
    def reddit_payload(self):
        return {
            "data": {"children": [
                {"data": {
                    "title": "A free coding chatbot",
                    "permalink": "/r/OpenSourceAI/comments/a/free_chat/",
                    "url": "https://chat.example/",
                    "selftext": "A browser-only assistant with no API key.",
                }},
                {"data": {
                    "title": "Another assistant",
                    "permalink": "/r/Chatbots/comments/b/another/",
                    "url": "https://www.reddit.com/r/Chatbots/comments/b/another/",
                    "selftext": "discussion",
                }},
                {"data": {"title": "third result", "permalink": "/r/x/comments/c/third/"}},
            ]}
        }

    def test_reddit_source_is_bounded_and_preserves_provenance(self) -> None:
        requested: list[str] = []
        source = RedditSearchSource(
            "free chatbot", subreddit="OpenSourceAI", max_results=2,
            fetch=lambda url: requested.append(url) or self.reddit_payload(),
            clock=lambda: 123.0,
        )
        leads = source.collect()
        self.assertEqual(len(leads), 2)
        self.assertIn("limit=2", requested[0])
        self.assertEqual(leads[0].title, "A free coding chatbot")
        self.assertEqual(leads[0].external_url, "https://chat.example/")
        self.assertEqual(leads[0].discovered_at, 123.0)
        self.assertIn("reddit.com/r/OpenSourceAI", leads[0].source_url)

    def test_runner_deduplicates_leads_and_caps_sources_and_total_leads(self) -> None:
        lead = DiscoveryLead(
            title="same", source_url="https://source.example/a", external_url="https://chat.example/",
        )
        class Source:
            def collect(self):
                return (lead, lead)

        result = DiscoveryRunner((Source(), Source()), max_sources=1, max_leads=1).run()
        self.assertEqual(result.leads, (lead,))
        self.assertEqual(result.errors, ())

    def test_persistent_lead_registry_keeps_provenance_and_deduplicates(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "leads.sqlite"
            store = Store(path)
            registry = LeadRegistry(store)
            lead = DiscoveryLead(
                title="assistant", source_url="https://reddit.example/post",
                summary="public discussion", discovered_at=10.0,
            )
            registry.add(lead, now=11.0)
            registry.add(lead, now=12.0)
            store.close()

            reopened_store = Store(path)
            reopened = LeadRegistry(reopened_store)
            records = reopened.all()
            self.assertEqual(len(records), 1)
            self.assertEqual(records[0].lead_id, lead.lead_id)
            self.assertEqual(records[0].hit_count, 2)
            reopened_store.close()

    def test_lead_requires_web_provenance(self) -> None:
        with self.assertRaisesRegex(ValueError, "absolute http or https"):
            DiscoveryLead(title="bad", source_url="not-a-url")


if __name__ == "__main__":
    unittest.main()
