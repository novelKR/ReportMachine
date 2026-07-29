from __future__ import annotations

import os
import re
from pathlib import Path, PureWindowsPath

ROOT_MARKERS = (".git", ".article-root", "AGENTS.md")
RUN_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")
RUN_ID_REQUIREMENT = (
    "must start with an ASCII letter or digit and may also contain underscore, hyphen, and dot"
)
RESERVED_RUN_IDS = frozenset({"latest"})


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


def validate_run_id(value: str, label: str = "--run-id") -> str:
    if not RUN_ID_PATTERN.fullmatch(value):
        raise ValueError(f"{label} {RUN_ID_REQUIREMENT}")
    if value.casefold() in RESERVED_RUN_IDS:
        raise ValueError(f"{label} must not use the reserved name '{value.casefold()}'")
    return value


def resolve_external_root(project_root: Path, value: str) -> Path:
    if not value:
        raise ValueError("External artifact root must not be empty")

    windows_path = PureWindowsPath(value)
    if windows_path.drive and os.name != "nt":
        raise ValueError(f"Windows drive paths are not supported on this OS: {value}")

    path = Path(value).expanduser()
    if not path.is_absolute():
        raise ValueError(f"External artifact root must be an absolute path or use '~': {value}")

    project_root = project_root.expanduser().resolve()
    resolved = path.resolve()
    try:
        resolved.relative_to(project_root)
    except ValueError:
        return resolved
    raise ValueError(f"External artifact root must be outside the project root: {value}")


def as_posix_arg(value: str) -> str:
    return Path(value).as_posix()
