from __future__ import annotations

from tests.cli_harness import *

from reportmachine_runner.review_issues import (
    validate_review_reports,
    validate_revision_coverage,
)


class ReviewIssueCoverageTests(CliHarness):
    def report_paths(self, root: Path) -> dict[str, Path]:
        base = root / "work" / "runs" / "test_run" / "reviews"
        return {
            "citation_logic": base / "citation_logic.json",
            "red_team": base / "red_team.json",
            "qa_report": base / "qa_report.json",
            "compression": base / "compression.json",
            "image_audit": base / "image_audit.json",
        }

    def blocking_issue(self, source_report: str) -> dict[str, str]:
        prefixes = {
            "citation_logic": "CIT",
            "red_team": "RT",
            "qa_report": "QA",
            "compression": "CMP",
            "image_audit": "IMG",
        }
        issue = {
            "issue_id": f"{prefixes[source_report]}-1",
            "priority": "blocking",
            "issue": f"{source_report} blocker",
            "required_fix": "fix it",
        }
        if source_report == "citation_logic":
            issue["issue_type"] = "missing_source"
        return issue

    def seed(self, root: Path) -> tuple[object, dict[str, Path]]:
        self.seed_pipeline_artifacts(root, include_images=True)
        artifacts = self.artifacts_for(root)
        paths = self.report_paths(root)
        for source_report, path in paths.items():
            payload = json.loads(path.read_text(encoding="utf-8"))
            payload["issues"] = [self.blocking_issue(source_report)]
            self.write_json(path, payload)
        return artifacts, paths

    def artifacts_for(self, root: Path):
        from reportmachine_runner.phase_contracts import build_run_artifacts

        return build_run_artifacts(
            work_run_dir="work/runs/test_run",
            outputs_dir="outputs/test_run",
            article="outputs/test_run/article.md",
            source_ledger="outputs/test_run/source_ledger.csv",
            image_ledger="outputs/test_run/image_ledger.csv",
            claim_ledger="outputs/test_run/claim_ledger.json",
            article_result="outputs/test_run/article_result.json",
        )

    def notes_path(self, root: Path) -> Path:
        return root / "work" / "runs" / "test_run" / "reviews" / "revision_notes.json"

    def issue_ref(self, source_report: str) -> dict[str, str]:
        prefixes = {
            "citation_logic": "CIT",
            "red_team": "RT",
            "qa_report": "QA",
            "compression": "CMP",
            "image_audit": "IMG",
        }
        return {
            "source_report": source_report,
            "issue_id": f"{prefixes[source_report]}-1",
            "resolution": "fixed",
            "note": "fixed",
        }

    def test_every_active_review_report_contributes_blockers(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            artifacts, _paths = self.seed(root)

            errors = validate_revision_coverage(
                root,
                artifacts,
                include_images=True,
                expected_run_id="test_run",
            )

            joined = "\n".join(errors)
            for expected in (
                "citation_logic:CIT-1",
                "red_team:RT-1",
                "qa_report:QA-1",
                "compression:CMP-1",
                "image_audit:IMG-1",
            ):
                self.assertIn(expected, joined)

    def test_exact_handled_coverage_passes(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            artifacts, paths = self.seed(root)
            notes = json.loads(self.notes_path(root).read_text(encoding="utf-8"))
            notes["handled_issues"] = [self.issue_ref(name) for name in paths]
            self.write_json(self.notes_path(root), notes)

            self.assertEqual(
                validate_revision_coverage(
                    root,
                    artifacts,
                    include_images=True,
                    expected_run_id="test_run",
                ),
                [],
            )

    def test_unknown_duplicate_and_overlapping_references_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            artifacts, _paths = self.seed(root)
            notes = json.loads(self.notes_path(root).read_text(encoding="utf-8"))
            citation = self.issue_ref("citation_logic")
            notes["handled_issues"] = [citation, dict(citation), {
                **citation,
                "issue_id": "CIT-999",
            }]
            notes["unresolved_blocking_issues"] = [
                {
                    "source_report": "citation_logic",
                    "issue_id": "CIT-1",
                    "note": "still open",
                }
            ]
            self.write_json(self.notes_path(root), notes)

            joined = "\n".join(
                validate_revision_coverage(
                    root,
                    artifacts,
                    include_images=True,
                    expected_run_id="test_run",
                )
            )

            self.assertIn("duplicate revision issue reference", joined)
            self.assertIn("unknown review issues", joined)
            self.assertIn("both handled and unresolved", joined)

    def test_duplicate_issue_id_inside_a_report_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            artifacts, paths = self.seed(root)
            citation = json.loads(paths["citation_logic"].read_text(encoding="utf-8"))
            citation["issues"].append(dict(citation["issues"][0]))
            self.write_json(paths["citation_logic"], citation)

            joined = "\n".join(
                validate_revision_coverage(
                    root,
                    artifacts,
                    include_images=True,
                    expected_run_id="test_run",
                )
            )

            self.assertIn("duplicate review issue id: citation_logic:CIT-1", joined)

    def test_nonblocking_issue_may_be_omitted(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            self.seed_pipeline_artifacts(root)
            artifacts = self.artifacts_for(root)
            citation = self.report_paths(root)["citation_logic"]
            payload = json.loads(citation.read_text(encoding="utf-8"))
            payload["issues"] = [{
                **self.blocking_issue("citation_logic"),
                "priority": "high",
            }]
            self.write_json(citation, payload)

            self.assertEqual(
                validate_revision_coverage(
                    root,
                    artifacts,
                    include_images=False,
                    expected_run_id="test_run",
                ),
                [],
            )

    def test_failure_verdict_without_a_blocker_is_rejected_for_each_report_type(self) -> None:
        cases = [
            ("citation_logic", "overall_status", "fail"),
            ("qa_report", "overall_status", "fail"),
            ("red_team", "verdict", "thesis_needs_qualification"),
            ("red_team", "verdict", "thesis_unsupported"),
        ]
        for source_report, field, value in cases:
            for issues in (
                [],
                [{**self.blocking_issue(source_report), "priority": "high"}],
                [{**self.blocking_issue(source_report), "priority": "normal"}],
            ):
                with self.subTest(source_report=source_report, issues=issues):
                    with tempfile.TemporaryDirectory() as temp_dir:
                        root = Path(temp_dir)
                        self.seed_pipeline_artifacts(root)
                        artifacts = self.artifacts_for(root)
                        path = self.report_paths(root)[source_report]
                        payload = json.loads(path.read_text(encoding="utf-8"))
                        payload[field] = value
                        payload["issues"] = issues
                        self.write_json(path, payload)

                        joined = "\n".join(
                            validate_review_reports(
                                root,
                                artifacts,
                                include_images=False,
                                expected_run_id="test_run",
                            )
                        )

                        self.assertIn("without a blocking issue", joined)

    def test_failure_verdict_with_a_blocker_passes_review_semantics(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            self.seed_pipeline_artifacts(root)
            artifacts = self.artifacts_for(root)
            paths = self.report_paths(root)
            for source_report, field, value in (
                ("citation_logic", "overall_status", "fail"),
                ("qa_report", "overall_status", "fail"),
                ("red_team", "verdict", "thesis_unsupported"),
            ):
                payload = json.loads(paths[source_report].read_text(encoding="utf-8"))
                payload[field] = value
                payload["issues"] = [self.blocking_issue(source_report)]
                self.write_json(paths[source_report], payload)

            self.assertEqual(
                validate_review_reports(
                    root,
                    artifacts,
                    include_images=False,
                    expected_run_id="test_run",
                ),
                [],
            )

    def test_qualification_verdict_with_a_blocker_passes_review_semantics(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            self.seed_pipeline_artifacts(root)
            artifacts = self.artifacts_for(root)
            red_team = self.report_paths(root)["red_team"]
            payload = json.loads(red_team.read_text(encoding="utf-8"))
            payload["verdict"] = "thesis_needs_qualification"
            payload["issues"] = [self.blocking_issue("red_team")]
            self.write_json(red_team, payload)

            self.assertEqual(
                validate_review_reports(
                    root,
                    artifacts,
                    include_images=False,
                    expected_run_id="test_run",
                ),
                [],
            )

    def test_report_run_id_must_match_the_current_run(self) -> None:
        for source_report in (
            "citation_logic",
            "red_team",
            "qa_report",
            "compression",
            "image_audit",
        ):
            with self.subTest(source_report=source_report):
                with tempfile.TemporaryDirectory() as temp_dir:
                    root = Path(temp_dir)
                    self.seed_pipeline_artifacts(root, include_images=True)
                    artifacts = self.artifacts_for(root)
                    path = self.report_paths(root)[source_report]
                    payload = json.loads(path.read_text(encoding="utf-8"))
                    payload["run_id"] = "other_run"
                    self.write_json(path, payload)

                    joined = "\n".join(
                        validate_review_reports(
                            root,
                            artifacts,
                            include_images=True,
                            expected_run_id="test_run",
                        )
                    )

                    self.assertIn("expected `test_run`, found `other_run`", joined)

    def test_revision_notes_run_id_must_match_the_current_run(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            self.seed_pipeline_artifacts(root)
            artifacts = self.artifacts_for(root)
            notes_path = self.notes_path(root)
            notes = json.loads(notes_path.read_text(encoding="utf-8"))
            notes["run_id"] = "other_run"
            self.write_json(notes_path, notes)

            joined = "\n".join(
                validate_revision_coverage(
                    root,
                    artifacts,
                    include_images=False,
                    expected_run_id="test_run",
                )
            )

            self.assertIn("revision notes run_id mismatch", joined)

    @unittest.skipIf(Draft202012Validator is None, "jsonschema is not installed")
    def test_failure_verdict_schemas_require_a_blocking_issue(self) -> None:
        base_cases = [
            (
                "schemas/citation_logic_report.schema.json",
                {"version": 1, "run_id": "test_run", "overall_status": "fail", "issues": []},
            ),
            (
                "schemas/qa_report.schema.json",
                {"version": 1, "run_id": "test_run", "overall_status": "fail", "issues": []},
            ),
            (
                "schemas/red_team_report.schema.json",
                {
                    "version": 1,
                    "run_id": "test_run",
                    "strongest_counter_thesis": "counter",
                    "alternative_causes": ["cause"],
                    "single_source_that_would_flip": "source",
                    "verdict": "thesis_unsupported",
                    "issues": [],
                },
            ),
            (
                "schemas/red_team_report.schema.json",
                {
                    "version": 1,
                    "run_id": "test_run",
                    "strongest_counter_thesis": "counter",
                    "alternative_causes": ["cause"],
                    "single_source_that_would_flip": "source",
                    "verdict": "thesis_needs_qualification",
                    "issues": [],
                },
            ),
        ]
        repo_root = Path(__file__).resolve().parents[1]
        for schema_rel, base_payload in base_cases:
            for issues in (
                [],
                [
                    {
                        "issue_id": "TEST-1",
                        "priority": "high",
                        "issue": "not blocking",
                        "required_fix": "fix",
                        **(
                            {"issue_type": "missing_source"}
                            if "citation_logic" in schema_rel
                            else {}
                        ),
                    }
                ],
                [
                    {
                        "issue_id": "TEST-1",
                        "priority": "normal",
                        "issue": "not blocking",
                        "required_fix": "fix",
                        **(
                            {"issue_type": "missing_source"}
                            if "citation_logic" in schema_rel
                            else {}
                        ),
                    }
                ],
            ):
                with self.subTest(schema=schema_rel, issues=issues):
                    schema = json.loads((repo_root / schema_rel).read_text(encoding="utf-8"))
                    payload = {**base_payload, "issues": issues}
                    errors = list(Draft202012Validator(schema).iter_errors(payload))
                    self.assertTrue(errors)


if __name__ == "__main__":
    unittest.main()
