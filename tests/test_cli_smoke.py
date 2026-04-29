from __future__ import annotations

from tests.cli_harness import *


class CliSmokeTests(CliHarness):
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
                patch("reportmachine_runner.workflow.run_source_ingest") as run_source_ingest,
                patch("reportmachine_runner.workflow.run_command", return_value=7) as run_command,
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



if __name__ == "__main__":
    unittest.main()
