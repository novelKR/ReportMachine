from __future__ import annotations

import csv
import io
import json
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

from reportmachine_runner import cli
from reportmachine_runner.intake import SOURCE_DISCOVERY_REQUIRED_ID
from reportmachine_runner.source_ingest import SourceIngestResult, build_source_ingest_paths
from reportmachine_runner.summary import (
    SUMMARY_CLAIM_MAP_HEADER,
    SUMMARY_SCHEMA,
    build_summary_run_config,
    source_hashes,
)

try:
    from jsonschema import Draft202012Validator
except ModuleNotFoundError:
    Draft202012Validator = None


class CliHarness(unittest.TestCase):
    def run_cli(self, argv: list[str]) -> tuple[int, str]:
        stdout = io.StringIO()
        stderr = io.StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            try:
                status = cli.main(argv)
            except SystemExit as exc:
                status = exc.code if isinstance(exc.code, int) else 1
        return status, stdout.getvalue() + stderr.getvalue()

    def init_git_root(self, temp_dir: str) -> Path:
        root = Path(temp_dir) / "project-root"
        root.mkdir(parents=True)
        subprocess.run(
            ["git", "init"],
            cwd=root,
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        self.copy_schema(root, "schemas/claim_ledger.schema.json")
        self.copy_schema(root, "schemas/earnings_delta.schema.json")
        return root

    def write_default_outputs(
        self,
        root: Path,
        *,
        run_id: str = "test_run",
        missing: set[str] | None = None,
    ) -> None:
        output_dir = root / "outputs" / run_id
        output_dir.mkdir(parents=True, exist_ok=True)
        for name in [
            "article_result.json",
            "article.md",
            "source_ledger.csv",
            "image_ledger.csv",
            "claim_ledger.json",
        ]:
            if missing and name in missing:
                continue
            content = "[]\n" if name == "claim_ledger.json" else name
            (output_dir / name).write_text(content, encoding="utf-8")

    def write_valid_article_outputs(self, root: Path, *, run_id: str = "test_run") -> None:
        output_dir = root / "outputs" / run_id
        output_dir.mkdir(parents=True, exist_ok=True)
        (output_dir / "article.md").write_text("# 제목\n\n본문입니다.\n", encoding="utf-8")
        (output_dir / "source_ledger.csv").write_text(
            "source_id,url,local_path\ns1,https://example.com,inputs/source.md\n",
            encoding="utf-8",
        )
        (output_dir / "image_ledger.csv").write_text(
            "image_path,inspected_status\n,not_available\n",
            encoding="utf-8",
        )
        (output_dir / "claim_ledger.json").write_text("[]\n", encoding="utf-8")
        (output_dir / "article_result.json").write_text(
            json.dumps(
                {
                    "article_type": "industry",
                    "title": "제목",
                    "subtitle": "부제",
                    "one_sentence_insight": "핵심",
                    "scorecard": None,
                    "output_files": {
                        "article_md": f"outputs/{run_id}/article.md",
                        "source_ledger_csv": f"outputs/{run_id}/source_ledger.csv",
                        "image_ledger_csv": f"outputs/{run_id}/image_ledger.csv",
                        "claim_ledger_json": f"outputs/{run_id}/claim_ledger.json",
                        "preflight_result_json": f"outputs/{run_id}/preflight_result.json",
                        "run_manifest_json": f"outputs/{run_id}/run_manifest.json",
                    },
                    "critical_warnings": [],
                    "checklist_status": "pass",
                },
                ensure_ascii=False,
            )
            + "\n",
            encoding="utf-8",
        )

    def copy_schema(self, root: Path, rel: str) -> None:
        source = Path(__file__).resolve().parents[1] / rel
        target = root / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(source.read_text(encoding="utf-8"), encoding="utf-8")

    def minimal_earnings_delta_payload(self) -> dict[str, object]:
        return {
            "delta_table": [],
            "disappeared_highlights": [],
            "wording_shifts": [],
            "guidance_assumption_changes": [],
            "ir_frame": "",
            "counter_reading": "",
            "scorecard_candidate": {
                "total": 0,
                "headline": 0,
                "quality": 0,
                "leading_indicators": 0,
                "transparency": 0,
                "execution": 0,
                "summary": "No delta rows.",
            },
            "next_quarter_verification_points": [],
        }

    def write_valid_earnings_outputs(
        self,
        root: Path,
        *,
        run_id: str = "test_run",
        delta_payload: object | str | None = None,
    ) -> None:
        output_dir = root / "outputs" / run_id
        output_dir.mkdir(parents=True, exist_ok=True)
        (output_dir / "article.md").write_text("## 한눈에 보기\n\n본문입니다.\n", encoding="utf-8")
        (output_dir / "source_ledger.csv").write_text(
            "source_id,url,local_path\nsrc_001,https://example.com,inputs/source.md\n",
            encoding="utf-8",
        )
        (output_dir / "image_ledger.csv").write_text(
            "image_path,inspected_status,keep_or_drop\n,not_available,drop\n",
            encoding="utf-8",
        )
        (output_dir / "claim_ledger.json").write_text("[]\n", encoding="utf-8")
        scorecard = {
            "total": 0,
            "headline": 0,
            "quality": 0,
            "leading_indicators": 0,
            "transparency": 0,
            "execution": 0,
            "summary": "No scorecard rows.",
        }
        (output_dir / "article_result.json").write_text(
            json.dumps(
                {
                    "article_type": "earnings",
                    "title": "제목",
                    "subtitle": "부제",
                    "one_sentence_insight": "핵심",
                    "scorecard": scorecard,
                    "output_files": {
                        "article_md": f"outputs/{run_id}/article.md",
                        "source_ledger_csv": f"outputs/{run_id}/source_ledger.csv",
                        "image_ledger_csv": f"outputs/{run_id}/image_ledger.csv",
                        "claim_ledger_json": f"outputs/{run_id}/claim_ledger.json",
                        "preflight_result_json": f"outputs/{run_id}/preflight_result.json",
                        "run_manifest_json": f"outputs/{run_id}/run_manifest.json",
                        "earnings_delta_matrix_json": f"outputs/{run_id}/earnings_delta_matrix.json",
                        "earnings_delta_matrix_md": f"outputs/{run_id}/earnings_delta_matrix.md",
                    },
                    "critical_warnings": [],
                    "checklist_status": "pass",
                },
                ensure_ascii=False,
            )
            + "\n",
            encoding="utf-8",
        )
        if delta_payload is None:
            delta_payload = self.minimal_earnings_delta_payload()
        delta_text = delta_payload if isinstance(delta_payload, str) else json.dumps(delta_payload, ensure_ascii=False)
        (output_dir / "earnings_delta_matrix.json").write_text(delta_text + "\n", encoding="utf-8")
        (output_dir / "earnings_delta_matrix.md").write_text("# Earnings delta\n", encoding="utf-8")

    def write_config(self, root: Path, text: str, name: str = "reportmachine.toml") -> Path:
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        return path

    def write_complete_earnings_sources(self, root: Path) -> None:
        input_dir = root / "inputs"
        source_dir = root / "fixtures"
        input_dir.mkdir(parents=True, exist_ok=True)
        source_dir.mkdir(parents=True, exist_ok=True)
        rows = [
            ("cur_release", "current", "earnings_release"),
            ("cur_deck", "current", "investor_presentation"),
            ("prev_release", "previous", "earnings_release"),
            ("prev_deck", "previous", "investor_presentation"),
        ]
        for source_id, _period_role, _doc_kind in rows:
            (source_dir / f"{source_id}.txt").write_text(source_id, encoding="utf-8")
        with (input_dir / "sources.csv").open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(
                handle,
                fieldnames=[
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
                ],
            )
            writer.writeheader()
            for source_id, period_role, doc_kind in rows:
                writer.writerow(
                    {
                        "source_id": source_id,
                        "article_type": "earnings",
                        "company": "Example",
                        "period_role": period_role,
                        "period": "Q1",
                        "doc_kind": doc_kind,
                        "issuer": "Example",
                        "date": "2026-01-01",
                        "title": source_id,
                        "url": "",
                        "source_tier": "primary",
                        "required": "true",
                        "local_path": f"fixtures/{source_id}.txt",
                        "notes": "",
                    }
                )

    def write_summary_schema(self, root: Path) -> None:
        schema_source = Path(__file__).resolve().parents[1] / SUMMARY_SCHEMA
        target = root / SUMMARY_SCHEMA
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(schema_source.read_text(encoding="utf-8"), encoding="utf-8")

    def write_claim_map_with_row(self, root: Path, config) -> None:
        claim_map = root / config.paths.summary_claim_map
        claim_map.parent.mkdir(parents=True, exist_ok=True)
        with claim_map.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=SUMMARY_CLAIM_MAP_HEADER)
            writer.writeheader()
            writer.writerow(
                {
                    "summary_item_id": "s1",
                    "summary_text": "본문입니다.",
                    "claim_type": "statement",
                    "source_artifact": "article.md",
                    "source_path": config.sources.article,
                    "source_anchor": "# 제목",
                    "source_url": "",
                    "notes": "test row",
                }
            )

    def write_success_summary_result(
        self,
        root: Path,
        run_id: str = "test_run",
        *,
        hashes: dict[str, str] | None = None,
    ) -> None:
        config = build_summary_run_config(root=root, run_id=run_id)
        hashes = hashes or source_hashes(config)
        summary_dir = root / config.paths.summary_dir
        summary_dir.mkdir(parents=True, exist_ok=True)
        (summary_dir / "summary_a4.png").write_bytes(b"png")
        self.write_claim_map_with_row(root, config)
        (summary_dir / "summary_result.json").write_text(
            json.dumps(
                {
                    "summary_type": "a4_visual",
                    "source_article": config.sources.article,
                    "source_article_sha256": hashes["article_sha256"],
                    "source_result": config.sources.article_result,
                    "source_result_sha256": hashes["article_result_sha256"],
                    "image_generation_surface": "codex_internal_tool",
                    "output_files": {
                        "summary_a4_png": config.paths.summary_image,
                        "summary_prompt_md": config.paths.summary_prompt,
                        "summary_claim_map_csv": config.paths.summary_claim_map,
                        "summary_manifest_json": config.paths.summary_manifest,
                    },
                    "critical_warnings": [],
                    "checklist_status": "pass",
                }
            )
            + "\n",
            encoding="utf-8",
        )

    def assert_no_ingest_artifacts_in_prompt(self, prompt: str) -> None:
        self.assertNotIn("source_ingest_manifest.json", prompt)
        self.assertNotIn("source_ledger.candidate.csv", prompt)
        self.assertNotIn("pdf_index.jsonl", prompt)
        self.assertNotIn("rendered_images.csv", prompt)
        self.assertNotIn("When source preprocessing is enabled", prompt)
        self.assertNotIn("Use preprocessed source candidates", prompt)
        self.assertNotIn("Use PDF search chunks", prompt)
