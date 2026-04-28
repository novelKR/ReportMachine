from __future__ import annotations

import importlib.util
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

    def prefix_from_stdout(self, process: subprocess.CompletedProcess[str]) -> str:
        for line in process.stdout.splitlines():
            if line.startswith("prefix: "):
                return line.removeprefix("prefix: ")
        self.fail("package_source.py did not print archive prefix")

    def require_image_binder_runtime(self) -> None:
        if importlib.util.find_spec("PIL") is None:
            self.skipTest("Pillow is not installed")

    def test_image_binder_declares_pillow_version_that_supports_sized_default_font(self) -> None:
        import builtins
        from scripts import package_source

        pyproject = (PROJECT_ROOT / "pyproject.toml").read_text(encoding="utf-8")
        requirements_build = (PROJECT_ROOT / "requirements-build.txt").read_text(encoding="utf-8")

        self.assertIn('"Pillow>=10.1.0"', pyproject)
        self.assertIn("Pillow>=10.1.0", requirements_build.splitlines())

        original_import = builtins.__import__

        def block_pillow_import(name, globals=None, locals=None, fromlist=(), level=0):
            if name == "PIL" or name.startswith("PIL."):
                raise ImportError("blocked Pillow import")
            return original_import(name, globals, locals, fromlist, level)

        builtins.__import__ = block_pillow_import
        try:
            with self.assertRaises(package_source.PackageSourceError) as context:
                package_source.require_pillow_image_modules()
        finally:
            builtins.__import__ = original_import

        self.assertIn("Pillow>=10.1.0", str(context.exception))

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

    def test_image_binder_requires_explicit_acknowledgement(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            self.init_fixture_repo(root)

            process = self.run_package(root, "--experimental-image-binder")

            self.assertNotEqual(process.returncode, 0)
            self.assertIn(
                "--experimental-image-binder requires --acknowledge-image-only-internal-distribution",
                process.stderr,
            )
            self.assertFalse((root / "dist").exists())

    def test_image_binder_rejects_zip_output_path(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            self.init_fixture_repo(root)

            process = self.run_package(
                root,
                "--experimental-image-binder",
                "--acknowledge-image-only-internal-distribution",
                "--output",
                "dist/source/not-image.zip",
            )

            self.assertNotEqual(process.returncode, 0)
            self.assertIn(
                "--output must end with .jpg or .jpeg when --experimental-image-binder is used",
                process.stderr,
            )
            self.assertFalse((root / "dist").exists())

    def test_normal_archive_rejects_jpg_output_path(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            self.init_fixture_repo(root)

            process = self.run_package(root, "--output", "dist/source/not-zip.jpg")

            self.assertNotEqual(process.returncode, 0)
            self.assertIn("--output must end with .zip", process.stderr)
            self.assertFalse((root / "dist").exists())

    def test_image_binder_creates_visible_jpg_that_remains_readable_as_zip(self) -> None:
        self.require_image_binder_runtime()
        from PIL import Image
        from scripts import package_source

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            self.init_fixture_repo(root)

            process = self.run_package(
                root,
                "--experimental-image-binder",
                "--acknowledge-image-only-internal-distribution",
            )

            self.assertEqual(process.returncode, 0, process.stdout + process.stderr)
            archives = list((root / "dist" / "source").glob("ReportMachine-source-*-image-binder.jpg"))
            self.assertEqual(len(archives), 1)
            archive_path = archives[0]
            self.assertEqual(archive_path.read_bytes()[:2], b"\xff\xd8")
            with Image.open(archive_path) as image:
                self.assertEqual(image.format, "JPEG")
                self.assertGreaterEqual(image.width, 1000)
                self.assertGreaterEqual(image.height, 700)

            prefix = self.prefix_from_stdout(process)
            names = self.archive_names(archive_path)
            self.assertIn(prefix + "README.md", names)
            self.assertIn(prefix + "scripts/package_source.py", names)
            package_source.validate_source_archive(archive_path, prefix)

    def test_image_binder_notice_text_is_fixed_and_not_user_overridable(self) -> None:
        from scripts import package_source

        self.assertIn("ZIP 파일", package_source.IMAGE_BINDER_KO_NOTICE)
        self.assertIn("File Binder", package_source.IMAGE_BINDER_KO_NOTICE)
        self.assertIn("ZIP file", package_source.IMAGE_BINDER_EN_NOTICE)
        self.assertIn("file-binder", package_source.IMAGE_BINDER_EN_NOTICE)
        self.assertIn("English-only fallback", package_source.IMAGE_BINDER_ENGLISH_ONLY_NOTICE)

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            self.init_fixture_repo(root)
            process = self.run_package(root, "--help")

        self.assertEqual(process.returncode, 0, process.stdout + process.stderr)
        self.assertIn("--experimental-image-binder", process.stdout)
        self.assertIn("--acknowledge-image-only-internal-distribution", process.stdout)
        self.assertNotIn("--image-binder-text", process.stdout)
        self.assertNotIn("--image-binder-cover", process.stdout)

    def test_image_binder_cover_uses_english_only_fallback_without_korean_font(self) -> None:
        self.require_image_binder_runtime()
        from PIL import Image
        from scripts import package_source

        cjk_candidates = package_source.IMAGE_BINDER_FONT_CANDIDATES
        latin_candidates = package_source.IMAGE_BINDER_LATIN_FONT_CANDIDATES
        try:
            package_source.IMAGE_BINDER_FONT_CANDIDATES = []
            package_source.IMAGE_BINDER_LATIN_FONT_CANDIDATES = []
            with tempfile.TemporaryDirectory() as temp_dir:
                cover_path = Path(temp_dir) / "cover.jpg"
                package_source.create_image_binder_cover(cover_path)

                self.assertEqual(cover_path.read_bytes()[:2], b"\xff\xd8")
                with Image.open(cover_path) as image:
                    self.assertEqual(image.format, "JPEG")
                    self.assertGreaterEqual(image.width, 1000)
                    self.assertGreaterEqual(image.height, 700)
        finally:
            package_source.IMAGE_BINDER_FONT_CANDIDATES = cjk_candidates
            package_source.IMAGE_BINDER_LATIN_FONT_CANDIDATES = latin_candidates


if __name__ == "__main__":
    unittest.main()
