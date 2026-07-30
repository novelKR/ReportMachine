from __future__ import annotations

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tests.cli_harness import CliHarness

HAS_REPORTLAB = importlib.util.find_spec("reportlab") is not None
HAS_JSONSCHEMA = importlib.util.find_spec("jsonschema") is not None

# Tests that let the run reach finalize-phase audits need the schema validation
# dependency, because those audits validate JSON artifacts against schemas.
requires_schema_validation = unittest.skipUnless(
    HAS_JSONSCHEMA, "jsonschema is not installed"
)


class CliPublishIntegrationTests(CliHarness):
    def write_config(self, root: Path, body: str) -> None:
        (root / "reportmachine.toml").write_text(body, encoding="utf-8")

    def write_publish_schema(self, root: Path) -> None:
        self.copy_schema(root, "schemas/publish_result.schema.json")

    def test_dry_run_reports_publish_paths(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = self.init_git_root(temp_dir)
            status, output = self.run_cli(
                [
                    "--project-root",
                    str(root),
                    "--mode",
                    "industry",
                    "--run-id",
                    "test_run",
                    "--publish-pdf",
                    "--dry-run",
                ]
            )
            self.assertEqual(status, 0, output)
            self.assertIn("outputs/test_run/publish/article.pdf", output)
            self.assertIn("publish_render_surface: local_deterministic_renderer", output)

    def test_publish_dependency_gate_runs_before_codex(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = self.init_git_root(temp_dir)
            calls: list[list[str]] = []

            def fake_run(command: list[str], **_kwargs) -> int:
                calls.append(command)
                return 0

            with (
                patch(
                    "reportmachine_runner.workflow.publish_dependency_error",
                    return_value="missing optional publish dependency: reportlab",
                ),
                patch("reportmachine_runner.workflow.run_command", side_effect=fake_run),
            ):
                status, output = self.run_cli(
                    [
                        "--project-root",
                        str(root),
                        "--mode",
                        "industry",
                        "--run-id",
                        "test_run",
                        "--publish-pdf",
                    ]
                )

            self.assertEqual(status, 2)
            self.assertIn("reportlab", output)
            self.assertEqual(calls, [])

    @requires_schema_validation
    def test_publish_disabled_by_default(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = self.init_git_root(temp_dir)

            def fake_run(_command: list[str], *, cwd: Path) -> int:
                self.write_valid_article_outputs(root)
                return 0

            with (
                patch("reportmachine_runner.workflow.run_command", side_effect=fake_run),
                patch(
                    "reportmachine_runner.workflow.run_publish_workflow"
                ) as publish_mock,
            ):
                status, output = self.run_cli(
                    [
                        "--project-root",
                        str(root),
                        "--mode",
                        "industry",
                        "--run-id",
                        "test_run",
                    ]
                )

            self.assertEqual(status, 0, output)
            publish_mock.assert_not_called()
            self.assertFalse((root / "outputs/test_run/publish").exists())

    @requires_schema_validation
    def test_publish_runs_after_finalize_and_before_summary(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = self.init_git_root(temp_dir)
            order: list[str] = []

            def fake_run(_command: list[str], *, cwd: Path) -> int:
                order.append("codex")
                self.write_valid_article_outputs(root)
                return 0

            def fake_publish(_config, **_kwargs) -> int:
                order.append("publish")
                return 0

            def fake_summary(_config, **_kwargs) -> int:
                order.append("summary")
                return 0

            with (
                patch("reportmachine_runner.workflow.run_command", side_effect=fake_run),
                patch(
                    "reportmachine_runner.workflow.publish_dependency_error",
                    return_value=None,
                ),
                patch(
                    "reportmachine_runner.workflow.run_publish_workflow",
                    side_effect=fake_publish,
                ),
                patch(
                    "reportmachine_runner.workflow.run_summary_workflow",
                    side_effect=fake_summary,
                ),
                patch("reportmachine_runner.workflow.mirror_latest_publish"),
                patch("reportmachine_runner.workflow.mirror_latest_summary"),
            ):
                status, output = self.run_cli(
                    [
                        "--project-root",
                        str(root),
                        "--mode",
                        "industry",
                        "--run-id",
                        "test_run",
                        "--publish-pdf",
                        "--summary-output",
                    ]
                )

            self.assertEqual(status, 0, output)
            # The harness targets `--phase finalize`, which is a single Codex role call.
            # Publishing must follow it and precede summary generation.
            self.assertEqual(order, ["codex", "publish", "summary"])

    @requires_schema_validation
    def test_publish_failure_does_not_damage_article_outputs(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = self.init_git_root(temp_dir)

            def fake_run(_command: list[str], *, cwd: Path) -> int:
                self.write_valid_article_outputs(root)
                return 0

            with (
                patch("reportmachine_runner.workflow.run_command", side_effect=fake_run),
                patch(
                    "reportmachine_runner.workflow.publish_dependency_error",
                    return_value=None,
                ),
                patch(
                    "reportmachine_runner.workflow.run_publish_workflow",
                    return_value=2,
                ),
            ):
                status, output = self.run_cli(
                    [
                        "--project-root",
                        str(root),
                        "--mode",
                        "industry",
                        "--run-id",
                        "test_run",
                        "--publish-pdf",
                    ]
                )

            self.assertEqual(status, 2, output)
            article = root / "outputs/test_run/article.md"
            self.assertTrue(article.is_file())
            self.assertIn("본문입니다", article.read_text(encoding="utf-8"))
            result = json.loads(
                (root / "outputs/test_run/article_result.json").read_text(encoding="utf-8")
            )
            # article_result.json has a closed schema; publishing must not touch it.
            self.assertNotIn("publish", json.dumps(result))
            self.assertEqual(result["checklist_status"], "pass")
            self.assertFalse((root / "outputs/latest").exists())

    @requires_schema_validation
    def test_toml_profile_enables_publish(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = self.init_git_root(temp_dir)
            self.write_config(
                root,
                """
[profiles.default]
mode = "industry"

[profiles.default.source_ingest]
enabled = false

[profiles.default.publish]
enabled = true
margin_mm = 18.0
include_toc = false
""".lstrip(),
            )

            def fake_run(_command: list[str], *, cwd: Path) -> int:
                self.write_valid_article_outputs(root)
                return 0

            with (
                patch("reportmachine_runner.workflow.run_command", side_effect=fake_run),
                patch(
                    "reportmachine_runner.workflow.publish_dependency_error",
                    return_value=None,
                ),
                patch(
                    "reportmachine_runner.workflow.run_publish_workflow", return_value=0
                ) as publish_mock,
                patch("reportmachine_runner.workflow.mirror_latest_publish") as mirror_mock,
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

            self.assertEqual(status, 0, output)
            publish_mock.assert_called_once()
            mirror_mock.assert_called_once()
            config = publish_mock.call_args.args[0]
            self.assertEqual(config.profile.margin_mm, 18.0)
            self.assertFalse(config.profile.include_toc)

    @requires_schema_validation
    def test_external_artifact_export_includes_publish_directory(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = self.init_git_root(temp_dir)
            external_root = Path(temp_dir) / "artifacts"
            self.write_config(
                root,
                f"""
[profiles.default]
mode = "industry"

[profiles.default.source_ingest]
enabled = false

[profiles.default.publish]
enabled = true

[profiles.default.artifacts]
external_root = "{external_root.as_posix()}"
""".lstrip(),
            )

            def fake_run(_command: list[str], *, cwd: Path) -> int:
                self.write_valid_article_outputs(root)
                return 0

            def fake_publish(config, **_kwargs) -> int:
                publish_dir = root / "outputs" / "test_run" / "publish"
                publish_dir.mkdir(parents=True, exist_ok=True)
                (publish_dir / "article.pdf").write_bytes(b"%PDF-1.4 test\n")
                return 0

            with (
                patch("reportmachine_runner.workflow.run_command", side_effect=fake_run),
                patch(
                    "reportmachine_runner.workflow.publish_dependency_error",
                    return_value=None,
                ),
                patch(
                    "reportmachine_runner.workflow.run_publish_workflow",
                    side_effect=fake_publish,
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
                    ]
                )

            self.assertEqual(status, 0, output)
            self.assertTrue(
                (external_root / "runs/test_run/publish/article.pdf").is_file()
            )
            self.assertTrue((external_root / "latest/publish/article.pdf").is_file())
            self.assertTrue((root / "outputs/latest/publish/article.pdf").is_file())


if __name__ == "__main__":
    unittest.main()
