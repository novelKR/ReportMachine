from __future__ import annotations

import csv
import json
import tempfile
import unittest
from pathlib import Path

from reportmachine_runner.audit import run_finalize_audits
from reportmachine_runner.preflight import build_preflight_result
from reportmachine_runner.source_ingest import SourceIngestConfig, run_source_ingest


SOURCE_HEADER = [
    "source_id",
    "article_type",
    "company",
    "period_role",
    "period",
    "doc_kind",
    "issuer",
    "date",
    "title",
    "url",
    "source_tier",
    "required",
    "local_path",
    "notes",
]


class PreflightAndAuditTests(unittest.TestCase):
    def copy_schema(self, root: Path, rel: str) -> None:
        source = Path(__file__).resolve().parents[1] / rel
        target = root / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(source.read_text(encoding="utf-8"), encoding="utf-8")

    def write_sources(self, root: Path, rows: list[dict[str, str]]) -> None:
        (root / "inputs").mkdir(parents=True, exist_ok=True)
        with (root / "inputs" / "sources.csv").open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=SOURCE_HEADER)
            writer.writeheader()
            writer.writerows(rows)

    def earnings_row(self, source_id: str, period_role: str, doc_kind: str, local_path: str = "", url: str = "") -> dict[str, str]:
        return {
            "source_id": source_id,
            "article_type": "earnings",
            "company": "Example",
            "period_role": period_role,
            "period": "Q1",
            "doc_kind": doc_kind,
            "issuer": "Example",
            "date": "2026-01-01",
            "title": source_id,
            "url": url,
            "source_tier": "primary",
            "required": "true",
            "local_path": local_path,
            "notes": "",
        }

    def run_ingest(self, root: Path) -> str:
        result = run_source_ingest(
            SourceIngestConfig(
                root=root,
                mode="earnings",
                run_id="test_run",
                sources_path="inputs/sources.csv",
                work_run_dir="work/runs/test_run",
                allow_downloads=False,
            )
        )
        return result.paths.source_candidate_ledger

    def test_earnings_preflight_passes_with_four_required_local_sources(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "fixtures").mkdir()
            rows = []
            for source_id, period_role, doc_kind in [
                ("cur_release", "current", "earnings_release"),
                ("cur_deck", "current", "investor_presentation"),
                ("prev_release", "previous", "earnings_release"),
                ("prev_deck", "previous", "investor_presentation"),
            ]:
                rel = f"fixtures/{source_id}.txt"
                (root / rel).write_text(source_id, encoding="utf-8")
                rows.append(self.earnings_row(source_id, period_role, doc_kind, local_path=rel))
            self.write_sources(root, rows)

            ledger = self.run_ingest(root)
            result = build_preflight_result(
                root=root,
                mode="earnings",
                run_id="test_run",
                sources_path="inputs/sources.csv",
                source_candidate_ledger=ledger,
            )

            self.assertTrue(result.passed, result.payload)
            self.assertEqual(result.payload["required_sources"]["previous_investor_presentation"], "pass")

    def test_earnings_preflight_fails_when_previous_deck_is_missing(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "fixtures").mkdir()
            rows = []
            for source_id, period_role, doc_kind in [
                ("cur_release", "current", "earnings_release"),
                ("cur_deck", "current", "investor_presentation"),
                ("prev_release", "previous", "earnings_release"),
            ]:
                rel = f"fixtures/{source_id}.txt"
                (root / rel).write_text(source_id, encoding="utf-8")
                rows.append(self.earnings_row(source_id, period_role, doc_kind, local_path=rel))
            self.write_sources(root, rows)

            ledger = self.run_ingest(root)
            result = build_preflight_result(
                root=root,
                mode="earnings",
                run_id="test_run",
                sources_path="inputs/sources.csv",
                source_candidate_ledger=ledger,
            )

            self.assertFalse(result.passed)
            self.assertEqual(result.payload["required_sources"]["previous_investor_presentation"], "fail")

    def test_earnings_preflight_fails_for_url_only_source_when_network_is_disabled(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "fixtures").mkdir()
            rows = []
            for source_id, period_role, doc_kind in [
                ("cur_release", "current", "earnings_release"),
                ("cur_deck", "current", "investor_presentation"),
                ("prev_release", "previous", "earnings_release"),
            ]:
                rel = f"fixtures/{source_id}.txt"
                (root / rel).write_text(source_id, encoding="utf-8")
                rows.append(self.earnings_row(source_id, period_role, doc_kind, local_path=rel))
            rows.append(
                self.earnings_row(
                    "prev_deck",
                    "previous",
                    "investor_presentation",
                    url="https://sec.gov/example.pdf",
                )
            )
            self.write_sources(root, rows)

            ledger = self.run_ingest(root)
            result = build_preflight_result(
                root=root,
                mode="earnings",
                run_id="test_run",
                sources_path="inputs/sources.csv",
                source_candidate_ledger=ledger,
            )

            self.assertFalse(result.passed)
            self.assertIn("previous_investor_presentation", "\n".join(result.payload["blocking_errors"]))

    def test_preview_preflight_fails_when_preview_prompt_is_missing(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            self.write_sources(root, [])

            result = build_preflight_result(
                root=root,
                mode="preview",
                run_id="test_run",
                sources_path="inputs/sources.csv",
                source_candidate_ledger="work/runs/test_run/ledgers/source_ledger.candidate.csv",
            )

            self.assertFalse(result.passed)
            self.assertIn("PREVIEW_PROMPT.md", result.payload["blocking_errors"][0])

    def test_finalize_audit_blocks_unsupported_claim_and_caption_mismatch(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            self.copy_schema(root, "schemas/claim_ledger.schema.json")
            output_dir = root / "outputs" / "test_run"
            output_dir.mkdir(parents=True)
            (output_dir / "article.md").write_text(
                "## 한눈에 보기\n\n![slide](work/runs/test_run/images/rendered/slide.png)\n",
                encoding="utf-8",
            )
            (output_dir / "article_result.json").write_text(
                json.dumps(
                    {
                        "article_type": "earnings",
                        "checklist_status": "pass",
                    }
                ),
                encoding="utf-8",
            )
            (output_dir / "claim_ledger.json").write_text(
                json.dumps(
                    [
                        {
                            "claim_id": "CLM-001",
                            "support_status": "unsupported",
                            "needs_human_review": False,
                        }
                    ]
                ),
                encoding="utf-8",
            )
            (output_dir / "source_ledger.csv").write_text(
                "source_id,url,local_path,sha256\nsrc_001,,,abc\n",
                encoding="utf-8",
            )
            with (output_dir / "image_ledger.csv").open("w", newline="", encoding="utf-8") as handle:
                writer = csv.DictWriter(
                    handle,
                    fieldnames=[
                        "image_path",
                        "actual_slide_title",
                        "detected_key_datapoints",
                        "caption",
                        "article_anchor_before",
                        "article_anchor_after",
                        "keep_or_drop",
                    ],
                )
                writer.writeheader()
                writer.writerow(
                    {
                        "image_path": "work/runs/test_run/images/rendered/slide.png",
                        "actual_slide_title": "Orders",
                        "detected_key_datapoints": "orders improved",
                        "caption": "Orders rose 99%.",
                        "article_anchor_before": "before",
                        "article_anchor_after": "after",
                        "keep_or_drop": "keep",
                    }
                )

            errors = run_finalize_audits(
                root=root,
                mode="earnings",
                article_result="outputs/test_run/article_result.json",
                article_output="outputs/test_run/article.md",
                claim_ledger_output="outputs/test_run/claim_ledger.json",
                image_ledger_output="outputs/test_run/image_ledger.csv",
                source_ledger_output="outputs/test_run/source_ledger.csv",
                pdf_index="work/runs/test_run/research/pdf_index.jsonl",
            )

            self.assertTrue(any("blocking claim" in error for error in errors), errors)
            self.assertTrue(any("99" in error for error in errors), errors)

    def test_finalize_audit_rejects_schema_invalid_claim_ledger_rows(self) -> None:
        cases = [
            (
                [{"claim_id": "CLM-001"}],
                ["claim_text", "support_status", "needs_human_review"],
            ),
            (
                [
                    {
                        "claim_id": "CLM-001",
                        "claim_text": "Claim",
                        "claim_type": "core",
                        "article_section": "body",
                        "source_ids": ["src_001"],
                        "source_quote_or_datapoint": "data",
                        "confidence": "high",
                        "support_status": "maybe",
                        "needs_human_review": "false",
                    }
                ],
                ["support_status", "needs_human_review"],
            ),
            (
                [
                    {
                        "claim_id": "CLM-001",
                        "claim_text": "Claim",
                        "claim_type": "core",
                        "article_section": "body",
                        "source_ids": [],
                        "source_quote_or_datapoint": "",
                        "confidence": "high",
                        "support_status": "supported",
                        "needs_human_review": False,
                    }
                ],
                ["source_ids", "source_quote_or_datapoint"],
            ),
        ]
        for payload, expected_fragments in cases:
            with self.subTest(payload=payload):
                with tempfile.TemporaryDirectory() as temp_dir:
                    root = Path(temp_dir)
                    self.copy_schema(root, "schemas/claim_ledger.schema.json")
                    output_dir = root / "outputs" / "test_run"
                    output_dir.mkdir(parents=True)
                    (output_dir / "article.md").write_text("## 한눈에 보기\n", encoding="utf-8")
                    (output_dir / "article_result.json").write_text(
                        json.dumps({"article_type": "earnings", "checklist_status": "pass"}),
                        encoding="utf-8",
                    )
                    (output_dir / "claim_ledger.json").write_text(
                        json.dumps(payload),
                        encoding="utf-8",
                    )
                    (output_dir / "source_ledger.csv").write_text(
                        "source_id,url,local_path,sha256\nsrc_001,,,abc\n",
                        encoding="utf-8",
                    )
                    (output_dir / "image_ledger.csv").write_text(
                        "image_path,keep_or_drop\n,drop\n",
                        encoding="utf-8",
                    )

                    errors = run_finalize_audits(
                        root=root,
                        mode="earnings",
                        article_result="outputs/test_run/article_result.json",
                        article_output="outputs/test_run/article.md",
                        claim_ledger_output="outputs/test_run/claim_ledger.json",
                        image_ledger_output="outputs/test_run/image_ledger.csv",
                        source_ledger_output="outputs/test_run/source_ledger.csv",
                        pdf_index="work/runs/test_run/research/pdf_index.jsonl",
                    )

                    joined = "\n".join(errors)
                    self.assertIn("claim ledger schema validation failed", joined)
                    for fragment in expected_fragments:
                        self.assertIn(fragment, joined)


if __name__ == "__main__":
    unittest.main()
