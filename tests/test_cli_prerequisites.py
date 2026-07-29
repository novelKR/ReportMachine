from __future__ import annotations

import importlib.util

from tests.cli_harness import *

from reportmachine_runner import schema_validation, workflow
from reportmachine_runner.paths import validate_run_id
from reportmachine_runner.summary_workflow import main as summary_main


TOML_BACKEND_AVAILABLE = any(
    importlib.util.find_spec(name) is not None for name in ("tomllib", "tomli")
)


def missing_schema_dependency():
    """Patch find_spec so only the schema-validation package looks absent."""

    real_find_spec = importlib.util.find_spec

    def fake_find_spec(name: str, *args, **kwargs):
        if name == schema_validation.SCHEMA_VALIDATION_PACKAGE:
            return None
        return real_find_spec(name, *args, **kwargs)

    return patch.object(importlib.util, "find_spec", side_effect=fake_find_spec)


class PrerequisiteHarness(CliHarness):
    bypass_schema_dependency_gate = False

    def run_summary_cli(self, argv: list[str]) -> tuple[int, str]:
        stdout = io.StringIO()
        stderr = io.StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            try:
                status = summary_main(argv)
            except SystemExit as exc:
                status = exc.code if isinstance(exc.code, int) else 1
        return status, stdout.getvalue() + stderr.getvalue()


class SchemaDependencyProbeTests(unittest.TestCase):
    def test_probe_reports_error_only_when_package_is_absent(self) -> None:
        cases = [(None, True), (object(), False)]
        for spec, expect_error in cases:
            with self.subTest(expect_error=expect_error):
                with patch.object(importlib.util, "find_spec", return_value=spec):
                    result = schema_validation.schema_validation_dependency_error()

                if expect_error:
                    self.assertIsNotNone(result)
                    self.assertIn("jsonschema", result)
                    self.assertIn("python3 -m pip install -e .", result)
                else:
                    self.assertIsNone(result)


