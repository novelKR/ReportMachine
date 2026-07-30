"""Persist per-role agent execution envelopes.

Every runner-invoked role writes an attempt file under
`work/runs/{run_id}/agents/{role}/attempt_NNN.json`, so a completed run can prove which
roles actually ran, on which inputs, with which compiled prompt.
"""

from __future__ import annotations

import json
import os
import stat
import tempfile
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path, PureWindowsPath
from typing import Any, Sequence

from .artifact_validation import validate_run_identity
from .paths import resolve_under_root


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def file_sha256(path: Path) -> str | None:
    if not path.is_file():
        return None
    digest = sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def hash_artifacts(root: Path, rels: Sequence[str]) -> dict[str, str | None]:
    hashes: dict[str, str | None] = {}
    for rel in rels:
        try:
            hashes[rel] = file_sha256(resolve_under_root(root, rel))
        except ValueError:
            hashes[rel] = None
    return hashes


@dataclass(frozen=True)
class AgentAttempt:
    run_id: str
    agent_name: str
    phase: str
    attempt: int
    agent_prompt_sha256: str
    effective_prompt_path: str
    input_artifact_hashes: dict[str, str | None]
    output_artifacts: tuple[str, ...]
    model: str
    model_reasoning_effort: str
    command: tuple[str, ...]
    started_at: str
    lane_id: str | None = None
    completed_at: str | None = None
    exit_code: int | None = None
    status: str = "started"
    result_summary_path: str | None = None
    errors: tuple[str, ...] = ()

    def payload(self) -> dict[str, Any]:
        return {
            "version": 1,
            "run_id": self.run_id,
            "agent_name": self.agent_name,
            "phase": self.phase,
            "lane_id": self.lane_id,
            "attempt": self.attempt,
            "agent_prompt_sha256": self.agent_prompt_sha256,
            "effective_prompt_path": self.effective_prompt_path,
            "input_artifact_hashes": self.input_artifact_hashes,
            "output_artifacts": list(self.output_artifacts),
            "model": self.model,
            "model_reasoning_effort": self.model_reasoning_effort,
            "command": list(self.command),
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "exit_code": self.exit_code,
            "status": self.status,
            "result_summary_path": self.result_summary_path,
            "errors": list(self.errors),
        }


def attempt_dir(agents_dir: str, agent_name: str, lane_id: str | None = None) -> str:
    base = f"{Path(agents_dir).as_posix().rstrip('/')}/{agent_name}"
    return f"{base}/{lane_id}" if lane_id else base


def next_attempt_number(root: Path, agents_dir: str, agent_name: str, lane_id: str | None = None) -> int:
    directory = resolve_under_root(root, attempt_dir(agents_dir, agent_name, lane_id))
    if not directory.is_dir():
        return 1
    existing = sorted(directory.glob("attempt_*.json"))
    return len(existing) + 1


def attempt_path(agents_dir: str, agent_name: str, attempt: int, lane_id: str | None = None) -> str:
    return f"{attempt_dir(agents_dir, agent_name, lane_id)}/attempt_{attempt:03d}.json"


def start_attempt(
    *,
    root: Path,
    agents_dir: str,
    run_id: str,
    agent_name: str,
    phase: str,
    agent_prompt_sha256: str,
    effective_prompt_path: str,
    input_artifact_hashes: dict[str, str | None],
    output_artifacts: Sequence[str],
    model: str,
    model_reasoning_effort: str,
    command: Sequence[str],
    lane_id: str | None = None,
) -> tuple[AgentAttempt, str]:
    attempt_number = next_attempt_number(root, agents_dir, agent_name, lane_id)
    attempt = AgentAttempt(
        run_id=run_id,
        agent_name=agent_name,
        phase=phase,
        attempt=attempt_number,
        agent_prompt_sha256=agent_prompt_sha256,
        effective_prompt_path=effective_prompt_path,
        input_artifact_hashes=dict(input_artifact_hashes),
        output_artifacts=tuple(output_artifacts),
        model=model,
        model_reasoning_effort=model_reasoning_effort,
        command=tuple(command),
        started_at=_now_iso(),
        lane_id=lane_id,
        status="started",
    )
    rel = attempt_path(agents_dir, agent_name, attempt_number, lane_id)
    write_attempt(root, rel, attempt)
    return attempt, rel


