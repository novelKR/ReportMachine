"""Mutation tests for the final cross audit.

Each test injects one deliberate defect into an otherwise clean run and asserts the audit
refuses to pass. These are the failure modes that previously shipped undetected.
"""

from __future__ import annotations

import csv
import json
import os
import shutil
import tempfile
import unittest
from hashlib import sha256
from pathlib import Path

from reportmachine_runner.final_sync import run_final_sync_audit
from reportmachine_runner.phase_contracts import build_run_artifacts


RUN_ID = "mutation_run"
PROJECT_ROOT = Path(__file__).resolve().parents[1]

ARTICLE_TEXT = """# 반도체 공급 구조가 다시 시험받고 있습니다

*공동생산 구조가 방어선인지 집중 위험인지 묻습니다*

## 최근 계기: 신규 라인이 가동에 들어갔습니다

공식 발표에 따르면 신규 라인이 가동을 시작했습니다.

## 고객 인증은 발표보다 느리게 움직입니다

양산 발표는 고객 인증 완료를 의미하지 않습니다.

## 결론

판단은 조건부로 유지합니다.
"""

SOURCE_TEXT = "공식 발표에 따르면 신규 라인이 가동을 시작했습니다. 수율은 공개되지 않았습니다."


def digest_of(text: str) -> str:
    return sha256(text.encode("utf-8")).hexdigest()


