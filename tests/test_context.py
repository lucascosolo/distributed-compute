import tempfile
import unittest
from pathlib import Path

from aipool.artifacts import ArtifactStore
from aipool.context import ContextPacket
from aipool.domain import TaskEnvelope


class ContextPacketTests(unittest.TestCase):
    def test_packet_reconstructs_artifact_context_for_a_constrained_provider(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            artifacts = ArtifactStore(Path(directory))
            reference = artifacts.put(b"def add(a, b):\n    return a + b\n")
            task = TaskEnvelope(
                task="coding",
                input_ref=reference,
                requirements={"objective": "Add a focused test for add", "output": "patch"},
            )

            packet = ContextPacket.from_task(task, artifacts)
            rendered = packet.render()
            self.assertIn("Add a focused test for add", rendered)
            self.assertIn("def add(a, b)", rendered)
            self.assertIn("untrusted reference material", rendered)

    def test_packet_has_a_hard_render_bound(self) -> None:
        task = TaskEnvelope(
            task="summarization",
            input_ref="inline-source",
            requirements={"objective": "Summarize the source"},
        )
        packet = ContextPacket.from_task(task, None, max_chars=256)
        rendered = packet.render()
        self.assertLessEqual(len(rendered), 256)
        self.assertIn("[context truncated]", rendered)

    def test_explicit_context_refs_must_be_artifacts(self) -> None:
        task = TaskEnvelope(
            task="coding",
            input_ref="artifact:sha256:" + "0" * 64,
            requirements={"context_refs": ["not-an-artifact"]},
        )
        with self.assertRaisesRegex(ValueError, "artifact reference"):
            ContextPacket.from_task(task, None)

    def test_context_refs_must_be_bounded(self) -> None:
        task = TaskEnvelope(
            task="research",
            input_ref="source",
            requirements={"context_refs": ["artifact:sha256:" + "0" * 64] * 17},
        )
        with self.assertRaisesRegex(ValueError, "at most 16"):
            ContextPacket.from_task(task, None)


if __name__ == "__main__":
    unittest.main()
