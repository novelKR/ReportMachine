from __future__ import annotations

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
from reportmachine_runner.source_ingest import SourceIngestResult, build_source_ingest_paths

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
        ]:
            if missing and name in missing:
                continue
            (output_dir / name).write_text(name, encoding="utf-8")

    def write_config(self, root: Path, text: str, name: str = "reportmachine.toml") -> Path:
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        return path

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

    def test_command_uses_codex_root_schema_run_outputs_and_live_search(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "project-root"
            argv = [
                "--mode",
                "earnings",
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
                ]:
                    path = root / rel
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_text(path.name, encoding="utf-8")
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
