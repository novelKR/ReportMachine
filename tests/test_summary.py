from __future__ import annotations

import csv
import io
import json
import subprocess
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

from reportmachine_runner.summary import (
    SUMMARY_CLAIM_MAP_HEADER,
    SUMMARY_SCHEMA,
    SummaryProfile,
    build_summary_run_config,
    main as summary_main,
    run_summary_workflow,
    source_hashes,
)

try:
    from jsonschema import Draft202012Validator
except ModuleNotFoundError:
    Draft202012Validator = None


class SummaryWorkflowTests(unittest.TestCase):
    def run_summary_cli(self, argv: list[str]) -> tuple[int, str]:
        stdout = io.StringIO()
        stderr = io.StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            try:
                status = summary_main(argv)
            except SystemExit as exc:
                status = exc.code if isinstance(exc.code, int) else 1
        return status, stdout.getvalue() + stderr.getvalue()

    def write_article_outputs(self, root: Path, *, status: str = "pass_with_warnings") -> None:
        output_dir = root / "outputs" / "test_run"
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
        (output_dir / "article_result.json").write_text(
            json.dumps(
                {
                    "article_type": "industry",
                    "title": "제목",
                    "subtitle": "부제",
                    "one_sentence_insight": "핵심",
                    "scorecard": None,
                    "output_files": {
                        "article_md": "outputs/test_run/article.md",
                        "source_ledger_csv": "outputs/test_run/source_ledger.csv",
                        "image_ledger_csv": "outputs/test_run/image_ledger.csv",
                    },
                    "critical_warnings": [],
                    "checklist_status": status,
                },
                ensure_ascii=False,
            )
            + "\n",
            encoding="utf-8",
        )

    def write_summary_schema(self, root: Path) -> None:
        schema_source = Path(__file__).resolve().parents[1] / SUMMARY_SCHEMA
        target = root / SUMMARY_SCHEMA
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(schema_source.read_text(encoding="utf-8"), encoding="utf-8")

    def init_git_root(self, root: Path) -> None:
        subprocess.run(
            ["git", "init"],
            cwd=root,
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

    def write_claim_map_with_row(self, root: Path, config) -> None:
        self.write_claim_map_rows(
            root,
            config,
            [
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
            ],
        )

    def write_claim_map_rows(self, root: Path, config, rows: list[dict[str, str]]) -> None:
        claim_map = root / config.paths.summary_claim_map
        claim_map.parent.mkdir(parents=True, exist_ok=True)
        with claim_map.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=SUMMARY_CLAIM_MAP_HEADER)
            writer.writeheader()
            writer.writerows(rows)

    def write_success_summary_result(
        self,
        root: Path,
        config,
        hashes: dict[str, str],
        *,
        article_sha256: str | None = None,
        result_sha256: str | None = None,
    ) -> None:
        summary_dir = root / config.paths.summary_dir
        summary_dir.mkdir(parents=True, exist_ok=True)
        (summary_dir / "summary_a4.png").write_bytes(b"png")
        self.write_claim_map_with_row(root, config)
        (summary_dir / "summary_result.json").write_text(
            json.dumps(
                {
                    "summary_type": "a4_visual",
                    "source_article": config.sources.article,
                    "source_article_sha256": article_sha256 or hashes["article_sha256"],
                    "source_result": config.sources.article_result,
                    "source_result_sha256": result_sha256
                    or hashes["article_result_sha256"],
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

    def assert_claim_map_rows_fail(
        self,
        rows: list[dict[str, str]],
        *,
        expected_warning: str,
    ) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            self.init_git_root(root)
            self.write_article_outputs(root)
            self.write_summary_schema(root)
            latest_summary = root / "outputs" / "latest" / "summary"
            latest_summary.mkdir(parents=True)
            (latest_summary / "summary_a4.png").write_bytes(b"stale")
            config = build_summary_run_config(root=root, run_id="test_run")
            hashes = source_hashes(config)

            def fake_run(_command: list[str], *, cwd: Path) -> int:
                self.write_success_summary_result(cwd, config, hashes)
                self.write_claim_map_rows(cwd, config, rows)
                return 0

            with patch("reportmachine_runner.summary_workflow.run_command", side_effect=fake_run):
                status = run_summary_workflow(config)

            self.assertEqual(status, 2)
            result = json.loads(
                (root / "outputs/test_run/summary/summary_result.json").read_text(
                    encoding="utf-8"
                )
            )
            self.assertEqual(result["checklist_status"], "fail")
            self.assertTrue(
                any(
                    expected_warning in warning
                    for warning in result["critical_warnings"]
                ),
                result["critical_warnings"],
            )
            self.assertEqual((latest_summary / "summary_a4.png").read_bytes(), b"stale")
            self.assertFalse((latest_summary / "summary_result.json").exists())

    def test_run_summary_missing_run_returns_clear_error(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)

            status, output = self.run_summary_cli(
                [
                    "--project-root",
                    str(root),
                    "--run-id",
                    "missing",
                ]
            )

            self.assertEqual(status, 2)
            self.assertIn("missing article_result: outputs/missing/article_result.json", output)

    def test_failed_article_skips_image_generation_and_writes_failure_result(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            self.write_article_outputs(root, status="fail")
            self.write_summary_schema(root)
            config = build_summary_run_config(root=root, run_id="test_run")

            with patch("reportmachine_runner.summary_workflow.run_command") as run_command:
                status = run_summary_workflow(config)

            self.assertEqual(status, 2)
            run_command.assert_not_called()
            result = json.loads(
                (root / "outputs/test_run/summary/summary_result.json").read_text(
                    encoding="utf-8"
                )
            )
            self.assertEqual(result["checklist_status"], "fail")
            self.assertIn("summary image generation skipped", result["critical_warnings"][0])
            self.assertFalse((root / "outputs/test_run/summary/summary_a4.png").exists())

    def test_pass_with_warnings_allows_summary_generation(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            self.init_git_root(root)
            self.write_article_outputs(root, status="pass_with_warnings")
            self.write_summary_schema(root)
            profile = SummaryProfile(
                style_name="operator_brief",
                style_prompt="Operator board style.",
                negative_prompt="No tiny text.",
            )
            config = build_summary_run_config(root=root, run_id="test_run", profile=profile)
            hashes = source_hashes(config)

            def fake_run(_command: list[str], *, cwd: Path) -> int:
                summary_dir = cwd / "outputs/test_run/summary"
                (summary_dir / "summary_a4.png").write_bytes(b"png")
                self.write_claim_map_with_row(cwd, config)
                (summary_dir / "summary_result.json").write_text(
                    json.dumps(
                        {
                            "summary_type": "a4_visual",
                            "source_article": "outputs/test_run/article.md",
                            "source_article_sha256": hashes["article_sha256"],
                            "source_result": "outputs/test_run/article_result.json",
                            "source_result_sha256": hashes["article_result_sha256"],
                            "image_generation_surface": "codex_internal_tool",
                            "output_files": {
                                "summary_a4_png": "outputs/test_run/summary/summary_a4.png",
                                "summary_prompt_md": "outputs/test_run/summary/summary_prompt.md",
                                "summary_claim_map_csv": "outputs/test_run/summary/summary_claim_map.csv",
                                "summary_manifest_json": "outputs/test_run/summary/summary_manifest.json",
                            },
                            "critical_warnings": [],
                            "checklist_status": "pass_with_warnings",
                        }
                    )
                    + "\n",
                    encoding="utf-8",
                )
                return 0

            with patch("reportmachine_runner.summary_workflow.run_command", side_effect=fake_run):
                status = run_summary_workflow(config)

            self.assertEqual(status, 0)
            prompt = (root / "outputs/test_run/summary/summary_prompt.md").read_text(
                encoding="utf-8"
            )
            self.assertIn("Operator board style.", prompt)
            self.assertIn("No tiny text.", prompt)
            self.assertTrue((root / "outputs/latest/summary/summary_a4.png").is_file())

    def test_missing_generated_image_overwrites_summary_result_with_failure(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            self.init_git_root(root)
            self.write_article_outputs(root)
            self.write_summary_schema(root)
            config = build_summary_run_config(root=root, run_id="test_run")
            hashes = source_hashes(config)

            def fake_run(_command: list[str], *, cwd: Path) -> int:
                (cwd / "outputs/test_run/summary/summary_result.json").write_text(
                    json.dumps(
                        {
                            "summary_type": "a4_visual",
                            "source_article": "outputs/test_run/article.md",
                            "source_article_sha256": hashes["article_sha256"],
                            "source_result": "outputs/test_run/article_result.json",
                            "source_result_sha256": hashes["article_result_sha256"],
                            "image_generation_surface": "codex_internal_tool",
                            "output_files": {
                                "summary_a4_png": "outputs/test_run/summary/summary_a4.png",
                                "summary_prompt_md": "outputs/test_run/summary/summary_prompt.md",
                                "summary_claim_map_csv": "outputs/test_run/summary/summary_claim_map.csv",
                                "summary_manifest_json": "outputs/test_run/summary/summary_manifest.json",
                            },
                            "critical_warnings": [],
                            "checklist_status": "pass",
                        }
                    )
                    + "\n",
                    encoding="utf-8",
                )
                return 0

            with patch("reportmachine_runner.summary_workflow.run_command", side_effect=fake_run):
                status = run_summary_workflow(config)

            self.assertEqual(status, 2)
            result = json.loads(
                (root / "outputs/test_run/summary/summary_result.json").read_text(
                    encoding="utf-8"
                )
            )
            self.assertEqual(result["checklist_status"], "fail")
            self.assertIn("missing generated summary image", result["critical_warnings"][0])

    def test_header_only_claim_map_overwrites_summary_result_with_failure(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            self.init_git_root(root)
            self.write_article_outputs(root)
            self.write_summary_schema(root)
            latest_summary = root / "outputs" / "latest" / "summary"
            latest_summary.mkdir(parents=True)
            (latest_summary / "summary_a4.png").write_bytes(b"stale")
            config = build_summary_run_config(root=root, run_id="test_run")
            hashes = source_hashes(config)

            def fake_run(_command: list[str], *, cwd: Path) -> int:
                summary_dir = cwd / "outputs/test_run/summary"
                (summary_dir / "summary_a4.png").write_bytes(b"png")
                (summary_dir / "summary_result.json").write_text(
                    json.dumps(
                        {
                            "summary_type": "a4_visual",
                            "source_article": "outputs/test_run/article.md",
                            "source_article_sha256": hashes["article_sha256"],
                            "source_result": "outputs/test_run/article_result.json",
                            "source_result_sha256": hashes["article_result_sha256"],
                            "image_generation_surface": "codex_internal_tool",
                            "output_files": {
                                "summary_a4_png": "outputs/test_run/summary/summary_a4.png",
                                "summary_prompt_md": "outputs/test_run/summary/summary_prompt.md",
                                "summary_claim_map_csv": "outputs/test_run/summary/summary_claim_map.csv",
                                "summary_manifest_json": "outputs/test_run/summary/summary_manifest.json",
                            },
                            "critical_warnings": [],
                            "checklist_status": "pass",
                        }
                    )
                    + "\n",
                    encoding="utf-8",
                )
                return 0

            with patch("reportmachine_runner.summary_workflow.run_command", side_effect=fake_run):
                status = run_summary_workflow(config)

            self.assertEqual(status, 2)
            result = json.loads(
                (root / "outputs/test_run/summary/summary_result.json").read_text(
                    encoding="utf-8"
                )
            )
            self.assertEqual(result["checklist_status"], "fail")
            self.assertIn(
                "summary claim map must include at least one data row",
                result["critical_warnings"][0],
            )
            self.assertEqual((latest_summary / "summary_a4.png").read_bytes(), b"stale")
            self.assertFalse((latest_summary / "summary_result.json").exists())

    def test_claim_map_row_with_only_summary_item_id_fails(self) -> None:
        self.assert_claim_map_rows_fail(
            [{"summary_item_id": "s1"}],
            expected_warning=(
                "summary claim map row 2 missing required fields "
                "summary_text, claim_type, source_artifact, source_path, source_anchor"
            ),
        )

    def test_claim_map_row_with_only_notes_fails(self) -> None:
        self.assert_claim_map_rows_fail(
            [{"notes": "placeholder"}],
            expected_warning=(
                "summary claim map row 2 missing required fields "
                "summary_item_id, summary_text, claim_type, source_artifact, "
                "source_path, source_anchor"
            ),
        )

    def test_claim_map_row_missing_required_field_fails(self) -> None:
        self.assert_claim_map_rows_fail(
            [
                {
                    "summary_item_id": "s1",
                    "summary_text": "",
                    "claim_type": "statement",
                    "source_artifact": "article.md",
                    "source_path": "outputs/test_run/article.md",
                    "source_anchor": "# 제목",
                }
            ],
            expected_warning=(
                "summary claim map row 2 missing required fields summary_text"
            ),
        )

    def test_claim_map_row_with_invalid_source_artifact_fails(self) -> None:
        self.assert_claim_map_rows_fail(
            [
                {
                    "summary_item_id": "s1",
                    "summary_text": "본문입니다.",
                    "claim_type": "statement",
                    "source_artifact": "made_up.csv",
                    "source_path": "outputs/test_run/made_up.csv",
                    "source_anchor": "# 제목",
                }
            ],
            expected_warning=(
                "summary claim map row 2 source_artifact must be one of "
                "article.md, source_ledger.csv, image_ledger.csv"
            ),
        )

    def test_invalid_claim_map_header_overwrites_summary_result_with_failure(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            self.init_git_root(root)
            self.write_article_outputs(root)
            self.write_summary_schema(root)
            latest_summary = root / "outputs" / "latest" / "summary"
            latest_summary.mkdir(parents=True)
            (latest_summary / "summary_a4.png").write_bytes(b"stale")
            config = build_summary_run_config(root=root, run_id="test_run")
            hashes = source_hashes(config)

            def fake_run(_command: list[str], *, cwd: Path) -> int:
                summary_dir = cwd / "outputs/test_run/summary"
                (summary_dir / "summary_a4.png").write_bytes(b"png")
                (summary_dir / "summary_claim_map.csv").write_text(
                    "summary_text,summary_item_id\n본문입니다.,s1\n",
                    encoding="utf-8",
                )
                (summary_dir / "summary_result.json").write_text(
                    json.dumps(
                        {
                            "summary_type": "a4_visual",
                            "source_article": "outputs/test_run/article.md",
                            "source_article_sha256": hashes["article_sha256"],
                            "source_result": "outputs/test_run/article_result.json",
                            "source_result_sha256": hashes["article_result_sha256"],
                            "image_generation_surface": "codex_internal_tool",
                            "output_files": {
                                "summary_a4_png": "outputs/test_run/summary/summary_a4.png",
                                "summary_prompt_md": "outputs/test_run/summary/summary_prompt.md",
                                "summary_claim_map_csv": "outputs/test_run/summary/summary_claim_map.csv",
                                "summary_manifest_json": "outputs/test_run/summary/summary_manifest.json",
                            },
                            "critical_warnings": [],
                            "checklist_status": "pass",
                        }
                    )
                    + "\n",
                    encoding="utf-8",
                )
                return 0

            with patch("reportmachine_runner.summary_workflow.run_command", side_effect=fake_run):
                status = run_summary_workflow(config)

            self.assertEqual(status, 2)
            result = json.loads(
                (root / "outputs/test_run/summary/summary_result.json").read_text(
                    encoding="utf-8"
                )
            )
            self.assertEqual(result["checklist_status"], "fail")
            self.assertIn(
                "summary claim map header must match contract",
                result["critical_warnings"][0],
            )
            self.assertEqual((latest_summary / "summary_a4.png").read_bytes(), b"stale")
            self.assertFalse((latest_summary / "summary_result.json").exists())

    def test_codex_generated_failure_result_returns_nonzero(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            self.init_git_root(root)
            self.write_article_outputs(root)
            self.write_summary_schema(root)
            config = build_summary_run_config(root=root, run_id="test_run")
            hashes = source_hashes(config)

            def fake_run(_command: list[str], *, cwd: Path) -> int:
                (cwd / "outputs/test_run/summary/summary_result.json").write_text(
                    json.dumps(
                        {
                            "summary_type": "a4_visual",
                            "source_article": "outputs/test_run/article.md",
                            "source_article_sha256": hashes["article_sha256"],
                            "source_result": "outputs/test_run/article_result.json",
                            "source_result_sha256": hashes["article_result_sha256"],
                            "image_generation_surface": "codex_internal_tool",
                            "output_files": {
                                "summary_a4_png": "outputs/test_run/summary/summary_a4.png",
                                "summary_prompt_md": "outputs/test_run/summary/summary_prompt.md",
                                "summary_claim_map_csv": "outputs/test_run/summary/summary_claim_map.csv",
                                "summary_manifest_json": "outputs/test_run/summary/summary_manifest.json",
                            },
                            "critical_warnings": ["image tool unavailable"],
                            "checklist_status": "fail",
                        }
                    )
                    + "\n",
                    encoding="utf-8",
                )
                return 0

            with patch("reportmachine_runner.summary_workflow.run_command", side_effect=fake_run):
                status = run_summary_workflow(config)

            self.assertEqual(status, 2)
            self.assertFalse((root / "outputs/latest/summary").exists())

    def test_summary_generation_mutating_protected_inputs_fails_without_refreshing_latest(self) -> None:
        cases = [
            ("article", "outputs/test_run/article.md"),
            ("source_ledger", "outputs/test_run/source_ledger.csv"),
            ("image_ledger", "outputs/test_run/image_ledger.csv"),
            ("protected_repo_file", "protected.md"),
        ]
        for label, rel in cases:
            with self.subTest(label=label):
                with tempfile.TemporaryDirectory() as temp_dir:
                    root = Path(temp_dir)
                    self.init_git_root(root)
                    protected = root / "protected.md"
                    protected.write_text("protected before\n", encoding="utf-8")
                    subprocess.run(
                        ["git", "add", "protected.md"],
                        cwd=root,
                        check=True,
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                    )
                    self.write_article_outputs(root)
                    self.write_summary_schema(root)
                    latest_summary = root / "outputs" / "latest" / "summary"
                    latest_summary.mkdir(parents=True)
                    (latest_summary / "summary_a4.png").write_bytes(b"stale")
                    config = build_summary_run_config(root=root, run_id="test_run")
                    hashes = source_hashes(config)

                    def fake_run(_command: list[str], *, cwd: Path) -> int:
                        target = cwd / rel
                        target.write_text(f"{label} after\n", encoding="utf-8")
                        self.write_success_summary_result(cwd, config, hashes)
                        return 0

                    with patch("reportmachine_runner.summary_workflow.run_command", side_effect=fake_run):
                        status = run_summary_workflow(config)

                    self.assertEqual(status, 2)
                    self.assertEqual(
                        (latest_summary / "summary_a4.png").read_bytes(),
                        b"stale",
                    )
                    self.assertFalse((latest_summary / "summary_result.json").exists())

    def test_stale_summary_source_hashes_fail_without_refreshing_latest(self) -> None:
        cases = [
            ("source_article_sha256", "0" * 64, None),
            ("source_result_sha256", None, "1" * 64),
        ]
        for label, article_sha256, result_sha256 in cases:
            with self.subTest(label=label):
                with tempfile.TemporaryDirectory() as temp_dir:
                    root = Path(temp_dir)
                    self.init_git_root(root)
                    self.write_article_outputs(root)
                    self.write_summary_schema(root)
                    latest_summary = root / "outputs" / "latest" / "summary"
                    latest_summary.mkdir(parents=True)
                    (latest_summary / "summary_a4.png").write_bytes(b"stale")
                    config = build_summary_run_config(root=root, run_id="test_run")
                    hashes = source_hashes(config)

                    def fake_run(_command: list[str], *, cwd: Path) -> int:
                        self.write_success_summary_result(
                            cwd,
                            config,
                            hashes,
                            article_sha256=article_sha256,
                            result_sha256=result_sha256,
                        )
                        return 0

                    with patch("reportmachine_runner.summary_workflow.run_command", side_effect=fake_run):
                        status = run_summary_workflow(config)

                    self.assertEqual(status, 2)
                    self.assertEqual(
                        (latest_summary / "summary_a4.png").read_bytes(),
                        b"stale",
                    )

    def test_unsafe_run_ids_are_rejected_by_config_builder_and_summary_main(self) -> None:
        cases = ["", "..", "../foo", "a/b", "test/../latest", ".hidden", "-leading"]
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            for run_id in cases:
                with self.subTest(run_id=run_id):
                    with self.assertRaises(ValueError):
                        build_summary_run_config(root=root, run_id=run_id)

                    status, output = self.run_summary_cli(
                        [
                            "--project-root",
                            str(root),
                            "--run-id",
                            run_id,
                            "--smoke-test",
                        ]
                    )

                    self.assertEqual(status, 2)
                    self.assertIn("--run-id", output)


@unittest.skipIf(Draft202012Validator is None, "jsonschema is not installed")
class SummarySchemaTests(unittest.TestCase):
    def validator(self) -> Draft202012Validator:
        schema = json.loads(
            (Path(__file__).resolve().parents[1] / SUMMARY_SCHEMA).read_text(
                encoding="utf-8"
            )
        )
        return Draft202012Validator(schema)

    def base_payload(self) -> dict[str, object]:
        return {
            "summary_type": "a4_visual",
            "source_article": "outputs/test_run/article.md",
            "source_article_sha256": "a" * 64,
            "source_result": "outputs/test_run/article_result.json",
            "source_result_sha256": "b" * 64,
            "image_generation_surface": "codex_internal_tool",
            "output_files": {
                "summary_a4_png": "outputs/test_run/summary/summary_a4.png",
                "summary_prompt_md": "outputs/test_run/summary/summary_prompt.md",
                "summary_claim_map_csv": "outputs/test_run/summary/summary_claim_map.csv",
                "summary_manifest_json": "outputs/test_run/summary/summary_manifest.json",
            },
            "critical_warnings": [],
            "checklist_status": "pass",
        }

    def test_summary_result_schema_accepts_valid_payload(self) -> None:
        errors = list(self.validator().iter_errors(self.base_payload()))
        self.assertEqual(errors, [])

    def test_summary_result_schema_rejects_output_outside_summary_dir(self) -> None:
        payload = self.base_payload()
        output_files = dict(payload["output_files"])  # type: ignore[arg-type]
        output_files["summary_a4_png"] = "outputs/test_run/article.png"
        payload["output_files"] = output_files

        errors = list(self.validator().iter_errors(payload))

        self.assertTrue(errors)

    def test_summary_result_schema_rejects_parent_directory_run_id(self) -> None:
        payload = self.base_payload()
        output_files = dict(payload["output_files"])  # type: ignore[arg-type]
        output_files["summary_a4_png"] = "outputs/../summary/summary_a4.png"
        payload["output_files"] = output_files

        errors = list(self.validator().iter_errors(payload))

        self.assertTrue(errors)


if __name__ == "__main__":
    unittest.main()
