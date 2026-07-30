from __future__ import annotations

import csv
import json
import tempfile
import unittest
from hashlib import sha256
from pathlib import Path

from reportmachine_runner.audit import run_finalize_audits
from reportmachine_runner.intake import ArticleBrief, write_intake_files
from reportmachine_runner.preflight import PreflightResult, build_preflight_result
from reportmachine_runner.run_outputs import write_preflight_failure_outputs
from reportmachine_runner.schema_validation import validate_json_artifact
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

    def test_industry_preflight_warns_but_does_not_block_on_discovery_placeholder(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            paths = write_intake_files(
                root=root,
                run_id="test_run",
                brief=ArticleBrief(mode="industry", topic="Example topic"),
            )
            ingest = run_source_ingest(
                SourceIngestConfig(
                    root=root,
                    mode="industry",
                    run_id="test_run",
                    sources_path=paths.sources,
                    work_run_dir="work/runs/test_run",
                    allow_downloads=False,
                )
            )

            result = build_preflight_result(
                root=root,
                mode="industry",
                run_id="test_run",
                sources_path=paths.sources,
                source_candidate_ledger=ingest.paths.source_candidate_ledger,
            )

            self.assertTrue(result.passed, result.payload)
            self.assertIn("intake placeholder", "\n".join(result.payload["warnings"]))

    def industry_row(self, source_id: str, *, url: str = "", local_path: str = "") -> dict[str, str]:
        return {
            "source_id": source_id,
            "article_type": "industry",
            "company": "",
            "period_role": "",
            "period": "",
            "doc_kind": "report",
            "issuer": "Example",
            "date": "2026-01-01",
            "title": source_id,
            "url": url,
            "source_tier": "primary",
            "required": "true",
            "local_path": local_path,
            "notes": "",
        }

    def industry_preflight(self, root: Path, **overrides) -> PreflightResult:
        # No candidate ledger is written, matching a --no-source-ingest run.
        kwargs = {
            "root": root,
            "mode": "industry",
            "run_id": "test_run",
            "sources_path": "inputs/sources.csv",
            "source_candidate_ledger": "work/runs/test_run/source_candidates.csv",
        }
        kwargs.update(overrides)
        return build_preflight_result(**kwargs)

    def test_industry_preflight_blocks_required_source_without_candidate_or_local_copy(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            self.write_sources(root, [self.industry_row("remote_only", url="https://example.com/a")])

            result = self.industry_preflight(root)

            self.assertFalse(result.passed, result.payload)
            joined = "\n".join(result.payload["blocking_errors"])
            self.assertIn("remote_only", joined)
            self.assertIn("no verified local copy", joined)

    def test_industry_preflight_passes_required_source_with_usable_local_copy(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "fixtures").mkdir()
            (root / "fixtures" / "doc.txt").write_text("body", encoding="utf-8")
            self.write_sources(
                root,
                [self.industry_row("local_doc", local_path="fixtures/doc.txt")],
            )

            result = self.industry_preflight(root)

            self.assertTrue(result.passed, result.payload)

    def test_waiver_downgrades_unverified_required_source_to_a_warning(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            self.write_sources(root, [self.industry_row("remote_only", url="https://example.com/a")])

            result = self.industry_preflight(root, waived_blocked_sources=("remote_only",))

            self.assertTrue(result.passed, result.payload)
            self.assertIn("waived", "\n".join(result.payload["warnings"]))

    def candidate_ledger(self, root: Path, rows: list[dict[str, str]]) -> str:
        rel = "work/runs/test_run/source_candidates.csv"
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        header = ["source_id", "ingest_status", "local_path", "sha256", "blocked_reason"]
        with path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=header)
            writer.writeheader()
            writer.writerows(rows)
        return rel

    def test_required_source_blocks_when_ledger_status_outlives_the_local_file(self) -> None:
        # A recorded `downloaded` status is not proof: the file may have been removed.
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            self.write_sources(root, [self.industry_row("s1", url="https://example.com/a")])
            ledger = self.candidate_ledger(
                root,
                [
                    {
                        "source_id": "s1",
                        "ingest_status": "downloaded",
                        "local_path": "work/runs/test_run/sources/s1.pdf",
                        "sha256": "a" * 64,
                        "blocked_reason": "",
                    }
                ],
            )

            result = self.industry_preflight(root, source_candidate_ledger=ledger)

            self.assertFalse(result.passed, result.payload)
            self.assertIn("missing from disk", "\n".join(result.payload["blocking_errors"]))

    def test_required_source_blocks_when_digest_is_absent(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "fixtures").mkdir()
            (root / "fixtures" / "s1.pdf").write_text("body", encoding="utf-8")
            self.write_sources(root, [self.industry_row("s1", url="https://example.com/a")])
            ledger = self.candidate_ledger(
                root,
                [
                    {
                        "source_id": "s1",
                        "ingest_status": "downloaded",
                        "local_path": "fixtures/s1.pdf",
                        "sha256": "",
                        "blocked_reason": "",
                    }
                ],
            )

            result = self.industry_preflight(root, source_candidate_ledger=ledger)

            self.assertFalse(result.passed, result.payload)
            self.assertIn("no sha256 digest", "\n".join(result.payload["blocking_errors"]))

    def test_required_source_passes_when_candidate_is_verifiably_usable(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "fixtures").mkdir()
            (root / "fixtures" / "s1.pdf").write_text("body", encoding="utf-8")
            self.write_sources(root, [self.industry_row("s1", url="https://example.com/a")])
            ledger = self.candidate_ledger(
                root,
                [
                    {
                        "source_id": "s1",
                        "ingest_status": "downloaded",
                        "local_path": "fixtures/s1.pdf",
                        "sha256": sha256(b"body").hexdigest(),
                        "blocked_reason": "",
                    }
                ],
            )

            result = self.industry_preflight(root, source_candidate_ledger=ledger)

            self.assertTrue(result.passed, result.payload)

    def test_required_source_blocks_when_recorded_digest_does_not_match_file(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "fixtures").mkdir()
            (root / "fixtures" / "s1.pdf").write_text("body", encoding="utf-8")
            self.write_sources(root, [self.industry_row("s1", url="https://example.com/a")])
            ledger = self.candidate_ledger(
                root,
                [
                    {
                        "source_id": "s1",
                        "ingest_status": "downloaded",
                        "local_path": "fixtures/s1.pdf",
                        "sha256": "b" * 64,
                        "blocked_reason": "",
                    }
                ],
            )

            result = self.industry_preflight(root, source_candidate_ledger=ledger)

            self.assertFalse(result.passed, result.payload)
            self.assertIn("does not match", "\n".join(result.payload["blocking_errors"]))

    def test_downloaded_hashed_status_uses_the_common_verified_status_contract(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "fixtures").mkdir()
            source = root / "fixtures" / "s1.pdf"
            source.write_text("body", encoding="utf-8")
            self.write_sources(root, [self.industry_row("s1", url="https://example.com/a")])
            ledger = self.candidate_ledger(
                root,
                [
                    {
                        "source_id": "s1",
                        "ingest_status": "downloaded_hashed",
                        "local_path": "fixtures/s1.pdf",
                        "sha256": sha256(source.read_bytes()).hexdigest(),
                        "blocked_reason": "",
                    }
                ],
            )

            result = self.industry_preflight(root, source_candidate_ledger=ledger)

            self.assertTrue(result.passed, result.payload)

    def test_blocked_status_keeps_the_ingest_reason(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            self.write_sources(root, [self.industry_row("s1", url="https://example.com/a")])
            ledger = self.candidate_ledger(
                root,
                [
                    {
                        "source_id": "s1",
                        "ingest_status": "blocked",
                        "local_path": "",
                        "sha256": "",
                        "blocked_reason": "403 denied",
                    }
                ],
            )

            result = self.industry_preflight(root, source_candidate_ledger=ledger)

            self.assertFalse(result.passed, result.payload)
            joined = "\n".join(result.payload["blocking_errors"])
            self.assertIn("`blocked`", joined)
            self.assertIn("403 denied", joined)

    def test_waiver_downgrades_a_stale_ledger_row_to_a_warning(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            self.write_sources(root, [self.industry_row("s1", url="https://example.com/a")])
            ledger = self.candidate_ledger(
                root,
                [
                    {
                        "source_id": "s1",
                        "ingest_status": "downloaded",
                        "local_path": "fixtures/gone.pdf",
                        "sha256": "c" * 64,
                        "blocked_reason": "",
                    }
                ],
            )

            result = self.industry_preflight(
                root,
                source_candidate_ledger=ledger,
                waived_blocked_sources=("s1",),
            )

            self.assertTrue(result.passed, result.payload)
            self.assertIn("waived", "\n".join(result.payload["warnings"]))

    def test_duplicate_source_ids_are_non_waivable_preflight_errors(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "fixtures").mkdir()
            (root / "fixtures" / "first.txt").write_text("first", encoding="utf-8")
            (root / "fixtures" / "second.txt").write_text("second", encoding="utf-8")
            self.write_sources(
                root,
                [
                    self.industry_row("s1", local_path="fixtures/first.txt"),
                    self.industry_row("s1", local_path="fixtures/second.txt"),
                ],
            )

            result = self.industry_preflight(root, waived_blocked_sources=("s1",))

            self.assertFalse(result.passed, result.payload)
            self.assertIn(
                "sources file contains duplicate source_id: s1",
                result.payload["blocking_errors"],
            )

    def test_duplicate_candidate_ids_do_not_use_the_last_usable_row(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "fixtures").mkdir()
            source = root / "fixtures" / "s1.pdf"
            source.write_text("body", encoding="utf-8")
            self.write_sources(root, [self.industry_row("s1", url="https://example.com/a")])
            ledger = self.candidate_ledger(
                root,
                [
                    {
                        "source_id": "s1",
                        "ingest_status": "blocked",
                        "local_path": "",
                        "sha256": "",
                        "blocked_reason": "403 denied",
                    },
                    {
                        "source_id": "s1",
                        "ingest_status": "downloaded_hashed",
                        "local_path": "fixtures/s1.pdf",
                        "sha256": sha256(source.read_bytes()).hexdigest(),
                        "blocked_reason": "",
                    },
                ],
            )

            result = self.industry_preflight(
                root,
                source_candidate_ledger=ledger,
                waived_blocked_sources=("s1",),
            )

            self.assertFalse(result.passed, result.payload)
            self.assertIn(
                "source candidate ledger contains duplicate source_id: s1",
                result.payload["blocking_errors"],
            )

    def test_preflight_failure_result_is_schema_valid_and_records_the_article_hash(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            self.copy_schema(root, "schemas/article_result.schema.json")
            (root / "outputs" / "test_run").mkdir(parents=True, exist_ok=True)
            (root / "work" / "runs" / "test_run").mkdir(parents=True, exist_ok=True)

            write_preflight_failure_outputs(
                root=root,
                mode="industry",
                preflight_payload={"blocking_errors": ["required source is unusable"]},
                output="outputs/test_run/article_result.json",
                article_output="outputs/test_run/article.md",
                source_ledger_output="outputs/test_run/source_ledger.csv",
                image_ledger_output="outputs/test_run/image_ledger.csv",
                claim_ledger_output="outputs/test_run/claim_ledger.json",
                preflight_result_output="outputs/test_run/preflight_result.json",
                run_manifest_output="outputs/test_run/run_manifest.json",
                earnings_delta_matrix_output="outputs/test_run/earnings_delta_matrix.json",
                earnings_delta_matrix_md="outputs/test_run/earnings_delta_matrix.md",
                source_candidate_ledger="work/runs/test_run/source_candidates.csv",
            )

            payload, errors = validate_json_artifact(
                root=root,
                artifact="outputs/test_run/article_result.json",
                schema="schemas/article_result.schema.json",
                label="article result",
            )
            self.assertEqual(errors, [])
            self.assertEqual(payload["checklist_status"], "fail")
            expected = sha256(
                (root / "outputs" / "test_run" / "article.md").read_bytes()
            ).hexdigest()
            self.assertEqual(payload["article_sha256"], expected)

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
