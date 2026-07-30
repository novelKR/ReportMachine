from __future__ import annotations

import csv
import json
import shutil
import tempfile
import unittest
from hashlib import sha256
from pathlib import Path
from types import SimpleNamespace

from reportmachine_runner.audit import _audit_image_captions
from reportmachine_runner.final_sync import (
    _artifact_hashes,
    validate_final_image_ledger,
    validate_image_audit_report,
)
from reportmachine_runner.phase_contracts import (
    build_run_artifacts,
    phase_input_contracts,
)
from reportmachine_runner.prompt import build_prompt
from reportmachine_runner.run_contract import build_run_contract
from reportmachine_runner.run_outputs import IMAGE_LEDGER_HEADER, write_empty_image_ledger
from reportmachine_runner.source_ingest_paths import build_source_ingest_paths
from reportmachine_runner.source_ledgers import RENDERED_IMAGES_HEADER


RUN_ID = "image_authority_run"


class ImageAuthorityTests(unittest.TestCase):
    def artifacts(self):
        return build_run_artifacts(
            work_run_dir=f"work/runs/{RUN_ID}",
            outputs_dir=f"outputs/{RUN_ID}",
            article=f"outputs/{RUN_ID}/article.md",
            source_ledger=f"outputs/{RUN_ID}/source_ledger.csv",
            image_ledger=f"outputs/{RUN_ID}/image_ledger.csv",
            claim_ledger=f"outputs/{RUN_ID}/claim_ledger.json",
            article_result=f"outputs/{RUN_ID}/article_result.json",
        )

    def copy_schema(self, root: Path) -> None:
        source = Path(__file__).resolve().parents[1] / "schemas" / "image_audit_report.schema.json"
        target = root / "schemas" / "image_audit_report.schema.json"
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)

    def write_json(self, path: Path, payload: object) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )

    def write_csv(self, path: Path, fields: list[str], rows: list[dict[str, str]]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=fields)
            writer.writeheader()
            writer.writerows(rows)

    def seed_image_authority(self, root: Path) -> tuple[object, str, dict[str, object]]:
        self.copy_schema(root)
        artifacts = self.artifacts()
        paths = build_source_ingest_paths(artifacts.work_run_dir)
        image_rel = f"work/runs/{RUN_ID}/images/rendered/deck_page_001.png"
        image_bytes = b"rendered-image-bytes"
        image_path = root / image_rel
        image_path.parent.mkdir(parents=True, exist_ok=True)
        image_path.write_bytes(image_bytes)
        image_hash = sha256(image_bytes).hexdigest()
        source_hash = "a" * 64
        self.write_csv(
            root / paths.rendered_images_ledger,
            RENDERED_IMAGES_HEADER,
            [
                {
                    "image_path": image_rel,
                    "source_doc": "inputs/deck.pdf",
                    "source_id": "deck",
                    "page": "1",
                    "source_hash": source_hash,
                    "image_hash": image_hash,
                    "render_dpi": "144",
                    "status": "ok",
                    "reason": "",
                }
            ],
        )
        report_image: dict[str, object] = {
            "image_path": image_rel,
            "source_doc": "inputs/deck.pdf",
            "source_id": "deck",
            "page_or_slide_number": 1,
            "source_hash": source_hash,
            "visual_question": "수요 구성은 어떻게 달라졌는가",
            "supported_claim_ids": ["C001"],
            "why_prose_is_insufficient": "구성 변화를 한눈에 비교해야 한다.",
            "actual_slide_title": "Demand mix",
            "detected_key_datapoints": ["enterprise 55%", "consumer 45%"],
            "caption_candidate": "기업 수요 비중이 더 크다.",
            "article_anchor_before": "수요 구성",
            "article_anchor_after": "공급 제약",
            "keep_or_drop": "keep",
            "drop_reason": "",
        }
        self.write_json(
            root / artifacts.image_audit_report,
            {"version": 1, "run_id": RUN_ID, "issues": [], "images": [report_image]},
        )
        final_row = {
            "image_path": image_rel,
            "source_doc": "inputs/deck.pdf",
            "source_id": "deck",
            "page_or_slide_number": "1",
            "source_hash": source_hash,
            "image_hash": image_hash,
            "inspected_status": "ok",
            "caption_basis": "audited rendered slide",
            "visual_question": "수요 구성은 어떻게 달라졌는가",
            "supported_claim_ids": '["C001"]',
            "why_prose_is_insufficient": "구성 변화를 한눈에 비교해야 한다.",
            "actual_slide_title": "Demand mix",
            "detected_key_datapoints": '["enterprise 55%","consumer 45%"]',
            # The frozen-article caption is deliberately not identical to the candidate.
            "caption": "본문 흐름에 맞춘 기업·소비자 수요 구성 설명.",
            "caption_candidate": "기업 수요 비중이 더 크다.",
            "article_anchor_before": "수요 구성",
            "article_anchor_after": "공급 제약",
            "keep_or_drop": "keep",
            "drop_reason": "",
        }
        self.write_csv(root / artifacts.image_ledger, IMAGE_LEDGER_HEADER, [final_row])
        return artifacts, image_rel, report_image

    def test_report_and_final_ledger_three_way_validation_allows_contextual_caption(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            artifacts, image_rel, _report_image = self.seed_image_authority(root)

            self.assertEqual(
                validate_image_audit_report(root=root, run_id=RUN_ID, artifacts=artifacts),
                [],
            )
            self.assertEqual(
                validate_final_image_ledger(root=root, run_id=RUN_ID, artifacts=artifacts),
                [],
            )
            hashes = _artifact_hashes(root, artifacts)
            self.assertEqual(
                hashes["image_audit_json"],
                sha256((root / artifacts.image_audit_report).read_bytes()).hexdigest(),
            )

    def test_duplicate_report_path_and_changed_candidate_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            artifacts, _image_rel, report_image = self.seed_image_authority(root)
            self.write_json(
                root / artifacts.image_audit_report,
                {
                    "version": 1,
                    "run_id": RUN_ID,
                    "issues": [],
                    "images": [report_image, dict(report_image)],
                },
            )

            report_errors = validate_image_audit_report(
                root=root, run_id=RUN_ID, artifacts=artifacts
            )
            self.assertTrue(any("duplicate image_path" in error for error in report_errors))

            self.write_json(
                root / artifacts.image_audit_report,
                {"version": 1, "run_id": RUN_ID, "issues": [], "images": [report_image]},
            )
            rows = []
            with (root / artifacts.image_ledger).open(newline="", encoding="utf-8") as handle:
                rows = list(csv.DictReader(handle))
            rows[0]["caption_candidate"] = "감사되지 않은 후보"
            self.write_csv(root / artifacts.image_ledger, IMAGE_LEDGER_HEADER, rows)

            final_errors = validate_final_image_ledger(
                root=root, run_id=RUN_ID, artifacts=artifacts
            )
            self.assertTrue(
                any(
                    "caption_candidate differs from image audit" in error
                    for error in final_errors
                ),
                final_errors,
            )
            self.assertFalse(any("caption differs" in error for error in final_errors))

    def test_report_must_decide_every_status_ok_rendered_image(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            artifacts, image_rel, _report_image = self.seed_image_authority(root)
            self.write_json(
                root / artifacts.image_audit_report,
                {"version": 1, "run_id": RUN_ID, "issues": [], "images": []},
            )

            errors = validate_image_audit_report(
                root=root, run_id=RUN_ID, artifacts=artifacts
            )

            self.assertTrue(
                any(
                    "missing a keep/drop decision" in error and image_rel in error
                    for error in errors
                ),
                errors,
            )

    def test_final_caption_cannot_self_support_a_new_number_via_caption_basis(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            artifacts, image_rel, _report_image = self.seed_image_authority(root)
            with (root / artifacts.image_ledger).open(newline="", encoding="utf-8") as handle:
                rows = list(csv.DictReader(handle))
            rows[0]["caption"] = "감사되지 않은 99% 수치"
            rows[0]["caption_basis"] = "99%"
            self.write_csv(root / artifacts.image_ledger, IMAGE_LEDGER_HEADER, rows)
            self.write_csv(
                root / artifacts.source_ledger,
                ["source_id", "url", "local_path", "sha256"],
                [{"source_id": "deck", "url": "", "local_path": "", "sha256": "a" * 64}],
            )
            article = f"![audited image]({image_rel})\n"
            article_path = root / artifacts.article
            article_path.parent.mkdir(parents=True, exist_ok=True)
            article_path.write_text(article, encoding="utf-8")

            errors = _audit_image_captions(
                root=root,
                article=article,
                image_ledger=artifacts.image_ledger,
                source_ledger=artifacts.source_ledger,
                pdf_index=build_source_ingest_paths(artifacts.work_run_dir).pdf_index,
            )

            self.assertTrue(any("99" in error for error in errors), errors)

    def test_no_image_run_ignores_legacy_candidate(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            artifacts = self.artifacts()
            self.copy_schema(root)
            write_empty_image_ledger(root, artifacts.image_ledger)
            legacy = root / artifacts.work_run_dir / "ledgers" / "image_ledger.candidate.json"
            legacy.parent.mkdir(parents=True, exist_ok=True)
            legacy.write_text("{not canonical and intentionally invalid", encoding="utf-8")

            self.assertEqual(
                validate_image_audit_report(root=root, run_id=RUN_ID, artifacts=artifacts),
                [],
            )
            self.assertEqual(
                validate_final_image_ledger(root=root, run_id=RUN_ID, artifacts=artifacts),
                [],
            )

    def test_finalizer_contract_uses_report_and_rendered_ledger_only(self) -> None:
        artifacts = self.artifacts()
        rendered = build_source_ingest_paths(artifacts.work_run_dir).rendered_images_ledger
        paths = {
            item.path
            for item in phase_input_contracts(
                "finalize",
                artifacts,
                role="finalizer",
                rendered_images_ledger=rendered,
                include_images=True,
            )
        }
        self.assertIn(artifacts.image_audit_report, paths)
        self.assertIn(rendered, paths)
        self.assertFalse(any("candidate" in path for path in paths))

    def test_no_source_ingest_contract_keeps_canonical_image_paths(self) -> None:
        artifacts = self.artifacts()
        ingest_paths = build_source_ingest_paths(artifacts.work_run_dir)
        context = SimpleNamespace(
            run_artifacts=artifacts,
            analysis_modes=("industry_structure",),
            depth="standard",
            mode="industry",
            run_id=RUN_ID,
            topic_path="inputs/topic.md",
            sources_path="inputs/sources.csv",
            work_run_dir=artifacts.work_run_dir,
            run_manifest_output=f"outputs/{RUN_ID}/run_manifest.json",
            earnings_delta_matrix_output=f"outputs/{RUN_ID}/earnings_delta_matrix.json",
            earnings_delta_matrix_md=f"outputs/{RUN_ID}/earnings_delta_matrix.md",
            source_ingest_enabled=False,
            source_ingest_paths=ingest_paths,
            max_image_inputs=20,
        )
        contract = build_run_contract(context)

        self.assertEqual(contract["image_audit_report"], artifacts.image_audit_report)
        self.assertEqual(contract["rendered_images_ledger"], ingest_paths.rendered_images_ledger)
        self.assertNotIn("image_ledger_candidate", contract)
        self.assertNotIn("source_ingest_manifest", contract)

    def test_app_native_prompt_names_only_canonical_image_authority(self) -> None:
        artifacts = self.artifacts()
        paths = build_source_ingest_paths(artifacts.work_run_dir)
        prompt = build_prompt(
            mode="industry",
            phase="finalize",
            run_id=RUN_ID,
            topic_path="inputs/topic.md",
            sources_path="inputs/sources.csv",
            work_run_dir=artifacts.work_run_dir,
            draft_output=artifacts.draft_article,
            image_audit_report=artifacts.image_audit_report,
            source_ingest_manifest=paths.manifest,
            source_candidate_ledger=paths.source_candidate_ledger,
            pdf_index=paths.pdf_index,
            rendered_images_ledger=paths.rendered_images_ledger,
            source_ingest_enabled=False,
            article_output=artifacts.article,
            source_ledger_output=artifacts.source_ledger,
            image_ledger_output=artifacts.image_ledger,
            claim_ledger_output=artifacts.claim_ledger,
            preflight_result_output=f"outputs/{RUN_ID}/preflight_result.json",
            run_manifest_output=f"outputs/{RUN_ID}/run_manifest.json",
            earnings_delta_matrix_output=f"outputs/{RUN_ID}/earnings_delta_matrix.json",
            earnings_delta_matrix_md=f"outputs/{RUN_ID}/earnings_delta_matrix.md",
            result_output=artifacts.article_result,
            app_native_fallback=True,
        )

        self.assertIn(artifacts.image_audit_report, prompt)
        self.assertIn(paths.rendered_images_ledger, prompt)
        self.assertNotIn("image_ledger.candidate", prompt)


if __name__ == "__main__":
    unittest.main()
