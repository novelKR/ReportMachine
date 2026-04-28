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


class CliDryRunTests(unittest.TestCase):
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

    def test_dry_run_does_not_create_default_output_dirs_under_custom_project_root(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "missing-root"

            status, output = self.run_cli(
                [
                    "--mode",
                    "industry",
                    "--run-id",
                    "test_run",
                    "--project-root",
                    str(root),
                    "--dry-run",
                ]
            )

            self.assertEqual(status, 0)
            self.assertIn("cwd:", output)
            self.assertIn("run_id: test_run", output)
            self.assertIn("command:", output)
            self.assertIn("outputs/test_run/article_result.json", output)
            self.assertIn("outputs/test_run/article.md", output)
            self.assertIn('web_search="cached"', output)
            self.assertIn("prompt:", output)
            self.assertFalse(root.exists())
            self.assertFalse((root / "outputs").exists())

    def test_dry_run_does_not_create_custom_output_parent_dirs(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "missing-root"

            status, _ = self.run_cli(
                [
                    "--mode",
                    "industry",
                    "--run-id",
                    "test_run",
                    "--project-root",
                    str(root),
                    "--output",
                    "outputs/custom/article_result.json",
                    "--article-output",
                    "outputs/articles/article.md",
                    "--source-ledger-output",
                    "work/ledgers/source_ledger.csv",
                    "--image-ledger-output",
                    "work/images/image_ledger.csv",
                    "--dry-run",
                ]
            )

            self.assertEqual(status, 0)
            self.assertFalse(root.exists())
            for parent in [
                root / "outputs" / "custom",
                root / "outputs" / "articles",
                root / "work" / "ledgers",
                root / "work" / "images",
            ]:
                self.assertFalse(parent.exists())

    def test_smoke_test_does_not_create_output_dirs(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "missing-root"

            status, output = self.run_cli(
                [
                    "--mode",
                    "earnings",
                    "--run-id",
                    "test_run",
                    "--project-root",
                    str(root),
                    "--smoke-test",
                ]
            )

            self.assertEqual(status, 0)
            self.assertIn("smoke-test: command and prompt built without execution", output)
            self.assertIn("Use $earnings-article.", output)
            self.assertFalse(root.exists())

    def test_intake_smoke_test_does_not_write_run_local_inputs(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "missing-root"

            status, output = self.run_cli(
                [
                    "--mode",
                    "industry",
                    "--run-id",
                    "test_run",
                    "--project-root",
                    str(root),
                    "--intake-topic",
                    "AI data center power infrastructure",
                    "--smoke-test",
                ]
            )

            self.assertEqual(status, 0, output)
            self.assertIn("intake_topic: work/runs/test_run/intake/topic.md", output)
            self.assertFalse(root.exists())

    def test_summary_output_smoke_test_does_not_create_output_dirs(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "missing-root"

            status, output = self.run_cli(
                [
                    "--mode",
                    "industry",
                    "--run-id",
                    "test_run",
                    "--project-root",
                    str(root),
                    "--summary-output",
                    "--smoke-test",
                ]
            )

            self.assertEqual(status, 0, output)
            self.assertIn("summary_dir: outputs/test_run/summary", output)
            self.assertIn("summary_result: outputs/test_run/summary/summary_result.json", output)
            self.assertIn("summary_image: outputs/test_run/summary/summary_a4.png", output)
            self.assertIn("Use $summary-output.", output)
            self.assertIn("Codex internal image generation tools only", output)
            self.assertFalse(root.exists())

    def test_direct_script_no_config_smoke_test_does_not_require_site_packages(self) -> None:
        root = Path(__file__).resolve().parents[1]
        result = subprocess.run(
            [
                sys.executable,
                "-S",
                str(root / "scripts" / "run_article.py"),
                "--mode",
                "industry",
                "--run-id",
                "test_run",
                "--smoke-test",
            ],
            cwd=root,
            check=False,
            capture_output=True,
            text=True,
        )

        output = result.stdout + result.stderr
        self.assertEqual(result.returncode, 0, output)
        self.assertIn("smoke-test: command and prompt built without execution", output)
        self.assertIn("Use $industry-article.", output)

    def test_config_only_requires_toml_backend_when_config_is_used(self) -> None:
        real_import = __import__

        def block_toml_imports(
            name: str,
            globals: dict[str, object] | None = None,
            locals: dict[str, object] | None = None,
            fromlist: tuple[str, ...] = (),
            level: int = 0,
        ) -> object:
            if name in {"tomllib", "tomli"}:
                raise ModuleNotFoundError(f"No module named {name!r}")
            return real_import(name, globals, locals, fromlist, level)

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "project-root"
            root.mkdir()
            self.write_config(
                root,
                """
[profiles.default]
mode = "industry"
""".lstrip(),
            )

            with patch("builtins.__import__", side_effect=block_toml_imports):
                no_config_status, no_config_output = self.run_cli(
                    [
                        "--mode",
                        "industry",
                        "--project-root",
                        str(root),
                        "--smoke-test",
                    ]
                )
                config_status, config_output = self.run_cli(
                    [
                        "--project-root",
                        str(root),
                        "--config",
                        "reportmachine.toml",
                        "--smoke-test",
                    ]
                )

            self.assertEqual(no_config_status, 0, no_config_output)
            self.assertEqual(config_status, 2)
            self.assertIn("TOML config support requires Python 3.11+ or the tomli package", config_output)
            self.assertNotIn("Traceback", config_output)

    def test_config_profile_supplies_mode_paths_web_search_and_run_id_prefix(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "project-root"
            root.mkdir()
            self.write_config(
                root,
                """
[profiles.custom]
mode = "earnings"
topic = "inputs/custom-topic.md"
sources = "inputs/custom-sources.csv"
schema = "schemas/custom-schema.json"
web_search = "live"
run_id_prefix = "custom"
""".lstrip(),
            )

            status, output = self.run_cli(
                [
                    "--project-root",
                    str(root),
                    "--config",
                    "reportmachine.toml",
                    "--profile",
                    "custom",
                    "--smoke-test",
                ]
            )

            self.assertEqual(status, 0, output)
            self.assertIn("run_id: custom_", output)
            self.assertIn("Use $earnings-article.", output)
            self.assertIn('web_search="live"', output)
            self.assertIn("inputs/custom-topic.md", output)
            self.assertIn("inputs/custom-sources.csv", output)
            self.assertIn("schemas/custom-schema.json", output)

    def test_config_profile_loads_summary_style(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "project-root"
            root.mkdir()
            self.write_config(
                root,
                """
[profiles.default]
mode = "industry"

[profiles.default.summary]
style_name = "operator_brief"
style_prompt = "Operator board style."
negative_prompt = "No tiny text."
max_visual_claims = 3
require_source_footer = false
""".lstrip(),
            )

            status, output = self.run_cli(
                [
                    "--project-root",
                    str(root),
                    "--config",
                    "reportmachine.toml",
                    "--run-id",
                    "test_run",
                    "--summary-output",
                    "--smoke-test",
                ]
            )

            self.assertEqual(status, 0, output)
            self.assertIn("style_name: `operator_brief`", output)
            self.assertIn("Operator board style.", output)
            self.assertIn("No tiny text.", output)
            self.assertIn("max_visual_claims: `3`", output)
            self.assertIn("require_source_footer: `false`", output)

    def test_config_profile_summary_enabled_activates_summary_smoke_output(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "project-root"
            root.mkdir()
            self.write_config(
                root,
                """
[profiles.default]
mode = "industry"

[profiles.default.summary]
enabled = true
style_name = "operator_brief"
""".lstrip(),
            )

            status, output = self.run_cli(
                [
                    "--project-root",
                    str(root),
                    "--config",
                    "reportmachine.toml",
                    "--run-id",
                    "test_run",
                    "--smoke-test",
                ]
            )

            self.assertEqual(status, 0, output)
            self.assertIn("summary_dir: outputs/test_run/summary", output)
            self.assertIn("summary_result: outputs/test_run/summary/summary_result.json", output)
            self.assertIn("Use $summary-output.", output)
            self.assertIn("style_name: `operator_brief`", output)

    def test_no_summary_output_disables_preset_summary_enable(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "project-root"
            root.mkdir()
            self.write_config(
                root,
                """
[profiles.default]
mode = "industry"

[profiles.default.summary]
enabled = true
""".lstrip(),
            )

            status, output = self.run_cli(
                [
                    "--project-root",
                    str(root),
                    "--config",
                    "reportmachine.toml",
                    "--run-id",
                    "test_run",
                    "--no-summary-output",
                    "--smoke-test",
                ]
            )

            self.assertEqual(status, 0, output)
            self.assertNotIn("summary_dir:", output)
            self.assertNotIn("Use $summary-output.", output)

    def test_config_profile_rejects_unknown_summary_key(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "project-root"
            root.mkdir()
            self.write_config(
                root,
                """
[profiles.default]
mode = "industry"

[profiles.default.summary]
unknown = true
""".lstrip(),
            )

            status, output = self.run_cli(
                [
                    "--project-root",
                    str(root),
                    "--config",
                    "reportmachine.toml",
                    "--smoke-test",
                ]
            )

            self.assertEqual(status, 2)
            self.assertIn("profiles.default.summary has unknown key(s): unknown", output)

    def test_config_profile_can_disable_source_ingest_prompt_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "project-root"
            root.mkdir()
            self.write_config(
                root,
                """
[profiles.default]
mode = "industry"

[profiles.default.source_ingest]
enabled = false
""".lstrip(),
            )

            with (
                patch("reportmachine_runner.cli.run_source_ingest") as run_source_ingest,
                patch("reportmachine_runner.cli.run_command", return_value=7) as run_command,
            ):
                status, _ = self.run_cli(
                    [
                        "--project-root",
                        str(root),
                        "--config",
                        "reportmachine.toml",
                        "--run-id",
                        "test_run",
                    ]
                )

            self.assertEqual(status, 7)
            run_source_ingest.assert_not_called()
            prompt = run_command.call_args.args[0][-1]
            self.assertIn("Source preprocessing is `disabled`.", prompt)
            self.assertIn("Use `inputs/sources.csv` directly as the source inventory", prompt)
            self.assert_no_ingest_artifacts_in_prompt(prompt)

    def test_cli_values_override_config_profile_values(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "project-root"
            root.mkdir()
            self.write_config(
                root,
                """
[profiles.default]
mode = "industry"
topic = "inputs/config-topic.md"
sources = "inputs/config-sources.csv"
web_search = "cached"
""".lstrip(),
            )

            status, output = self.run_cli(
                [
                    "--project-root",
                    str(root),
                    "--config",
                    "reportmachine.toml",
                    "--mode",
                    "earnings",
                    "--web-search",
                    "disabled",
                    "--topic",
                    "inputs/override-topic.md",
                    "--sources",
                    "inputs/override-sources.csv",
                    "--run-id",
                    "override_run",
                    "--smoke-test",
                ]
            )

            self.assertEqual(status, 0, output)
            self.assertIn("run_id: override_run", output)
            self.assertIn("Use $earnings-article.", output)
            self.assertIn('web_search="disabled"', output)
            self.assertIn("inputs/override-topic.md", output)
            self.assertIn("inputs/override-sources.csv", output)
            self.assertNotIn("inputs/config-topic.md", output)

    def test_normal_run_creates_output_dirs_before_running_command(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "project-root"
            argv = [
                "--mode",
                "industry",
                "--run-id",
                "test_run",
                "--project-root",
                str(root),
                "--output",
                "outputs/custom/article_result.json",
                "--article-output",
                "outputs/articles/article.md",
                "--source-ledger-output",
                "work/ledgers/source_ledger.csv",
                "--image-ledger-output",
                "work/images/image_ledger.csv",
            ]

            with patch("reportmachine_runner.cli.run_command", return_value=7) as run_command:
                status, _ = self.run_cli(argv)

            self.assertEqual(status, 7)
            for parent in [
                root / "outputs" / "custom",
                root / "outputs" / "articles",
                root / "work" / "ledgers",
                root / "work" / "images",
                root / "work" / "runs" / "test_run" / "research",
                root / "work" / "runs" / "test_run" / "drafts",
                root / "work" / "runs" / "test_run" / "reviews",
            ]:
                self.assertTrue(parent.is_dir())
            run_command.assert_called_once()

    def test_prepare_only_with_intake_writes_run_local_inputs_and_manifest_without_codex(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "project-root"

            status, output = self.run_cli(
                [
                    "--mode",
                    "industry",
                    "--run-id",
                    "test_run",
                    "--project-root",
                    str(root),
                    "--codex-bin",
                    "missing-codex-bin",
                    "--prepare-only",
                    "--intake-topic",
                    "AI 데이터센터 전력 인프라 병목과 전력기기 공급망",
                    "--intake-core-question",
                    "전력기기 공급망이 왜 병목이 되는가?",
                    "--intake-recent-trigger",
                    "최근 데이터센터 전력 수요 전망",
                    "--intake-source-note",
                    "IEA and official utility planning materials",
                    "--intake-output-notes",
                    "full report",
                ]
            )

            self.assertEqual(status, 0, output)
            topic = root / "work" / "runs" / "test_run" / "intake" / "topic.md"
            sources = root / "work" / "runs" / "test_run" / "intake" / "sources.csv"
            manifest = root / "work" / "runs" / "test_run" / "prepare_manifest.json"
            source_candidate = root / "work" / "runs" / "test_run" / "ledgers" / "source_ledger.candidate.csv"
            self.assertIn("AI 데이터센터", topic.read_text(encoding="utf-8"))
            self.assertIn(SOURCE_DISCOVERY_REQUIRED_ID, sources.read_text(encoding="utf-8"))
            self.assertIn(SOURCE_DISCOVERY_REQUIRED_ID, source_candidate.read_text(encoding="utf-8"))
            manifest_payload = json.loads(manifest.read_text(encoding="utf-8"))
            self.assertEqual(manifest_payload["execution"]["codex_command"][0], "missing-codex-bin")
            self.assertEqual(manifest_payload["paths"]["topic"], "work/runs/test_run/intake/topic.md")
            self.assertEqual(
                manifest_payload["paths"]["result_output"],
                "outputs/test_run/article_result.json",
            )
            manifest_prompt = manifest_payload["prompt"]
            self.assertIn(
                "write that schema-valid JSON directly to `outputs/test_run/article_result.json`",
                manifest_prompt,
            )
            self.assertIn("App-native fallback flow does not run Codex CLI `-o`", manifest_prompt)
            self.assertNotIn(
                "Do not write `outputs/test_run/article_result.json` directly",
                manifest_prompt,
            )
            command_prompt = manifest_payload["execution"]["codex_command"][-1]
            self.assertIn(
                "Do not write `outputs/test_run/article_result.json` directly",
                command_prompt,
            )
            self.assertIn("Codex CLI `-o` saves the final assistant message", command_prompt)
            self.assertIn("prepare-only", output)
            self.assertFalse((root / "outputs" / "latest").exists())

    def test_prepare_only_with_explicit_run_local_inputs_does_not_require_codex(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "project-root"
            intake = root / "work" / "runs" / "test_run" / "intake"
            intake.mkdir(parents=True)
            (intake / "topic.md").write_text("## Topic\n\nExample\n", encoding="utf-8")
            (intake / "sources.csv").write_text(
                "source_id,article_type,company,period_role,period,doc_kind,issuer,date,title,url,source_tier,required,local_path,notes\n",
                encoding="utf-8",
            )

            status, output = self.run_cli(
                [
                    "--mode",
                    "industry",
                    "--run-id",
                    "test_run",
                    "--project-root",
                    str(root),
                    "--codex-bin",
                    "missing-codex-bin",
                    "--prepare-only",
                    "--topic",
                    "work/runs/test_run/intake/topic.md",
                    "--sources",
                    "work/runs/test_run/intake/sources.csv",
                ]
            )

            self.assertEqual(status, 0, output)
            manifest = json.loads(
                (root / "work" / "runs" / "test_run" / "prepare_manifest.json").read_text(
                    encoding="utf-8"
                )
            )
            self.assertEqual(manifest["paths"]["topic"], "work/runs/test_run/intake/topic.md")
            self.assertEqual(manifest["execution"]["codex_command"][0], "missing-codex-bin")

    def test_command_uses_codex_root_schema_run_outputs_and_live_search(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "project-root"
            self.write_complete_earnings_sources(root)
            draft = root / "work" / "runs" / "test_run" / "drafts" / "draft_article.md"
            draft.parent.mkdir(parents=True, exist_ok=True)
            draft.write_text("draft", encoding="utf-8")
            argv = [
                "--mode",
                "earnings",
                "--phase",
                "finalize",
                "--run-id",
                "test_run",
                "--project-root",
                str(root),
            ]

            with patch("reportmachine_runner.cli.run_command", return_value=7) as run_command:
                status, _ = self.run_cli(argv)

            self.assertEqual(status, 7)
            command = run_command.call_args.args[0]
            self.assertEqual(command[0:4], ["codex", "exec", "-C", str(root.resolve())])
            self.assertIn("--output-schema", command)
            self.assertIn("schemas/article_result.schema.json", command)
            self.assertIn("-o", command)
            self.assertIn("outputs/test_run/article_result.json", command)
            self.assertIn('project_root_markers=[".git",".article-root"]', command)
            self.assertIn('web_search="live"', command)
            self.assertNotIn("--full-auto", command)
            self.assertNotIn("sandbox_workspace_write.network_access=true", command)
            prompt = command[-1]
            self.assertIn("Run id: `test_run`.", prompt)
            self.assertIn("outputs/test_run/article.md", prompt)
            self.assertIn("Do not write `outputs/test_run/article_result.json` directly", prompt)
            self.assertIn("non-publishable failure note", prompt)

    def test_allow_shell_network_is_explicit_opt_in(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "project-root"
            argv = [
                "--mode",
                "industry",
                "--run-id",
                "test_run",
                "--project-root",
                str(root),
                "--allow-shell-network",
            ]

            with patch("reportmachine_runner.cli.run_command", return_value=7) as run_command:
                status, _ = self.run_cli(argv)

            self.assertEqual(status, 7)
            command = run_command.call_args.args[0]
            self.assertIn("sandbox_workspace_write.network_access=true", command)

    def test_no_source_ingest_omits_preprocessed_artifact_prompt_references(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "project-root"
            argv = [
                "--mode",
                "industry",
                "--run-id",
                "test_run",
                "--project-root",
                str(root),
                "--no-source-ingest",
            ]

            with (
                patch("reportmachine_runner.cli.run_source_ingest") as run_source_ingest,
                patch("reportmachine_runner.cli.run_command", return_value=7) as run_command,
            ):
                status, _ = self.run_cli(argv)

            self.assertEqual(status, 7)
            run_source_ingest.assert_not_called()
            prompt = run_command.call_args.args[0][-1]
            self.assertIn("Source preprocessing is `disabled`.", prompt)
            self.assertIn("Use `inputs/sources.csv` directly as the source inventory", prompt)
            self.assert_no_ingest_artifacts_in_prompt(prompt)

    def test_rendered_ingest_images_are_passed_to_codex_image_input(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "project-root"
            self.write_complete_earnings_sources(root)
            argv = [
                "--mode",
                "earnings",
                "--run-id",
                "test_run",
                "--project-root",
                str(root),
            ]
            image_one = "work/runs/test_run/images/rendered/deck_page_001.png"
            image_two = "work/runs/test_run/images/rendered/deck_page_002.png"
            ingest_result = SourceIngestResult(
                paths=build_source_ingest_paths("work/runs/test_run"),
                image_inputs=(image_one, image_two),
                manifest={},
            )

            with (
                patch("reportmachine_runner.cli.run_source_ingest", return_value=ingest_result),
                patch("reportmachine_runner.cli.run_command", return_value=7) as run_command,
            ):
                status, _ = self.run_cli(argv)

            self.assertEqual(status, 7)
            command = run_command.call_args.args[0]
            prompt = command[-1]
            image_index = command.index("--image")
            self.assertEqual(command[image_index:], ["--image", image_one, image_two, "--", prompt])
            self.assertNotIn(f"{image_one},{image_two}", command)

    def test_industry_defaults_to_cached_web_search(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "project-root"
            argv = [
                "--mode",
                "industry",
                "--run-id",
                "test_run",
                "--project-root",
                str(root),
            ]

            with patch("reportmachine_runner.cli.run_command", return_value=7) as run_command:
                status, _ = self.run_cli(argv)

            self.assertEqual(status, 7)
            command = run_command.call_args.args[0]
            self.assertIn('web_search="cached"', command)
            self.assertNotIn('web_search="live"', command)

    def test_web_search_can_be_disabled_explicitly(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "project-root"
            self.write_complete_earnings_sources(root)
            argv = [
                "--mode",
                "earnings",
                "--run-id",
                "test_run",
                "--project-root",
                str(root),
                "--web-search",
                "disabled",
            ]

            with patch("reportmachine_runner.cli.run_command", return_value=7) as run_command:
                status, _ = self.run_cli(argv)

            self.assertEqual(status, 7)
            command = run_command.call_args.args[0]
            self.assertIn('web_search="disabled"', command)
            self.assertNotIn('web_search="live"', command)

    def test_output_path_must_stay_under_project_root(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "project-root"
            cases = [
                (str(Path(temp_dir) / "outside.json"), "Absolute paths are not allowed"),
                ("C:\\temp\\outside.json", "Windows drive paths are not allowed"),
                ("../outside.json", "Path escapes project root"),
            ]
            for value, expected in cases:
                with self.subTest(value=value):
                    status, output = self.run_cli(
                        [
                            "--mode",
                            "industry",
                            "--run-id",
                            "test_run",
                            "--project-root",
                            str(root),
                            "--output",
                            value,
                            "--dry-run",
                        ]
                    )

                    self.assertEqual(status, 2)
                    self.assertIn(expected, output)

    def test_external_artifact_root_must_stay_outside_project_root(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "project-root"
            root.mkdir()
            self.write_config(
                root,
                f"""
[profiles.default]
mode = "industry"

[profiles.default.artifacts]
external_root = "{(root / "artifacts").as_posix()}"
""".lstrip(),
            )

            status, output = self.run_cli(
                [
                    "--project-root",
                    str(root),
                    "--config",
                    "reportmachine.toml",
                    "--run-id",
                    "test_run",
                    "--smoke-test",
                ]
            )

            self.assertEqual(status, 2)
            self.assertIn("External artifact root must be outside the project root", output)

    @unittest.skipIf(Draft202012Validator is None, "jsonschema is not installed")
    def test_article_result_schema_rejects_successful_earnings_without_scorecard(self) -> None:
        schema_path = (
            Path(__file__).resolve().parents[1]
            / "schemas"
            / "article_result.schema.json"
        )
        schema = json.loads(schema_path.read_text(encoding="utf-8"))
        validator = Draft202012Validator(schema)
        for checklist_status in ["pass", "pass_with_warnings"]:
            with self.subTest(checklist_status=checklist_status):
                payload = {
                    "article_type": "earnings",
                    "title": "Example",
                    "subtitle": "Example",
                    "one_sentence_insight": "Example",
                    "scorecard": None,
                    "output_files": {
                        "article_md": "outputs/test_run/article.md",
                        "source_ledger_csv": "outputs/test_run/source_ledger.csv",
                        "image_ledger_csv": "outputs/test_run/image_ledger.csv",
                        "claim_ledger_json": "outputs/test_run/claim_ledger.json",
                        "preflight_result_json": "outputs/test_run/preflight_result.json",
                        "run_manifest_json": "outputs/test_run/run_manifest.json",
                    },
                    "critical_warnings": [],
                    "checklist_status": checklist_status,
                }

                errors = list(validator.iter_errors(payload))
                self.assertTrue(errors)

    def test_successful_run_mirrors_outputs_to_latest(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = self.init_git_root(temp_dir)
            latest_dir = root / "outputs" / "latest"
            latest_dir.mkdir(parents=True)
            (latest_dir / "stale_extra.txt").write_text("stale", encoding="utf-8")

            def fake_run(_command: list[str], *, cwd: Path) -> int:
                self.assertEqual(cwd, root.resolve())
                self.write_default_outputs(root)
                return 0

            with patch("reportmachine_runner.cli.run_command", side_effect=fake_run):
                status, _ = self.run_cli(
                    [
                        "--mode",
                        "industry",
                        "--run-id",
                        "test_run",
                        "--project-root",
                        str(root),
                    ]
                )

            self.assertEqual(status, 0)
            for name in [
                "article_result.json",
                "article.md",
                "source_ledger.csv",
                "image_ledger.csv",
            ]:
                self.assertEqual((root / "outputs" / "latest" / name).read_text(encoding="utf-8"), name)
            self.assertFalse((root / "outputs" / "latest" / "stale_extra.txt").exists())

    def test_successful_run_allows_configured_custom_output_files(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = self.init_git_root(temp_dir)
            custom_outputs = [
                "outputs/custom/article_result.json",
                "outputs/articles/article.md",
                "work/ledgers/source_ledger.csv",
                "work/images/image_ledger.csv",
            ]

            def fake_run(_command: list[str], *, cwd: Path) -> int:
                self.assertEqual(cwd, root.resolve())
                for rel in custom_outputs:
                    path = root / rel
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_text(rel, encoding="utf-8")
                claim_path = root / "outputs" / "test_run" / "claim_ledger.json"
                claim_path.parent.mkdir(parents=True, exist_ok=True)
                claim_path.write_text("[]\n", encoding="utf-8")
                return 0

            with patch("reportmachine_runner.cli.run_command", side_effect=fake_run):
                status, output = self.run_cli(
                    [
                        "--mode",
                        "industry",
                        "--run-id",
                        "test_run",
                        "--project-root",
                        str(root),
                        "--output",
                        custom_outputs[0],
                        "--article-output",
                        custom_outputs[1],
                        "--source-ledger-output",
                        custom_outputs[2],
                        "--image-ledger-output",
                        custom_outputs[3],
                    ]
                )

            self.assertEqual(status, 0, output)
            self.assertEqual(
                (root / "outputs" / "latest" / "article_result.json").read_text(encoding="utf-8"),
                custom_outputs[0],
            )
            self.assertEqual(
                (root / "outputs" / "latest" / "article.md").read_text(encoding="utf-8"),
                custom_outputs[1],
            )
            self.assertEqual(
                (root / "outputs" / "latest" / "source_ledger.csv").read_text(encoding="utf-8"),
                custom_outputs[2],
            )
            self.assertEqual(
                (root / "outputs" / "latest" / "image_ledger.csv").read_text(encoding="utf-8"),
                custom_outputs[3],
            )

    def test_missing_configured_output_fails_without_refreshing_latest(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = self.init_git_root(temp_dir)
            latest_dir = root / "outputs" / "latest"
            latest_dir.mkdir(parents=True)
            (latest_dir / "image_ledger.csv").write_text("stale-image-ledger", encoding="utf-8")

            def fake_run(_command: list[str], *, cwd: Path) -> int:
                self.assertEqual(cwd, root.resolve())
                self.write_default_outputs(root, missing={"image_ledger.csv"})
                return 0

            with patch("reportmachine_runner.cli.run_command", side_effect=fake_run):
                status, output = self.run_cli(
                    [
                        "--mode",
                        "industry",
                        "--run-id",
                        "test_run",
                        "--project-root",
                        str(root),
                    ]
                )

            self.assertEqual(status, 2)
            self.assertIn("missing configured output: outputs/test_run/image_ledger.csv", output)
            self.assertEqual(
                (latest_dir / "image_ledger.csv").read_text(encoding="utf-8"),
                "stale-image-ledger",
            )
            self.assertFalse((latest_dir / "article.md").exists())

    def test_earnings_run_rejects_malformed_delta_json_without_refreshing_latest(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = self.init_git_root(temp_dir)
            self.write_complete_earnings_sources(root)
            self.copy_schema(root, "schemas/claim_ledger.schema.json")
            self.copy_schema(root, "schemas/earnings_delta.schema.json")
            latest_dir = root / "outputs" / "latest"
            latest_dir.mkdir(parents=True)
            (latest_dir / "article.md").write_text("stale-article", encoding="utf-8")

            def fake_run(_command: list[str], *, cwd: Path) -> int:
                self.assertEqual(cwd, root.resolve())
                self.write_valid_earnings_outputs(root, delta_payload="{not-json")
                return 0

            with patch("reportmachine_runner.cli.run_command", side_effect=fake_run):
                status, output = self.run_cli(
                    [
                        "--mode",
                        "earnings",
                        "--run-id",
                        "test_run",
                        "--project-root",
                        str(root),
                    ]
                )

            self.assertEqual(status, 2)
            self.assertIn("Post-run validation failed", output)
            self.assertIn("invalid earnings delta JSON", output)
            self.assertEqual(
                (latest_dir / "article.md").read_text(encoding="utf-8"),
                "stale-article",
            )
            self.assertFalse((latest_dir / "earnings_delta_matrix.json").exists())

    def test_earnings_run_rejects_schema_invalid_delta_json_without_export(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = self.init_git_root(temp_dir)
            external_root = Path(temp_dir) / "artifacts"
            self.write_complete_earnings_sources(root)
            self.copy_schema(root, "schemas/claim_ledger.schema.json")
            self.copy_schema(root, "schemas/earnings_delta.schema.json")
            self.write_config(
                root,
                f"""
[profiles.default]
mode = "earnings"

[profiles.default.artifacts]
external_root = "{external_root.as_posix()}"
""".lstrip(),
            )

            def fake_run(_command: list[str], *, cwd: Path) -> int:
                self.assertEqual(cwd, root.resolve())
                self.write_valid_earnings_outputs(root, delta_payload={})
                return 0

            with patch("reportmachine_runner.cli.run_command", side_effect=fake_run):
                status, output = self.run_cli(
                    [
                        "--project-root",
                        str(root),
                        "--config",
                        "reportmachine.toml",
                        "--run-id",
                        "test_run",
                    ]
                )

            self.assertEqual(status, 2)
            self.assertIn("earnings delta schema validation failed", output)
            self.assertFalse((root / "outputs" / "latest").exists())
            self.assertFalse((external_root / "latest").exists())

    def test_earnings_run_accepts_schema_valid_delta_json(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = self.init_git_root(temp_dir)
            self.write_complete_earnings_sources(root)
            self.copy_schema(root, "schemas/claim_ledger.schema.json")
            self.copy_schema(root, "schemas/earnings_delta.schema.json")
            delta_payload = self.minimal_earnings_delta_payload()

            def fake_run(_command: list[str], *, cwd: Path) -> int:
                self.assertEqual(cwd, root.resolve())
                self.write_valid_earnings_outputs(root, delta_payload=delta_payload)
                return 0

            with patch("reportmachine_runner.cli.run_command", side_effect=fake_run):
                status, output = self.run_cli(
                    [
                        "--mode",
                        "earnings",
                        "--run-id",
                        "test_run",
                        "--project-root",
                        str(root),
                    ]
                )

            self.assertEqual(status, 0, output)
            latest_delta = root / "outputs" / "latest" / "earnings_delta_matrix.json"
            self.assertEqual(
                json.loads(latest_delta.read_text(encoding="utf-8")),
                delta_payload,
            )

    def test_post_run_validator_rejects_changed_existing_untracked_file(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = self.init_git_root(temp_dir)
            scratch = root / "scratch.md"
            scratch.write_text("before", encoding="utf-8")

            def fake_run(_command: list[str], *, cwd: Path) -> int:
                self.assertEqual(cwd, root.resolve())
                scratch.write_text("after", encoding="utf-8")
                self.write_default_outputs(root)
                return 0

            with patch("reportmachine_runner.cli.run_command", side_effect=fake_run):
                status, output = self.run_cli(
                    [
                        "--mode",
                        "industry",
                        "--run-id",
                        "test_run",
                        "--project-root",
                        str(root),
                    ]
                )

            self.assertEqual(status, 2)
            self.assertIn("protected untracked file changed: scratch.md", output)
            self.assertFalse((root / "outputs" / "latest").exists())

    def test_existing_untracked_allowed_artifacts_can_be_updated(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = self.init_git_root(temp_dir)
            configured_output = root / "outputs" / "custom" / "article_result.json"
            configured_output.parent.mkdir(parents=True)
            configured_output.write_text("before-configured-output", encoding="utf-8")
            run_scoped_draft = (
                root / "work" / "runs" / "test_run" / "drafts" / "draft_article.md"
            )
            run_scoped_draft.parent.mkdir(parents=True)
            run_scoped_draft.write_text("before-draft", encoding="utf-8")

            def fake_run(_command: list[str], *, cwd: Path) -> int:
                self.assertEqual(cwd, root.resolve())
                configured_output.write_text("after-configured-output", encoding="utf-8")
                run_scoped_draft.write_text("after-draft", encoding="utf-8")
                for rel in [
                    "outputs/test_run/article.md",
                    "outputs/test_run/source_ledger.csv",
                    "outputs/test_run/image_ledger.csv",
                    "outputs/test_run/claim_ledger.json",
                ]:
                    path = root / rel
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_text("[]\n" if path.suffix == ".json" else path.name, encoding="utf-8")
                return 0

            with patch("reportmachine_runner.cli.run_command", side_effect=fake_run):
                status, output = self.run_cli(
                    [
                        "--mode",
                        "industry",
                        "--run-id",
                        "test_run",
                        "--project-root",
                        str(root),
                        "--output",
                        "outputs/custom/article_result.json",
                    ]
                )

            self.assertEqual(status, 0, output)
            self.assertEqual(configured_output.read_text(encoding="utf-8"), "after-configured-output")
            self.assertEqual(run_scoped_draft.read_text(encoding="utf-8"), "after-draft")
            self.assertEqual(
                (root / "outputs" / "latest" / "article_result.json").read_text(encoding="utf-8"),
                "after-configured-output",
            )

    def test_successful_configured_external_export_omits_external_path_from_codex_prompt_and_cleans_cache(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = self.init_git_root(temp_dir)
            external_root = Path(temp_dir) / "artifacts"
            self.write_config(
                root,
                f"""
[profiles.default]
mode = "industry"

[profiles.default.artifacts]
external_root = "{external_root.as_posix()}"
cleanup_cache_on_success = true
preserve_cache_on_failure = true
""".lstrip(),
            )

            captured_command: list[str] = []

            def fake_run(command: list[str], *, cwd: Path) -> int:
                captured_command.extend(command)
                self.assertEqual(cwd, root.resolve())
                work_draft = root / "work" / "runs" / "test_run" / "drafts" / "draft_article.md"
                work_draft.parent.mkdir(parents=True, exist_ok=True)
                work_draft.write_text("draft", encoding="utf-8")
                self.write_default_outputs(root)
                return 0

            with patch("reportmachine_runner.cli.run_command", side_effect=fake_run):
                status, output = self.run_cli(
                    [
                        "--project-root",
                        str(root),
                        "--config",
                        "reportmachine.toml",
                        "--run-id",
                        "test_run",
                    ]
                )

            self.assertEqual(status, 0, output)
            self.assertNotIn(external_root.as_posix(), " ".join(captured_command))
            self.assertEqual(
                (external_root / "runs" / "test_run" / "article.md").read_text(encoding="utf-8"),
                "article.md",
            )
            self.assertTrue((external_root / "latest" / "reportmachine_manifest.json").is_file())
            self.assertFalse((root / "work" / "runs" / "test_run").exists())
            self.assertFalse((root / "outputs" / "test_run").exists())
            self.assertTrue((root / "outputs" / "latest" / "article.md").is_file())

    def test_successful_summary_run_refreshes_local_and_external_latest_summary(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = self.init_git_root(temp_dir)
            external_root = Path(temp_dir) / "artifacts"
            self.write_summary_schema(root)
            self.write_config(
                root,
                f"""
[profiles.default]
mode = "industry"

[profiles.default.source_ingest]
enabled = false

[profiles.default.artifacts]
external_root = "{external_root.as_posix()}"
""".lstrip(),
            )

            def fake_article_run(_command: list[str], *, cwd: Path) -> int:
                self.assertEqual(cwd, root.resolve())
                self.write_valid_article_outputs(root)
                return 0

            def fake_summary_run(_command: list[str], *, cwd: Path) -> int:
                config = build_summary_run_config(root=cwd, run_id="test_run")
                self.write_success_summary_result(cwd, hashes=source_hashes(config))
                return 0

            with (
                patch("reportmachine_runner.cli.run_command", side_effect=fake_article_run),
                patch("reportmachine_runner.summary.run_command", side_effect=fake_summary_run),
            ):
                status, output = self.run_cli(
                    [
                        "--project-root",
                        str(root),
                        "--config",
                        "reportmachine.toml",
                        "--run-id",
                        "test_run",
                        "--summary-output",
                    ]
                )

            self.assertEqual(status, 0, output)
            self.assertEqual(
                (root / "outputs/latest/summary/summary_a4.png").read_bytes(),
                b"png",
            )
            self.assertEqual(
                (external_root / "latest/summary/summary_a4.png").read_bytes(),
                b"png",
            )
            self.assertTrue(
                (external_root / "runs/test_run/summary/summary_result.json").is_file()
            )

    def test_external_export_failure_preserves_latest_and_success_cache(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = self.init_git_root(temp_dir)
            external_root = Path(temp_dir) / "artifacts"
            latest_dir = root / "outputs" / "latest"
            latest_dir.mkdir(parents=True)
            (latest_dir / "article.md").write_text("stale-article", encoding="utf-8")
            self.write_config(
                root,
                f"""
[profiles.default]
mode = "industry"

[profiles.default.artifacts]
external_root = "{external_root.as_posix()}"
cleanup_cache_on_success = true
""".lstrip(),
            )

            def fake_run(_command: list[str], *, cwd: Path) -> int:
                self.assertEqual(cwd, root.resolve())
                self.write_default_outputs(root)
                return 0

            with (
                patch("reportmachine_runner.cli.run_command", side_effect=fake_run),
                patch("reportmachine_runner.cli.export_final_artifacts", side_effect=OSError("disk full")),
            ):
                status, output = self.run_cli(
                    [
                        "--project-root",
                        str(root),
                        "--config",
                        "reportmachine.toml",
                        "--run-id",
                        "test_run",
                    ]
                )

            self.assertEqual(status, 2)
            self.assertIn("Final artifact export failed: disk full", output)
            self.assertEqual((latest_dir / "article.md").read_text(encoding="utf-8"), "stale-article")
            self.assertFalse((latest_dir / "article_result.json").exists())
            self.assertTrue((root / "work" / "runs" / "test_run").is_dir())
            self.assertTrue((root / "outputs" / "test_run").is_dir())

    def test_cli_defers_summary_latest_until_external_export_succeeds(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = self.init_git_root(temp_dir)
            external_root = Path(temp_dir) / "artifacts"
            latest_dir = root / "outputs" / "latest"
            latest_summary = latest_dir / "summary"
            latest_summary.mkdir(parents=True)
            (latest_dir / "article.md").write_text("stale-article", encoding="utf-8")
            (latest_summary / "summary_a4.png").write_bytes(b"stale-summary")
            self.write_summary_schema(root)
            self.write_config(
                root,
                f"""
[profiles.default]
mode = "industry"

[profiles.default.source_ingest]
enabled = false

[profiles.default.artifacts]
external_root = "{external_root.as_posix()}"
""".lstrip(),
            )

            def fake_article_run(_command: list[str], *, cwd: Path) -> int:
                self.assertEqual(cwd, root.resolve())
                self.write_valid_article_outputs(root)
                return 0

            def fake_summary_run(_command: list[str], *, cwd: Path) -> int:
                config = build_summary_run_config(root=cwd, run_id="test_run")
                self.write_success_summary_result(cwd, hashes=source_hashes(config))
                return 0

            with (
                patch("reportmachine_runner.cli.run_command", side_effect=fake_article_run),
                patch("reportmachine_runner.summary.run_command", side_effect=fake_summary_run),
                patch(
                    "reportmachine_runner.cli.export_final_artifacts",
                    side_effect=OSError("disk full"),
                ),
            ):
                status, output = self.run_cli(
                    [
                        "--project-root",
                        str(root),
                        "--config",
                        "reportmachine.toml",
                        "--run-id",
                        "test_run",
                        "--summary-output",
                    ]
                )

            self.assertEqual(status, 2)
            self.assertIn("Final artifact export failed: disk full", output)
            self.assertEqual(
                (latest_dir / "article.md").read_text(encoding="utf-8"),
                "stale-article",
            )
            self.assertEqual(
                (latest_summary / "summary_a4.png").read_bytes(),
                b"stale-summary",
            )
            self.assertFalse((latest_summary / "summary_result.json").exists())
            self.assertFalse((external_root / "latest").exists())

    def test_cli_summary_mutation_blocks_external_export_and_latest_mirror(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = self.init_git_root(temp_dir)
            external_root = Path(temp_dir) / "artifacts"
            self.write_summary_schema(root)
            self.write_config(
                root,
                f"""
[profiles.default]
mode = "industry"

[profiles.default.source_ingest]
enabled = false

[profiles.default.artifacts]
external_root = "{external_root.as_posix()}"
""".lstrip(),
            )

            def fake_article_run(_command: list[str], *, cwd: Path) -> int:
                self.assertEqual(cwd, root.resolve())
                self.write_valid_article_outputs(root)
                return 0

            def fake_summary_run(_command: list[str], *, cwd: Path) -> int:
                config = build_summary_run_config(root=cwd, run_id="test_run")
                hashes = source_hashes(config)
                (cwd / "outputs" / "test_run" / "article.md").write_text(
                    "# 제목\n\nsummary changed the article.\n",
                    encoding="utf-8",
                )
                self.write_success_summary_result(cwd, hashes=hashes)
                return 0

            with (
                patch("reportmachine_runner.cli.run_command", side_effect=fake_article_run),
                patch("reportmachine_runner.summary.run_command", side_effect=fake_summary_run),
            ):
                status, output = self.run_cli(
                    [
                        "--project-root",
                        str(root),
                        "--config",
                        "reportmachine.toml",
                        "--run-id",
                        "test_run",
                        "--summary-output",
                    ]
                )

            self.assertEqual(status, 2, output)
            self.assertFalse((external_root / "latest").exists())
            self.assertFalse(
                (external_root / "runs" / "test_run" / "reportmachine_manifest.json").exists()
            )
            self.assertFalse((external_root / "runs" / "test_run" / "article.md").exists())
            self.assertFalse((root / "outputs" / "latest").exists())

    def test_failed_configured_external_run_preserves_cache_and_writes_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = self.init_git_root(temp_dir)
            external_root = Path(temp_dir) / "artifacts"
            self.write_config(
                root,
                f"""
[profiles.default]
mode = "industry"

[profiles.default.artifacts]
external_root = "{external_root.as_posix()}"
preserve_cache_on_failure = true
""".lstrip(),
            )

            def fake_run(_command: list[str], *, cwd: Path) -> int:
                work_draft = cwd / "work" / "runs" / "test_run" / "drafts" / "draft_article.md"
                work_draft.parent.mkdir(parents=True, exist_ok=True)
                work_draft.write_text("partial draft", encoding="utf-8")
                return 7

            with patch("reportmachine_runner.cli.run_command", side_effect=fake_run):
                status, _ = self.run_cli(
                    [
                        "--project-root",
                        str(root),
                        "--config",
                        "reportmachine.toml",
                        "--run-id",
                        "test_run",
                    ]
                )

            self.assertEqual(status, 7)
            self.assertTrue((root / "work" / "runs" / "test_run").is_dir())
            self.assertEqual(
                (
                    external_root
                    / "runs"
                    / "test_run"
                    / "snapshot"
                    / "work_run"
                    / "drafts"
                    / "draft_article.md"
                ).read_text(encoding="utf-8"),
                "partial draft",
            )

    def test_post_run_validator_rejects_changed_or_deleted_existing_ignored_artifacts(self) -> None:
        cases = [
            ("changed", "work/drafts/legacy.md"),
            ("deleted", "outputs/old_run/article.md"),
        ]
        for action, rel in cases:
            with self.subTest(action=action, rel=rel):
                with tempfile.TemporaryDirectory() as temp_dir:
                    root = self.init_git_root(temp_dir)
                    (root / ".gitignore").write_text(
                        "work/**/*.md\noutputs/**/*.md\n",
                        encoding="utf-8",
                    )
                    artifact = root / rel
                    artifact.parent.mkdir(parents=True, exist_ok=True)
                    artifact.write_text("before", encoding="utf-8")

                    def fake_run(_command: list[str], *, cwd: Path) -> int:
                        self.assertEqual(cwd, root.resolve())
                        if action == "changed":
                            artifact.write_text("after", encoding="utf-8")
                        else:
                            artifact.unlink()
                        self.write_default_outputs(root)
                        return 0

                    with patch("reportmachine_runner.cli.run_command", side_effect=fake_run):
                        status, output = self.run_cli(
                            [
                                "--mode",
                                "industry",
                                "--run-id",
                                "test_run",
                                "--project-root",
                                str(root),
                            ]
                        )

                    self.assertEqual(status, 2, output)
                    self.assertIn(f"protected work/output file changed: {rel}", output)
                    self.assertFalse((root / "outputs" / "latest").exists())

    def test_post_run_validator_rejects_legacy_work_artifact_paths(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = self.init_git_root(temp_dir)
            (root / ".gitignore").write_text("work/**/*.md\n", encoding="utf-8")

            def fake_run(_command: list[str], *, cwd: Path) -> int:
                (cwd / "work" / "drafts").mkdir(parents=True, exist_ok=True)
                (cwd / "work" / "drafts" / "legacy.md").write_text("legacy", encoding="utf-8")
                self.write_default_outputs(root)
                return 0

            with patch("reportmachine_runner.cli.run_command", side_effect=fake_run):
                status, _ = self.run_cli(
                    [
                        "--mode",
                        "industry",
                        "--run-id",
                        "test_run",
                        "--project-root",
                        str(root),
                    ]
                )

            self.assertEqual(status, 2)
            self.assertFalse((root / "outputs" / "latest").exists())


if __name__ == "__main__":
    unittest.main()
