#!/usr/bin/env python3

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path, PurePosixPath
from typing import Sequence


class PackageSourceError(RuntimeError):
    pass


def run_command(command: Sequence[str], *, cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        list(command),
        cwd=cwd,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )


def require_success(process: subprocess.CompletedProcess[str], description: str) -> str:
    if process.returncode == 0:
        return process.stdout.strip()
    detail = (process.stderr or process.stdout).strip()
    raise PackageSourceError(f"{description} failed: {detail}")


def find_git_root(cwd: Path) -> Path:
    output = require_success(
        run_command(["git", "rev-parse", "--show-toplevel"], cwd=cwd),
        "git root detection",
    )
    root = Path(output).resolve()
    if not (root / ".article-root").exists() or not (root / "scripts" / "verify_layout.py").exists():
        raise PackageSourceError("current Git repository is not a ReportMachine source tree")
    return root


def head_short_sha(root: Path) -> str:
    return require_success(
        run_command(["git", "rev-parse", "--short", "HEAD"], cwd=root),
        "HEAD revision lookup",
    )


def dirty_status(root: Path) -> str:
    return require_success(
        run_command(["git", "status", "--porcelain", "--untracked-files=normal"], cwd=root),
        "working tree status check",
    )


def normalize_prefix(prefix: str) -> str:
    value = prefix.strip().replace("\\", "/")
    value = value.rstrip("/")
    path = PurePosixPath(value)
    if (
        not value
        or path.is_absolute()
        or any(part in {"", ".", ".."} for part in path.parts)
    ):
        raise PackageSourceError("--prefix must be a relative archive directory name")
    return path.as_posix() + "/"


def resolve_output_path(root: Path, output: str | None, short_sha: str) -> Path:
    if output:
        path = Path(output)
    else:
        path = Path("dist") / "source" / f"ReportMachine-source-{short_sha}.zip"
    if path.suffix.lower() != ".zip":
        raise PackageSourceError("--output must end with .zip")
    return path if path.is_absolute() else root / path


def display_path(root: Path, path: Path) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return str(path)


def archive_root_from_prefix(extract_dir: Path, prefix: str) -> Path:
    return extract_dir.joinpath(*prefix.rstrip("/").split("/"))


def validate_source_archive(archive_path: Path, prefix: str) -> None:
    with tempfile.TemporaryDirectory(prefix="reportmachine-source-verify-") as temp_dir:
        extract_dir = Path(temp_dir)
        with zipfile.ZipFile(archive_path) as archive:
            archive.extractall(extract_dir)

        archive_root = archive_root_from_prefix(extract_dir, prefix)
        verify_script = archive_root / "scripts" / "verify_layout.py"
        if not verify_script.exists():
            raise PackageSourceError("archive validation failed: scripts/verify_layout.py is missing")

        process = run_command([sys.executable, "scripts/verify_layout.py", "--release"], cwd=archive_root)
        if process.returncode != 0:
            output = (process.stdout + process.stderr).strip()
            raise PackageSourceError(f"archive validation failed:\n{output}")


def build_archive(root: Path, output_path: Path, prefix: str) -> None:
    with tempfile.TemporaryDirectory(prefix="reportmachine-source-") as temp_dir:
        temp_zip = Path(temp_dir) / output_path.name
        require_success(
            run_command(
                [
                    "git",
                    "archive",
                    "--format=zip",
                    f"--prefix={prefix}",
                    "--output",
                    str(temp_zip),
                    "HEAD",
                ],
                cwd=root,
            ),
            "git archive",
        )
        validate_source_archive(temp_zip, prefix)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        if output_path.exists():
            output_path.unlink()
        shutil.move(str(temp_zip), output_path)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Create a validated Git HEAD source zip for ReportMachine distribution."
    )
    parser.add_argument("--output", help="Output zip path. Defaults to dist/source/ReportMachine-source-{short_sha}.zip.")
    parser.add_argument("--prefix", help="Archive root directory prefix. Defaults to ReportMachine-source-{short_sha}/.")
    parser.add_argument(
        "--allow-dirty",
        action="store_true",
        help="Allow packaging from a dirty working tree. The archive still uses Git HEAD only.",
    )
    parser.add_argument("--dry-run", action="store_true", help="Print packaging details without creating a zip.")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    try:
        root = find_git_root(Path.cwd())
        status = dirty_status(root)
        if status and not args.allow_dirty:
            raise PackageSourceError(
                "working tree has uncommitted changes; commit/stash them or pass --allow-dirty "
                "to package Git HEAD only\n" + status
            )

        short_sha = head_short_sha(root)
        prefix = normalize_prefix(args.prefix or f"ReportMachine-source-{short_sha}")
        output_path = resolve_output_path(root, args.output, short_sha)

        print(f"repo_root: {root}")
        print(f"head: {short_sha}")
        print(f"prefix: {prefix}")
        print(f"output: {display_path(root, output_path)}")
        if status and args.allow_dirty:
            print("allow_dirty: packaging Git HEAD only; uncommitted changes are excluded")

        if args.dry_run:
            print("dry_run: no archive created")
            return 0

        build_archive(root, output_path, prefix)
        print(f"created: {display_path(root, output_path)}")
        return 0
    except PackageSourceError as exc:
        print(f"package-source failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
