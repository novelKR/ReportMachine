from __future__ import annotations

from tests.cli_harness import *


class CliPrepareTests(CliHarness):
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

            with patch("reportmachine_runner.workflow.run_command", return_value=7) as run_command:
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

    def test_prepare_only_with_intake_writes_run_local_inputs_and_manifest_without_codex(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "project-root"

            status, output = self.run_cli(
                [
                    "--mode",
                    "industry",
                    "--run-id",
                    "test_run",
                    "--project-root",
                    str(root),
                    "--codex-bin",
                    "missing-codex-bin",
                    "--prepare-only",
                    "--intake-topic",
                    "AI 데이터센터 전력 인프라 병목과 전력기기 공급망",
                    "--intake-core-question",
                    "전력기기 공급망이 왜 병목이 되는가?",
                    "--intake-recent-trigger",
                    "최근 데이터센터 전력 수요 전망",
                    "--intake-source-note",
                    "IEA and official utility planning materials",
                    "--intake-output-notes",
                    "full report",
                ]
            )

            self.assertEqual(status, 0, output)
            topic = root / "work" / "runs" / "test_run" / "intake" / "topic.md"
            sources = root / "work" / "runs" / "test_run" / "intake" / "sources.csv"
            manifest = root / "work" / "runs" / "test_run" / "prepare_manifest.json"
            source_candidate = root / "work" / "runs" / "test_run" / "ledgers" / "source_ledger.candidate.csv"
            self.assertIn("AI 데이터센터", topic.read_text(encoding="utf-8"))
            self.assertIn(SOURCE_DISCOVERY_REQUIRED_ID, sources.read_text(encoding="utf-8"))
            self.assertIn(SOURCE_DISCOVERY_REQUIRED_ID, source_candidate.read_text(encoding="utf-8"))
            manifest_payload = json.loads(manifest.read_text(encoding="utf-8"))
            self.assertEqual(manifest_payload["execution"]["codex_command"][0], "missing-codex-bin")
            self.assertEqual(manifest_payload["paths"]["topic"], "work/runs/test_run/intake/topic.md")
            self.assertEqual(
                manifest_payload["paths"]["result_output"],
                "outputs/test_run/article_result.json",
            )
            manifest_prompt = manifest_payload["prompt"]
            self.assertIn(
                "write that schema-valid JSON directly to `outputs/test_run/article_result.json`",
                manifest_prompt,
            )
            self.assertIn("App-native fallback flow does not run Codex CLI `-o`", manifest_prompt)
            self.assertNotIn(
                "Do not write `outputs/test_run/article_result.json` directly",
                manifest_prompt,
            )
            command_prompt = manifest_payload["execution"]["codex_command"][-1]
            self.assertIn(
                "Do not write `outputs/test_run/article_result.json` directly",
                command_prompt,
            )
            self.assertIn("Codex CLI `-o` saves the final assistant message", command_prompt)
            self.assertIn("prepare-only", output)
            self.assertFalse((root / "outputs" / "latest").exists())

    def test_prepare_only_with_explicit_run_local_inputs_does_not_require_codex(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "project-root"
            intake = root / "work" / "runs" / "test_run" / "intake"
            intake.mkdir(parents=True)
            (intake / "topic.md").write_text("## Topic\n\nExample\n", encoding="utf-8")
            (intake / "sources.csv").write_text(
                "source_id,article_type,company,period_role,period,doc_kind,issuer,date,title,url,source_tier,required,local_path,notes\n",
                encoding="utf-8",
            )

            status, output = self.run_cli(
                [
                    "--mode",
                    "industry",
                    "--run-id",
                    "test_run",
                    "--project-root",
                    str(root),
                    "--codex-bin",
                    "missing-codex-bin",
                    "--prepare-only",
                    "--topic",
                    "work/runs/test_run/intake/topic.md",
                    "--sources",
                    "work/runs/test_run/intake/sources.csv",
                ]
            )

            self.assertEqual(status, 0, output)
            manifest = json.loads(
                (root / "work" / "runs" / "test_run" / "prepare_manifest.json").read_text(
                    encoding="utf-8"
                )
            )
            self.assertEqual(manifest["paths"]["topic"], "work/runs/test_run/intake/topic.md")
            self.assertEqual(manifest["execution"]["codex_command"][0], "missing-codex-bin")

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
                        "claim_ledger_json": "outputs/test_run/claim_ledger.json",
                        "preflight_result_json": "outputs/test_run/preflight_result.json",
                        "run_manifest_json": "outputs/test_run/run_manifest.json",
                    },
                    "critical_warnings": [],
                    "checklist_status": checklist_status,
                }

                errors = list(validator.iter_errors(payload))
                self.assertTrue(errors)



if __name__ == "__main__":
    unittest.main()
