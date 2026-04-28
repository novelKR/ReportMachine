#!/usr/bin/env python3

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(description="Build the ReportMachine runner with PyInstaller.")
    parser.add_argument("--mode", choices=["onedir", "onefile"], default="onedir")
    parser.add_argument("--name", default="reportmachine-runner")
    parser.add_argument("--distpath", default="dist")
    parser.add_argument("--workpath", default="build")
    parser.add_argument("--clean", action="store_true", help="Ask PyInstaller to clean cache before building.")
    parser.add_argument("--dry-run", action="store_true", help="Print the PyInstaller command without running it.")
    args = parser.parse_args()

    root = Path(__file__).resolve().parents[1]
    specpath = Path(".tmp") / "pyinstaller" / "spec"

    command = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--noconfirm",
        "--name",
        args.name,
        "--distpath",
        args.distpath,
        "--workpath",
        args.workpath,
        "--specpath",
        specpath.as_posix(),
        "--paths",
        "src",
    ]
    if args.clean:
        command.append("--clean")
    command.append("--onefile" if args.mode == "onefile" else "--onedir")
    command.append("src/reportmachine_runner/pyinstaller_entry.py")

    if args.dry_run:
        print("cwd:", root)
        print("command:", command)
        return 0

    env = os.environ.copy()
    env.setdefault("PYINSTALLER_CONFIG_DIR", str(root / ".tmp" / "pyinstaller"))
    (root / ".tmp" / "pyinstaller").mkdir(parents=True, exist_ok=True)
    (root / specpath).mkdir(parents=True, exist_ok=True)
    return subprocess.run(command, cwd=root, env=env, text=True, check=False).returncode


if __name__ == "__main__":
    raise SystemExit(main())
