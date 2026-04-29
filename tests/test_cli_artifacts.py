from __future__ import annotations

from tests.cli_harness import *


class CliArtifactTests(CliHarness):
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

            with patch("reportmachine_runner.workflow.run_command", side_effect=fake_run):
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
                patch("reportmachine_runner.workflow.run_command", side_effect=fake_run),
                patch("reportmachine_runner.workflow.export_final_artifacts", side_effect=OSError("disk full")),
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

            with patch("reportmachine_runner.workflow.run_command", side_effect=fake_run):
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



if __name__ == "__main__":
    unittest.main()