class MutationAuditTests(unittest.TestCase):
    def build_clean_run(self, root: Path) -> None:
        """Write a run whose ledgers and article are fully in sync."""

        work = root / "work" / "runs" / RUN_ID
        outputs = root / "outputs" / RUN_ID
        (work / "evidence").mkdir(parents=True, exist_ok=True)
        (work / "research" / "extracted_text").mkdir(parents=True, exist_ok=True)
        outputs.mkdir(parents=True, exist_ok=True)
        for rel in (
            "schemas/article_result.schema.json",
            "schemas/claim_ledger.schema.json",
            "schemas/paragraph_map.schema.json",
            "schemas/evidence_card.schema.json",
        ):
            target = root / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(PROJECT_ROOT / rel, target)

        (outputs / "article.md").write_text(ARTICLE_TEXT, encoding="utf-8")
        (outputs / "research_appendix.md").write_text("# 연구 부록\n", encoding="utf-8")
        (work / "research" / "extracted_text" / "S001.txt").write_text(
            SOURCE_TEXT, encoding="utf-8"
        )

        self.write_jsonl(
            work / "evidence" / "evidence_cards.jsonl",
            [
                {
                    "evidence_id": "E001",
                    "claim_ids": ["C001"],
                    "evidence_class": "issuer_claim",
                    "source_id": "S001",
                    "exact_quote": "신규 라인이 가동을 시작했습니다",
                    "locator": {"page": None, "section": "본문 1문단", "url_fragment": None},
                    "supports": ["가동 개시"],
                    "does_not_establish": ["수율 안정", "고객 인증 완료"],
                    "independence": "non_independent",
                    "confidence": "medium",
                }
            ],
        )
        self.write_source_ledger(
            outputs / "source_ledger.csv",
            [
                {
                    "source_id": "S001",
                    "url": "https://example.com/news",
                    "local_path": f"work/runs/{RUN_ID}/research/sources/S001.html",
                    "sha256": "abc123",
                    "ingest_status": "registered_local",
                    "blocked_reason": "",
                }
            ],
        )
        self.write_image_ledger(outputs / "image_ledger.csv", [])
        self.write_json(
            outputs / "claim_ledger.json",
            [
                {
                    "claim_id": "C001",
                    "claim_text": "신규 라인이 가동을 시작했습니다.",
                    "claim_type": "structural_fact",
                    "article_section": "최근 계기: 신규 라인이 가동에 들어갔습니다",
                    "article_anchor": {
                        "heading": "최근 계기: 신규 라인이 가동에 들어갔습니다",
                        "paragraph_id": "P001",
                    },
                    "claim_class": "issuer_claim",
                    "importance": "critical",
                    "evidence_ids": ["E001"],
                    "source_ids": ["S001"],
                    "source_quote_or_datapoint": "신규 라인이 가동을 시작했습니다",
                    "confidence": "medium",
                    "support_status": "conditionally_supported",
                    "needs_human_review": False,
                    "reason": "회사 발표만으로는 수율과 인증을 확인할 수 없습니다.",
                }
            ],
        )
        self.write_json(
            outputs / "paragraph_map.json",
            {
                "version": 1,
                "run_id": RUN_ID,
                "article_sha256": digest_of(ARTICLE_TEXT),
                "paragraphs": [
                    {
                        "paragraph_id": "P001",
                        "heading": "최근 계기: 신규 라인이 가동에 들어갔습니다",
                        "line_start": 7,
                        "line_end": 7,
                        "claim_ids": ["C001"],
                        "evidence_ids": ["E001"],
                    }
                ],
            },
        )
        self.write_json(
            outputs / "article_result.json",
            {
                "article_type": "industry",
                "article_sha256": digest_of(ARTICLE_TEXT),
                "analysis_modes": ["industry_structure"],
                "title": "반도체 공급 구조가 다시 시험받고 있습니다",
                "subtitle": "공동생산 구조가 방어선인지 집중 위험인지 묻습니다",
                "one_sentence_insight": "발표와 인증 사이의 간격이 방어력을 결정합니다.",
                "scorecard": None,
                "output_files": {
                    "article_md": f"outputs/{RUN_ID}/article.md",
                    "source_ledger_csv": f"outputs/{RUN_ID}/source_ledger.csv",
                    "image_ledger_csv": f"outputs/{RUN_ID}/image_ledger.csv",
                    "claim_ledger_json": f"outputs/{RUN_ID}/claim_ledger.json",
                    "preflight_result_json": f"outputs/{RUN_ID}/preflight_result.json",
                    "run_manifest_json": f"outputs/{RUN_ID}/run_manifest.json",
                    "research_appendix_md": f"outputs/{RUN_ID}/research_appendix.md",
                    "paragraph_map_json": f"outputs/{RUN_ID}/paragraph_map.json",
                },
                "critical_warnings": [],
                "checklist_status": "pass",
            },
        )

    def audit(self, root: Path):
        artifacts = build_run_artifacts(
            work_run_dir=f"work/runs/{RUN_ID}",
            outputs_dir=f"outputs/{RUN_ID}",
            article=f"outputs/{RUN_ID}/article.md",
            source_ledger=f"outputs/{RUN_ID}/source_ledger.csv",
            image_ledger=f"outputs/{RUN_ID}/image_ledger.csv",
            claim_ledger=f"outputs/{RUN_ID}/claim_ledger.json",
            article_result=f"outputs/{RUN_ID}/article_result.json",
        )
        return run_final_sync_audit(root=root, run_id=RUN_ID, artifacts=artifacts)

    def write_json(self, path: Path, payload: object) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )

    def write_jsonl(self, path: Path, rows: list[dict]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            "\n".join(json.dumps(row, ensure_ascii=False, sort_keys=True) for row in rows) + "\n",
            encoding="utf-8",
        )

    def write_source_ledger(self, path: Path, rows: list[dict]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        fields = [
            "source_id",
            "url",
            "local_path",
            "sha256",
            "ingest_status",
            "allowed_domain",
            "blocked_reason",
        ]
        with path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=fields)
            writer.writeheader()
            writer.writerows(rows)

    def write_image_ledger(self, path: Path, rows: list[dict]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        fields = [
            "image_path",
            "source_id",
            "image_hash",
            "visual_question",
            "supported_claim_ids",
            "why_prose_is_insufficient",
            "article_anchor_before",
            "article_anchor_after",
            "keep_or_drop",
        ]
        with path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=fields)
            writer.writeheader()
            writer.writerows(rows)

    def read_json(self, path: Path):
        return json.loads(path.read_text(encoding="utf-8"))

    def read_json_lines(self, path: Path) -> list[dict]:
        return [
            json.loads(line)
            for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]

    def test_clean_run_passes(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            self.build_clean_run(root)
            result = self.audit(root)
            self.assertTrue(result.passed, result.errors)
            self.assertEqual(result.payload["status"], "pass")

    def test_removing_a_used_source_is_caught(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            self.build_clean_run(root)
            self.write_source_ledger(root / "outputs" / RUN_ID / "source_ledger.csv", [])
            result = self.audit(root)
            self.assertFalse(result.passed)
            self.assertTrue(
                any("unknown source_id" in error for error in result.errors), result.errors
            )

    def test_wrong_quote_locator_is_caught(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            self.build_clean_run(root)
            path = root / "work" / "runs" / RUN_ID / "evidence" / "evidence_cards.jsonl"
            card = self.read_json_lines(path)[0]
            card["exact_quote"] = "원문에 존재하지 않는 문장입니다"
            self.write_jsonl(path, [card])
            result = self.audit(root)
            self.assertFalse(result.passed)
            self.assertTrue(
                any("was not found in extracted text" in error for error in result.errors),
                result.errors,
            )

    def test_wrong_pdf_page_locator_is_caught(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            self.build_clean_run(root)
            work = root / "work" / "runs" / RUN_ID
            page_text = work / "research" / "pdf_text" / "S001_page_001.txt"
            page_text.parent.mkdir(parents=True, exist_ok=True)
            page_text.write_text(SOURCE_TEXT, encoding="utf-8")
            self.write_jsonl(
                work / "research" / "pdf_index.jsonl",
                [
                    {
                        "source_id": "S001",
                        "page": 1,
                        "text_path": f"work/runs/{RUN_ID}/research/pdf_text/S001_page_001.txt",
                        "text_status": "ok",
                    }
                ],
            )
            evidence_path = work / "evidence" / "evidence_cards.jsonl"
            evidence = self.read_json_lines(evidence_path)
            evidence[0]["locator"]["page"] = 2
            self.write_jsonl(evidence_path, evidence)

            result = self.audit(root)

            self.assertFalse(result.passed)
            self.assertTrue(
                any("locator page 2 is not indexed" in error for error in result.errors),
                result.errors,
            )

    def test_stale_ledger_anchor_after_heading_change_is_caught(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            self.build_clean_run(root)
            changed = ARTICLE_TEXT.replace(
                "## 최근 계기: 신규 라인이 가동에 들어갔습니다",
                "## 최근 계기: 신규 라인이 시험 가동에 들어갔습니다",
            )
            (root / "outputs" / RUN_ID / "article.md").write_text(changed, encoding="utf-8")
            result_path = root / "outputs" / RUN_ID / "article_result.json"
            payload = self.read_json(result_path)
            payload["article_sha256"] = digest_of(changed)
            self.write_json(result_path, payload)
            result = self.audit(root)
            self.assertFalse(result.passed)
            self.assertTrue(
                any("verbatim heading" in error for error in result.errors), result.errors
            )

    def test_blocked_source_marked_supported_is_caught(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            self.build_clean_run(root)
            self.write_source_ledger(
                root / "outputs" / RUN_ID / "source_ledger.csv",
                [
                    {
                        "source_id": "S001",
                        "url": "https://example.com/news",
                        "local_path": "",
                        "sha256": "",
                        "ingest_status": "blocked",
                        "blocked_reason": "HTTP 403",
                    }
                ],
            )
            claim_path = root / "outputs" / RUN_ID / "claim_ledger.json"
            claims = self.read_json(claim_path)
            claims[0]["support_status"] = "supported"
            claims[0]["needs_human_review"] = False
            self.write_json(claim_path, claims)
            result = self.audit(root)
            self.assertFalse(result.passed)
            self.assertTrue(
                any("unverified sources" in error for error in result.errors), result.errors
            )

    def test_remote_manual_verified_requires_human_review(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            self.build_clean_run(root)
            self.write_source_ledger(
                root / "outputs" / RUN_ID / "source_ledger.csv",
                [
                    {
                        "source_id": "S001",
                        "url": "https://example.com/news",
                        "local_path": "",
                        "sha256": "",
                        "ingest_status": "remote_manual_verified",
                        "blocked_reason": "",
                    }
                ],
            )
            result = self.audit(root)
            self.assertFalse(result.passed)
            self.assertTrue(
                any("needs_human_review is false" in error for error in result.errors),
                result.errors,
            )

    def test_nonexistent_image_path_is_caught(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            self.build_clean_run(root)
            self.write_image_ledger(
                root / "outputs" / RUN_ID / "image_ledger.csv",
                [
                    {
                        "image_path": f"work/runs/{RUN_ID}/images/selected/missing.png",
                        "source_id": "S001",
                        "image_hash": "",
                        "visual_question": "무엇이 다른가",
                        "supported_claim_ids": "C001",
                        "why_prose_is_insufficient": "구조를 문장으로 설명하기 어렵습니다",
                        "article_anchor_before": "최근 계기: 신규 라인이 가동에 들어갔습니다",
                        "article_anchor_after": "결론",
                        "keep_or_drop": "keep",
                    }
                ],
            )
            result = self.audit(root)
            self.assertFalse(result.passed)
            self.assertTrue(
                any("does not exist" in error for error in result.errors), result.errors
            )

    def prepare_kept_image(self, root: Path) -> str:
        image_rel = f"work/runs/{RUN_ID}/images/selected/diagram.png"
        image_path = root / image_rel
        image_path.parent.mkdir(parents=True, exist_ok=True)
        image_path.write_bytes(b"png-bytes")
        article_path = root / "outputs" / RUN_ID / "article.md"
        article_path.write_text(ARTICLE_TEXT + f"\n![도식]({image_rel})\n", encoding="utf-8")
        result_path = root / "outputs" / RUN_ID / "article_result.json"
        payload = self.read_json(result_path)
        payload["article_sha256"] = digest_of(article_path.read_text(encoding="utf-8"))
        self.write_json(result_path, payload)
        return image_rel

    def test_kept_image_missing_visual_question_is_caught(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            self.build_clean_run(root)
            image_rel = self.prepare_kept_image(root)
            self.write_image_ledger(
                root / "outputs" / RUN_ID / "image_ledger.csv",
                [
                    {
                        "image_path": image_rel,
                        "source_id": "S001",
                        "image_hash": "",
                        "visual_question": "",
                        "supported_claim_ids": "",
                        "why_prose_is_insufficient": "",
                        "article_anchor_before": "최근 계기",
                        "article_anchor_after": "결론",
                        "keep_or_drop": "keep",
                    }
                ],
            )
            result = self.audit(root)
            self.assertFalse(result.passed)
            joined = "\n".join(result.errors)
            self.assertIn("visual_question", joined)
            self.assertIn("supported_claim_ids", joined)

    def test_image_hash_mismatch_is_caught(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            self.build_clean_run(root)
            image_rel = self.prepare_kept_image(root)
            self.write_image_ledger(
                root / "outputs" / RUN_ID / "image_ledger.csv",
                [
                    {
                        "image_path": image_rel,
                        "source_id": "S001",
                        "image_hash": "0" * 64,
                        "visual_question": "무엇이 다른가",
                        "supported_claim_ids": "C001",
                        "why_prose_is_insufficient": "구조를 문장으로 설명하기 어렵습니다",
                        "article_anchor_before": "최근 계기",
                        "article_anchor_after": "결론",
                        "keep_or_drop": "keep",
                    }
                ],
            )
            result = self.audit(root)
            self.assertFalse(result.passed)
            self.assertTrue(
                any("hash mismatch" in error for error in result.errors), result.errors
            )

    def test_claim_referencing_unknown_evidence_is_caught(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            self.build_clean_run(root)
            path = root / "work" / "runs" / RUN_ID / "evidence" / "evidence_cards.jsonl"
            card = self.read_json_lines(path)[0]
            card["evidence_id"] = "E999"
            self.write_jsonl(path, [card])
            result = self.audit(root)
            self.assertFalse(result.passed)
            self.assertTrue(
                any("unknown evidence_id" in error for error in result.errors), result.errors
            )

    def test_duplicate_evidence_ids_are_caught_before_indexing(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            self.build_clean_run(root)
            path = root / "work" / "runs" / RUN_ID / "evidence" / "evidence_cards.jsonl"
            card = self.read_json_lines(path)[0]
            duplicate = dict(card)
            duplicate["claim_ids"] = ["C999"]
            self.write_jsonl(path, [card, duplicate])

            result = self.audit(root)

            self.assertFalse(result.passed)
            self.assertTrue(
                any("duplicate evidence_id: E001" in error for error in result.errors),
                result.errors,
            )

    def test_paragraph_map_from_another_run_is_caught(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            self.build_clean_run(root)
            path = root / "outputs" / RUN_ID / "paragraph_map.json"
            payload = self.read_json(path)
            payload["run_id"] = "other_run"
            self.write_json(path, payload)

            result = self.audit(root)

            self.assertFalse(result.passed)
            self.assertTrue(
                any("paragraph map run_id mismatch" in error for error in result.errors),
                result.errors,
            )

    def test_article_hash_mismatch_is_caught(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            self.build_clean_run(root)
            (root / "outputs" / RUN_ID / "article.md").write_text(
                ARTICLE_TEXT + "\n추가 문장입니다.\n", encoding="utf-8"
            )
            result = self.audit(root)
            self.assertFalse(result.passed)
            self.assertTrue(
                any("article_sha256 mismatch" in error for error in result.errors),
                result.errors,
            )

    def test_missing_article_hash_is_caught(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            self.build_clean_run(root)
            result_path = root / "outputs" / RUN_ID / "article_result.json"
            payload = self.read_json(result_path)
            payload.pop("article_sha256")
            self.write_json(result_path, payload)
            result = self.audit(root)
            self.assertFalse(result.passed)
            self.assertTrue(
                any("does not record article_sha256" in error for error in result.errors),
                result.errors,
            )

    def test_paragraph_map_out_of_range_is_caught(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            self.build_clean_run(root)
            map_path = root / "outputs" / RUN_ID / "paragraph_map.json"
            payload = self.read_json(map_path)
            payload["paragraphs"][0]["line_end"] = 9999
            self.write_json(map_path, payload)
            result = self.audit(root)
            self.assertFalse(result.passed)
            self.assertTrue(
                any("exceeds article length" in error for error in result.errors),
                result.errors,
            )

    def test_missing_evidence_cards_is_caught(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            self.build_clean_run(root)
            (root / "work" / "runs" / RUN_ID / "evidence" / "evidence_cards.jsonl").unlink()

            result = self.audit(root)

            self.assertFalse(result.passed)
            self.assertTrue(any("evidence cards" in error for error in result.errors), result.errors)

    def test_missing_research_appendix_is_caught(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            self.build_clean_run(root)
            (root / "outputs" / RUN_ID / "research_appendix.md").unlink()

            result = self.audit(root)

            self.assertFalse(result.passed)
            self.assertTrue(any("research appendix" in error for error in result.errors), result.errors)

    def test_ledger_older_than_frozen_article_is_caught(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            self.build_clean_run(root)
            article = root / "outputs" / RUN_ID / "article.md"
            newest_ledger_mtime = max(
                (root / "outputs" / RUN_ID / name).stat().st_mtime
                for name in (
                    "claim_ledger.json",
                    "source_ledger.csv",
                    "image_ledger.csv",
                    "paragraph_map.json",
                )
            )
            os.utime(article, (newest_ledger_mtime + 10, newest_ledger_mtime + 10))

            result = self.audit(root)

            self.assertFalse(result.passed)
            self.assertTrue(
                any("older than article.md" in error for error in result.errors), result.errors
            )

    def test_legacy_unknown_source_status_is_not_treated_as_verified(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            self.build_clean_run(root)
            self.write_source_ledger(
                root / "outputs" / RUN_ID / "source_ledger.csv",
                [
                    {
                        "source_id": "S001",
                        "url": "https://example.com/news",
                        "local_path": "",
                        "sha256": "",
                        "ingest_status": "network_disabled",
                        "blocked_reason": "legacy status",
                    }
                ],
            )
            claims_path = root / "outputs" / RUN_ID / "claim_ledger.json"
            claims = self.read_json(claims_path)
            claims[0]["support_status"] = "supported"
            claims[0]["needs_human_review"] = False
            self.write_json(claims_path, claims)

            result = self.audit(root)

            self.assertFalse(result.passed)
            self.assertTrue(
                any("relies only on unverified sources" in error for error in result.errors),
                result.errors,
            )

    def test_issuer_only_evidence_cannot_be_promoted_to_observed_fact(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            self.build_clean_run(root)
            claims_path = root / "outputs" / RUN_ID / "claim_ledger.json"
            claims = self.read_json(claims_path)
            claims[0]["claim_class"] = "observed_fact"
            claims[0]["support_status"] = "supported"
            claims[0]["needs_human_review"] = False
            self.write_json(claims_path, claims)

            result = self.audit(root)

            self.assertFalse(result.passed)
            self.assertTrue(
                any("issuer claim or non-independent" in error for error in result.errors),
                result.errors,
            )

    def test_absence_claim_without_search_log_fails_schema_audit(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            self.build_clean_run(root)
            evidence_path = root / "work" / "runs" / RUN_ID / "evidence" / "evidence_cards.jsonl"
            evidence = self.read_json_lines(evidence_path)
            evidence[0]["evidence_class"] = "absence_claim"
            evidence[0].pop("absence_search_log", None)
            self.write_jsonl(evidence_path, evidence)

            result = self.audit(root)

            self.assertFalse(result.passed)
            self.assertTrue(
                any("absence_search_log" in error for error in result.errors), result.errors
            )


if __name__ == "__main__":
    unittest.main()
