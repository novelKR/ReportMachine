from __future__ import annotations

import os
from pathlib import Path, PureWindowsPath

ROOT_MARKERS = (".git", ".article-root", "AGENTS.md")


def find_project_root(start: Path | None = None) -> Path:
    env_root = os.environ.get("REPORTMACHINE_ROOT")
    if env_root:
        return Path(env_root).expanduser().resolve()

    current = (start or Path.cwd()).expanduser().resolve()
    candidates = (current, *current.parents)
    for candidate in candidates:
        if any((candidate / marker).exists() for marker in ROOT_MARKERS):
            return candidate

    return current


def resolve_under_root(root: Path, value: str) -> Path:
    if not value:
        raise ValueError("Path value must not be empty")

    windows_path = PureWindowsPath(value)
    if windows_path.drive:
        raise ValueError(f"Windows drive paths are not allowed: {value}")

    root = root.expanduser().resolve()
    path = Path(value).expanduser()
    if path.is_absolute():
        raise ValueError(f"Absolute paths are not allowed: {value}")

    resolved = (root / path).resolve()
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise ValueError(f"Path escapes project root: {value}") from exc
    return resolved


def as_posix_arg(value: str) -> str:
    return Path(value).as_posix()
