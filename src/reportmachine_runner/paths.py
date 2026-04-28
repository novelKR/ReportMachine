from __future__ import annotations

import os
from pathlib import Path

ROOT_MARKERS = (".article-root", "AGENTS.md")


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
    path = Path(value).expanduser()
    if path.is_absolute():
        return path
    return root / path


def as_posix_arg(value: str) -> str:
    return Path(value).as_posix()
