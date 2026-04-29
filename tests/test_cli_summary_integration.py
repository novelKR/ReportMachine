from __future__ import annotations

from tests.cli_harness import *


class CliSummaryIntegrationTests(CliHarness):
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
                patch("reportmachine_runner.workflow.run_command", side_effect=fake_article_run),
                patch("reportmachine_runner.summary_workflow.run_command", side_effect=fake_summary_run),
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
                patch("reportmachine_runner.workflow.run_command", side_effect=fake_article_run),
                patch("reportmachine_runner.summary_workflow.run_command", side_effect=fake_summary_run),
                patch(
                    "reportmachine_runner.workflow.export_final_artifacts",
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
                patch("reportmachine_runner.workflow.run_command", side_effect=fake_article_run),
                patch("reportmachine_runner.summary_workflow.run_command", side_effect=fake_summary_run),
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



if __name__ == "__main__":
    unittest.main()
