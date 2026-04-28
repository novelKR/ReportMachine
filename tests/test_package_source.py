from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class PackageSourceTests(unittest.TestCase):
    def run_git(self, root: Path, args: list[str], *, check: bool = True) -> subprocess.CompletedProcess[str]:
        process = subprocess.run(
            ["git", *args],
            cwd=root,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        if check and process.returncode != 0:
            self.fail(process.stderr or process.stdout)
        return process

    def copy_source_fixture(self, root: Path) -> None:
        process = subprocess.run(
            ["git", "ls-files", "-z", "--cached"],
            cwd=PROJECT_ROOT,
            check=True,
            stdout=subprocess.PIPE,
        )
        rels = {raw_rel for raw_rel in process.stdout.decode("utf-8").split("\0") if raw_rel}
        for rel in [
            "scripts/package_source.py",
            "tests/test_package_source.py",
            "schemas/preflight_result.schema.json",
            "schemas/claim_ledger.schema.json",
            "src/reportmachine_runner/audit.py",
            "src/reportmachine_runner/manifest.py",
            "src/reportmachine_runner/preflight.py",
            "src/reportmachine_runner/schema_validation.py",
        ]:
            if (PROJECT_ROOT / rel).exists():
                rels.add(rel)
        for raw_rel in sorted(rels):
            source = PROJECT_ROOT / raw_rel
            if not source.is_file():
                continue
            target = root / raw_rel
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)

    def init_fixture_repo(self, root: Path) -> None:
        self.copy_source_fixture(root)
        self.run_git(root, ["init"])
        self.run_git(root, ["add", "."])
        self.run_git(
            root,
            [
                "-c",
                "user.name=ReportMachine Tests",
                "-c",
                "user.email=tests@example.com",
                "commit",
                "-m",
                "initial",
            ],
        )

    def run_package(self, root: Path, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, "scripts/package_source.py", *args],
            cwd=root,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )

    def archive_names(self, archive_path: Path) -> set[str]:
        with zipfile.ZipFile(archive_path) as archive:
            return set(archive.namelist())

    def test_clean_repo_creates_default_archive_with_prefix_and_tracked_source(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            self.init_fixture_repo(root)

            process = self.run_package(root)

            self.assertEqual(process.returncode, 0, process.stdout + process.stderr)
            archives = list((root / "dist" / "source").glob("ReportMachine-source-*.zip"))
            self.assertEqual(len(archives), 1)
            prefix = archives[0].stem + "/"
            names = self.archive_names(archives[0])
            self.assertIn(prefix + "README.md", names)
            self.assertIn(prefix + "scripts/package_source.py", names)
            self.assertIn(prefix + "scripts/verify_layout.py", names)

    def test_ignored_generated_files_and_local_config_are_omitted(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            self.init_fixture_repo(root)
            for rel in [
                "reportmachine.local.toml",
                "reportmachine-runner.spec",
                "build/junk.txt",
                "dist/junk.txt",
                ".tmp/junk.txt",
                "outputs/test_run/article.md",
                "work/runs/test_run/drafts/draft_article.md",
            ]:
                path = root / rel
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("local generated file\n", encoding="utf-8")

            process = self.run_package(root)

            self.assertEqual(process.returncode, 0, process.stdout + process.stderr)
            archive_path = next((root / "dist" / "source").glob("ReportMachine-source-*.zip"))
            names = self.archive_names(archive_path)
            self.assertFalse(any(name.endswith("reportmachine.local.toml") for name in names))
            self.assertFalse(any(name.endswith("reportmachine-runner.spec") for name in names))
            self.assertFalse(any("/build/" in name or "/dist/" in name or "/.tmp/" in name for name in names))
            self.assertFalse(any("/outputs/test_run/" in name or "/work/runs/test_run/" in name for name in names))

    def test_dirty_worktree_fails_by_default_and_allow_dirty_packages_head(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            self.init_fixture_repo(root)
            committed_readme = (root / "README.md").read_text(encoding="utf-8")
            (root / "README.md").write_text("dirty README\n", encoding="utf-8")

            failed = self.run_package(root)
            self.assertNotEqual(failed.returncode, 0)
            self.assertIn("working tree has uncommitted changes", failed.stderr)

            allowed = self.run_package(root, "--allow-dirty")
            self.assertEqual(allowed.returncode, 0, allowed.stdout + allowed.stderr)
            archive_path = next((root / "dist" / "source").glob("ReportMachine-source-*.zip"))
            prefix = archive_path.stem + "/"
            with zipfile.ZipFile(archive_path) as archive:
                archived_readme = archive.read(prefix + "README.md").decode("utf-8")
            self.assertEqual(archived_readme, committed_readme)

    def test_tracked_release_blocked_spec_fails_archive_validation(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            self.init_fixture_repo(root)
            (root / "reportmachine-runner.spec").write_text("absolute local spec\n", encoding="utf-8")
            self.run_git(root, ["add", "-f", "reportmachine-runner.spec"])
            self.run_git(
                root,
                [
                    "-c",
                    "user.name=ReportMachine Tests",
                    "-c",
                    "user.email=tests@example.com",
                    "commit",
                    "-m",
                    "add blocked spec",
                ],
            )

            process = self.run_package(root)

            self.assertNotEqual(process.returncode, 0)
            self.assertIn("release-blocked generated artifact exists: reportmachine-runner.spec", process.stderr)
            self.assertFalse((root / "dist" / "source").exists())

    def test_dry_run_does_not_create_output_directory(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            self.init_fixture_repo(root)

            process = self.run_package(root, "--dry-run")

            self.assertEqual(process.returncode, 0, process.stdout + process.stderr)
            self.assertIn("dry_run: no archive created", process.stdout)
            self.assertFalse((root / "dist").exists())


if __name__ == "__main__":
    unittest.main()
