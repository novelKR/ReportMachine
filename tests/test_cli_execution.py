from __future__ import annotations

from tests.cli_harness import *


class CliExecutionTests(CliHarness):
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

            with patch("reportmachine_runner.workflow.run_command", return_value=7) as run_command:
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

            with patch("reportmachine_runner.workflow.run_command", return_value=7) as run_command:
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
                patch("reportmachine_runner.workflow.run_source_ingest") as run_source_ingest,
                patch("reportmachine_runner.workflow.run_command", return_value=7) as run_command,
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
                patch("reportmachine_runner.workflow.run_source_ingest", return_value=ingest_result),
                patch("reportmachine_runner.workflow.run_command", return_value=7) as run_command,
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

            with patch("reportmachine_runner.workflow.run_command", return_value=7) as run_command:
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

            with patch("reportmachine_runner.workflow.run_command", return_value=7) as run_command:
                status, _ = self.run_cli(argv)

            self.assertEqual(status, 7)
            command = run_command.call_args.args[0]
            self.assertIn('web_search="disabled"', command)
            self.assertNotIn('web_search="live"', command)

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

            with patch("reportmachine_runner.workflow.run_command", side_effect=fake_run):
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

            with patch("reportmachine_runner.workflow.run_command", side_effect=fake_run):
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

            with patch("reportmachine_runner.workflow.run_command", side_effect=fake_run):
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

            with patch("reportmachine_runner.workflow.run_command", side_effect=fake_run):
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

            with patch("reportmachine_runner.workflow.run_command", side_effect=fake_run):
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

            with patch("reportmachine_runner.workflow.run_command", side_effect=fake_run):
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

            with patch("reportmachine_runner.workflow.run_command", side_effect=fake_run):
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

                    with patch("reportmachine_runner.workflow.run_command", side_effect=fake_run):
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

            with patch("reportmachine_runner.workflow.run_command", side_effect=fake_run):
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
