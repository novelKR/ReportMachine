from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from scripts.verify_layout import API_UPLOAD_GUARDED_FILES, check_paid_api_upload_surface, check_release_cleanliness


class VerifyLayoutReleaseTests(unittest.TestCase):
    def collect_release_errors(self, root: Path) -> list[str]:
        errors: list[str] = []
        check_release_cleanliness(root, errors)
        return errors

    def write_file(self, root: Path, rel: str, text: str = "generated") -> None:
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")

    def write_guarded_files(self, root: Path) -> None:
        for rel in API_UPLOAD_GUARDED_FILES:
            self.write_file(root, rel, "local runner code\n")

    def collect_paid_api_errors(self, root: Path) -> list[str]:
        errors: list[str] = []
        check_paid_api_upload_surface(root, errors)
        return errors

    def test_paid_api_guard_rejects_openai_file_upload_surface(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            self.write_guarded_files(root)
            self.write_file(
                root,
                "src/reportmachine_runner/cli.py",
                "response = client.post('/v1/responses')\n",
            )

            errors = self.collect_paid_api_errors(root)

            self.assertIn(
                "src/reportmachine_runner/cli.py must not use paid OpenAI API/File Search upload surface: /v1/responses",
                errors,
            )

    def test_paid_api_guard_allows_local_quota_runner_surface(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            self.write_guarded_files(root)
            self.write_file(
                root,
                "src/reportmachine_runner/codex_exec.py",
                "command = ['codex', 'exec', '--image', 'rendered.png']\n",
            )

            errors = self.collect_paid_api_errors(root)

            self.assertEqual(errors, [])

    def test_release_blocks_run_scoped_output_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            self.write_file(root, "outputs/test_run/article.md")

            errors = self.collect_release_errors(root)

            self.assertIn(
                "release-blocked generated artifact exists: outputs/test_run/article.md",
                errors,
            )

    def test_release_blocks_latest_output_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            self.write_file(root, "outputs/latest/source_ledger.csv")

            errors = self.collect_release_errors(root)

            self.assertIn(
                "release-blocked generated artifact exists: outputs/latest/source_ledger.csv",
                errors,
            )

    def test_release_blocks_run_scoped_work_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            self.write_file(root, "work/runs/test_run/drafts/draft_article.md")

            errors = self.collect_release_errors(root)

            self.assertIn(
                "release-blocked generated artifact exists: work/runs/test_run/drafts/draft_article.md",
                errors,
            )

    def test_release_blocks_output_artifacts_when_run_id_matches_blocked_dir_name(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            self.write_file(root, "outputs/build/article.md")
            self.write_file(root, "outputs/dist/article.md")

            errors = self.collect_release_errors(root)

            self.assertIn(
                "release-blocked generated artifact exists: outputs/build/article.md",
                errors,
            )
            self.assertIn(
                "release-blocked generated artifact exists: outputs/dist/article.md",
                errors,
            )

    def test_release_blocks_work_artifacts_when_run_id_matches_blocked_dir_name(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            self.write_file(root, "work/runs/build/drafts/draft_article.md")
            self.write_file(root, "work/runs/dist/drafts/draft_article.md")

            errors = self.collect_release_errors(root)

            self.assertIn(
                "release-blocked generated artifact exists: work/runs/build/drafts/draft_article.md",
                errors,
            )
            self.assertIn(
                "release-blocked generated artifact exists: work/runs/dist/drafts/draft_article.md",
                errors,
            )

    def test_release_still_blocks_root_output_json(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            self.write_file(root, "outputs/article_result.json")

            errors = self.collect_release_errors(root)

            self.assertIn(
                "release-blocked root output JSON exists: outputs/article_result.json",
                errors,
            )

    def test_release_allows_gitkeep_placeholders(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            self.write_file(root, "outputs/.gitkeep", "")
            self.write_file(root, "work/drafts/.gitkeep", "")

            errors = self.collect_release_errors(root)

            self.assertEqual(errors, [])

    def test_release_reports_pattern_matches_under_artifact_dirs_once(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            self.write_file(root, "outputs/latest/.DS_Store")

            errors = self.collect_release_errors(root)

            expected = "release-blocked generated artifact exists: outputs/latest/.DS_Store"
            self.assertEqual(errors.count(expected), 1)

    def test_release_reports_pattern_matches_under_nested_blocked_dir_once(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            self.write_file(root, "outputs/test_run/build/.DS_Store")

            errors = self.collect_release_errors(root)

            expected = "release-blocked generated artifact exists: outputs/test_run/build/.DS_Store"
            self.assertEqual(errors.count(expected), 1)

    def test_release_reports_nested_git_metadata_under_artifact_dirs(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            self.write_file(root, "outputs/test_run/.git/config")

            errors = self.collect_release_errors(root)

            self.assertIn(
                "release-blocked generated artifact exists: outputs/test_run/.git/config",
                errors,
            )

    def test_release_ignores_top_level_git_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            self.write_file(root, ".git/hooks/.DS_Store")

            errors = self.collect_release_errors(root)

            self.assertEqual(errors, [])


if __name__ == "__main__":
    unittest.main()
