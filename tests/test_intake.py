from __future__ import annotations

import csv
import tempfile
import unittest
from pathlib import Path

from reportmachine_runner.intake import (
    ArticleBrief,
    SOURCE_DISCOVERY_REQUIRED_ID,
    SOURCE_DISCOVERY_REQUIRED_NOTE,
    write_intake_files,
)


class IntakeRenderingTests(unittest.TestCase):
    def read_rows(self, path: Path) -> list[dict[str, str]]:
        with path.open(newline="", encoding="utf-8") as handle:
            return list(csv.DictReader(handle))

    def test_write_intake_files_creates_run_local_topic_and_discovery_source(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            brief = ArticleBrief(
                mode="industry",
                topic="AI 데이터센터 전력 인프라 병목과 전력기기 공급망",
                core_question="전력기기 공급망이 왜 병목이 되는가?",
                recent_trigger="최근 데이터센터 전력 수요 전망",
                required_source_notes=("IEA and official utility planning materials",),
                output_notes="full report",
            )

            paths = write_intake_files(root=root, run_id="test_run", brief=brief)

            topic = root / paths.topic
            sources = root / paths.sources
            self.assertIn("Generated from an approved conversational intake brief", topic.read_text(encoding="utf-8"))
            self.assertIn("전력기기 공급망이 왜 병목", topic.read_text(encoding="utf-8"))
            rows = self.read_rows(sources)
            self.assertEqual(rows[0]["source_id"], SOURCE_DISCOVERY_REQUIRED_ID)
            self.assertEqual(rows[0]["required"], "true")
            self.assertIn(SOURCE_DISCOVERY_REQUIRED_NOTE, rows[0]["notes"])

    def test_write_intake_files_can_mirror_to_inputs_when_explicitly_requested(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            brief = ArticleBrief(mode="industry", topic="Power grid topic")

            paths = write_intake_files(
                root=root,
                run_id="test_run",
                brief=brief,
                mirror_inputs=True,
            )

            self.assertEqual(paths.mirrored_topic, "inputs/topic.md")
            self.assertEqual(paths.mirrored_sources, "inputs/sources.csv")
            self.assertIn("Power grid topic", (root / "inputs" / "topic.md").read_text(encoding="utf-8"))
            rows = self.read_rows(root / "inputs" / "sources.csv")
            self.assertEqual(rows[0]["source_id"], SOURCE_DISCOVERY_REQUIRED_ID)


if __name__ == "__main__":
    unittest.main()
