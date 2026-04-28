from __future__ import annotations

import io
import subprocess
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

from reportmachine_runner import cli


class CliDryRunTests(unittest.TestCase):
    def run_cli(self, argv: list[str]) -> tuple[int, str]:
        stdout = io.StringIO()
        stderr = io.StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            status = cli.main(argv)
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
            self.assertIn('web_search="live"', output)
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