class SchemaDependencyPrerequisiteTests(PrerequisiteHarness):
    def prepare_industry_argv(self, root: Path, *extra: str) -> list[str]:
        return [
            "--mode",
            "industry",
            "--run-id",
            "test_run",
            "--project-root",
            str(root),
            *extra,
        ]

    def test_missing_dependency_blocks_run_before_codex_and_outputs(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "project-root"

            with (
                missing_schema_dependency(),
                patch("reportmachine_runner.workflow.run_command", return_value=0) as run_command,
            ):
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
            self.assertIn("missing required runtime dependency: jsonschema", output)
            self.assertIn("python3 -m pip install -e .", output)
            run_command.assert_not_called()
            self.assertFalse((root / "outputs" / "test_run").exists())
            self.assertFalse((root / "work" / "runs" / "test_run").exists())
            self.assertFalse((root / "outputs" / "latest").exists())

    def test_missing_dependency_still_allows_smoke_test_and_dry_run(self) -> None:
        for flag in ["--smoke-test", "--dry-run"]:
            with self.subTest(flag=flag):
                with tempfile.TemporaryDirectory() as temp_dir:
                    root = Path(temp_dir) / "project-root"

                    with (
                        missing_schema_dependency(),
                        patch(
                            "reportmachine_runner.workflow.run_command",
                            return_value=0,
                        ) as run_command,
                    ):
                        status, output = self.run_cli(
                            [
                                "--mode",
                                "industry",
                                "--run-id",
                                "test_run",
                                "--project-root",
                                str(root),
                                flag,
                            ]
                        )

                    self.assertEqual(status, 0, output)
                    self.assertNotIn("missing required runtime dependency", output)
                    run_command.assert_not_called()

    def test_missing_dependency_allows_prepare_phases(self) -> None:
        cases = [["--prepare-only"], ["--phase", "prepare"]]
        for extra in cases:
            with self.subTest(extra=" ".join(extra)):
                with tempfile.TemporaryDirectory() as temp_dir:
                    root = Path(temp_dir) / "project-root"

                    with (
                        missing_schema_dependency(),
                        patch(
                            "reportmachine_runner.workflow.run_command",
                            return_value=0,
                        ) as run_command,
                    ):
                        status, output = self.run_cli(
                            self.prepare_industry_argv(root, *extra)
                        )

                    self.assertEqual(status, 0, output)
                    self.assertNotIn("missing required runtime dependency", output)
                    run_command.assert_not_called()
                    self.assertTrue(
                        (
                            root
                            / "work"
                            / "runs"
                            / "test_run"
                            / "prepare_manifest.json"
                        ).is_file()
                    )
                    self.assertTrue(
                        (
                            root / "outputs" / "test_run" / "preflight_result.json"
                        ).is_file()
                    )

    def test_missing_dependency_allows_draft_phase(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "project-root"
            draft_output = (
                root / "work" / "runs" / "test_run" / "drafts" / "draft_article.md"
            )

            def fake_run(_command: list[str], *, cwd: Path) -> int:
                draft_output.parent.mkdir(parents=True, exist_ok=True)
                draft_output.write_text("# draft\n", encoding="utf-8")
                return 0

            with (
                missing_schema_dependency(),
                patch(
                    "reportmachine_runner.workflow.run_command",
                    side_effect=fake_run,
                ) as run_command,
            ):
                status, output = self.run_cli(
                    self.prepare_industry_argv(root, "--phase", "draft")
                )

            self.assertEqual(status, 0, output)
            self.assertNotIn("missing required runtime dependency", output)
            run_command.assert_called_once()
            self.assertTrue(draft_output.is_file())

    def test_missing_dependency_blocks_finalize_phases_before_codex(self) -> None:
        cases = [["--phase", "finalize"], ["--phase", "all"], []]
        for extra in cases:
            with self.subTest(extra=" ".join(extra) or "default"):
                with tempfile.TemporaryDirectory() as temp_dir:
                    root = Path(temp_dir) / "project-root"

                    with (
                        missing_schema_dependency(),
                        patch(
                            "reportmachine_runner.workflow.run_command",
                            return_value=0,
                        ) as run_command,
                    ):
                        status, output = self.run_cli(
                            self.prepare_industry_argv(root, *extra)
                        )

                    self.assertEqual(status, 2, output)
                    self.assertIn(
                        "missing required runtime dependency: jsonschema",
                        output,
                    )
                    run_command.assert_not_called()

    def test_present_dependency_keeps_existing_success_path(self) -> None:
        if Draft202012Validator is None:
            self.skipTest("jsonschema is not installed")
        with tempfile.TemporaryDirectory() as temp_dir:
            root = self.init_git_root(temp_dir)

            def fake_run(_command: list[str], *, cwd: Path) -> int:
                self.assertEqual(cwd, root.resolve())
                self.write_valid_article_outputs(root)
                return 0

            with (
                patch.object(
                    workflow,
                    "schema_validation_dependency_error",
                    return_value=None,
                ),
                patch("reportmachine_runner.workflow.run_command", side_effect=fake_run),
            ):
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

            self.assertEqual(status, 0, output)
            self.assertNotIn("missing required runtime dependency", output)
            self.assertTrue((root / "outputs" / "latest" / "article.md").is_file())


class ReservedRunIdTests(PrerequisiteHarness):
    def test_article_cli_rejects_reserved_run_id(self) -> None:
        for run_id in ["latest", "LATEST", "Latest"]:
            with self.subTest(run_id=run_id):
                with tempfile.TemporaryDirectory() as temp_dir:
                    root = Path(temp_dir) / "project-root"
                    status, output = self.run_cli(
                        [
                            "--mode",
                            "industry",
                            "--run-id",
                            run_id,
                            "--project-root",
                            str(root),
                            "--smoke-test",
                        ]
                    )

                    self.assertEqual(status, 2, output)
                    self.assertIn(
                        "--run-id must not use the reserved name 'latest'",
                        output,
                    )

    @unittest.skipUnless(TOML_BACKEND_AVAILABLE, "TOML backend is not installed")
    def test_config_run_id_prefix_rejects_reserved_name(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "project-root"
            root.mkdir(parents=True)
            self.write_config(
                root,
                """
[profiles.default]
mode = "industry"
run_id_prefix = "latest"
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

            self.assertEqual(status, 2, output)
            self.assertIn(
                "run_id_prefix must not use the reserved name 'latest'",
                output,
            )

    def test_summary_cli_rejects_reserved_run_id(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "project-root"
            root.mkdir(parents=True)

            status, output = self.run_summary_cli(
                [
                    "--run-id",
                    "latest",
                    "--project-root",
                    str(root),
                ]
            )

            self.assertEqual(status, 2, output)
            self.assertIn(
                "--run-id must not use the reserved name 'latest'",
                output,
            )

    def test_run_ids_containing_latest_remain_allowed(self) -> None:
        for run_id in ["latest_run", "run_latest", "latest-2026", "latestish"]:
            with self.subTest(run_id=run_id):
                self.assertEqual(validate_run_id(run_id), run_id)


if __name__ == "__main__":
    unittest.main()
