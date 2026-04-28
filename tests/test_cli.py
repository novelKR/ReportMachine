from __future__ import annotations

import io
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from reportmachine_runner import cli


class CliDryRunTests(unittest.TestCase):
    def run_cli(self, argv: list[str]) -> tuple[int, str]:
        stdout = io.StringIO()
        with redirect_stdout(stdout):
            status = cli.main(argv)
        return status, stdout.getvalue()

    def test_dry_run_does_not_create_default_output_dirs_under_custom_project_root(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "missing-root"

            status, output = self.run_cli(
                [
                    "--mode",
                    "industry",
                    "--project-root",
                    str(root),
                    "--dry-run",
                ]
            )

            self.assertEqual(status, 0)
            self.assertIn("cwd:", output)
            self.assertIn("command:", output)
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

    def test_normal_run_creates_output_dirs_before_running_command(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "project-root"
            argv = [
                "--mode",
                "industry",
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
            ]:
                self.assertTrue(parent.is_dir())
            run_command.assert_called_once()


if __name__ == "__main__":
    unittest.main()