def finish_attempt(
    *,
    root: Path,
    rel: str,
    attempt: AgentAttempt,
    exit_code: int,
    errors: Sequence[str] = (),
) -> AgentAttempt:
    status = "completed" if exit_code == 0 and not errors else "failed"
    updated = replace(
        attempt,
        completed_at=_now_iso(),
        exit_code=exit_code,
        status=status,
        errors=tuple(errors),
    )
    write_attempt(root, rel, updated)
    return updated


def write_attempt(root: Path, rel: str, attempt: AgentAttempt) -> None:
    """Atomically replace an attempt without following a role-created target symlink."""

    path = Path(rel)
    windows_path = PureWindowsPath(rel)
    if not rel or path.is_absolute() or windows_path.drive or windows_path.is_absolute():
        raise ValueError(f"attempt path must be repo-relative: {rel}")
    parts = tuple(part for part in path.parts if part not in {"", "."})
    if not parts or any(part == ".." for part in parts):
        raise ValueError(f"attempt path escapes project root: {rel}")

    resolved_root = root.expanduser().resolve()
    parent = resolved_root
    for part in parts[:-1]:
        parent = parent / part
        try:
            mode = parent.lstat().st_mode
        except FileNotFoundError:
            parent.mkdir()
            continue
        if not stat.S_ISDIR(mode):
            parent_rel = parent.relative_to(resolved_root).as_posix()
            raise OSError(f"attempt parent is not a real directory: {parent_rel}")

    target = resolved_root.joinpath(*parts)
    serialized = (
        json.dumps(attempt.payload(), ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    )
    file_descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{target.name}.",
        suffix=".tmp",
        dir=parent,
    )
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(file_descriptor, "w", encoding="utf-8") as handle:
            handle.write(serialized)
            handle.flush()
        os.replace(temporary_path, target)
        if not stat.S_ISREG(target.lstat().st_mode):
            raise OSError(f"attempt replacement is not a regular file: {rel}")
    finally:
        try:
            temporary_path.unlink()
        except FileNotFoundError:
            pass


def collect_agent_executions(root: Path, agents_dir: str) -> list[dict[str, Any]]:
    """Read every attempt envelope for the run manifest."""

    try:
        directory = resolve_under_root(root, agents_dir)
    except ValueError:
        return []
    if not directory.is_dir():
        return []
    executions: list[dict[str, Any]] = []
    for path in sorted(directory.rglob("attempt_*.json")):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        executions.append(
            {
                "agent_name": payload.get("agent_name"),
                "phase": payload.get("phase"),
                "lane_id": payload.get("lane_id"),
                "attempt": payload.get("attempt"),
                "status": payload.get("status"),
                "exit_code": payload.get("exit_code"),
                "agent_prompt_sha256": payload.get("agent_prompt_sha256"),
                "started_at": payload.get("started_at"),
                "completed_at": payload.get("completed_at"),
                "attempt_path": path.relative_to(root.resolve()).as_posix(),
            }
        )
    return executions


def validate_agent_attempt_run_ids(
    root: Path,
    agents_dir: str,
    *,
    expected_run_id: str,
) -> list[str]:
    """Reject attempt envelopes copied from a different run before manifest reuse."""

    try:
        directory = resolve_under_root(root, agents_dir)
    except ValueError as exc:
        return [str(exc)]
    if not directory.is_dir():
        return []
    errors: list[str] = []
    for path in sorted(directory.rglob("attempt_*.json")):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        rel = path.relative_to(root.resolve()).as_posix()
        errors.extend(
            validate_run_identity(
                payload,
                expected_run_id=expected_run_id,
                label="agent attempt",
                artifact=rel,
            )
        )
    return errors
