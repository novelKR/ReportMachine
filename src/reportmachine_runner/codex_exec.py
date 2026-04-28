from __future__ import annotations

import os
import shutil
import subprocess
import time
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from typing import Callable, Sequence

from .artifacts import ArtifactSyncConfig
from .paths import resolve_under_root


@dataclass(frozen=True)
class CodexExecConfig:
    root: Path
    codex_bin: str
    run_id: str
    phase: str
    schema: str
    output: str
    article_output: str
    source_ledger_output: str
    image_ledger_output: str
    claim_ledger_output: str
    preflight_result_output: str
    run_manifest_output: str
    earnings_delta_matrix_output: str
    earnings_delta_matrix_md: str
    work_run_dir: str
    draft_output: str
    image_notes_dir: str
    image_ledger_candidate: str
    source_ingest_manifest: str
    source_candidate_ledger: str
    pdf_index: str
    rendered_images_ledger: str
    latest_dir: str
    web_search: str
    allow_shell_network: bool
    image_inputs: tuple[str, ...]
    structured_output: bool
    json_events: bool
    events_log: str
    stderr_log: str
    artifact_sync: ArtifactSyncConfig
    prompt: str


def ensure_output_directories(config: CodexExecConfig) -> None:
    for value in [
        config.output,
        config.article_output,
        config.source_ledger_output,
        config.image_ledger_output,
        config.claim_ledger_output,
        config.preflight_result_output,
        config.run_manifest_output,
        config.earnings_delta_matrix_output,
        config.earnings_delta_matrix_md,
        config.draft_output,
        config.image_ledger_candidate,
        config.source_ingest_manifest,
        config.source_candidate_ledger,
        config.pdf_index,
        config.rendered_images_ledger,
        config.events_log,
        config.stderr_log,
    ]:
        resolve_under_root(config.root, value).parent.mkdir(parents=True, exist_ok=True)
    work_run_dir = resolve_under_root(config.root, config.work_run_dir)
    for name in ["research", "ledgers", "images", "drafts", "reviews"]:
        (work_run_dir / name).mkdir(parents=True, exist_ok=True)
    for name in ["research/sources", "research/pdf_text", "images/rendered"]:
        (work_run_dir / name).mkdir(parents=True, exist_ok=True)


def build_command(config: CodexExecConfig) -> list[str]:
    command = [
        config.codex_bin,
        "exec",
        "-C",
        str(config.root),
        "--skip-git-repo-check",
        "-c",
        'project_root_markers=[".git",".article-root"]',
        "-c",
        f'web_search="{config.web_search}"',
    ]
    if config.json_events:
        command.append("--json")
    if config.structured_output:
        command.extend(
            [
                "--output-schema",
                config.schema,
                "-o",
                config.output,
            ]
        )
    if config.allow_shell_network:
        command.extend(["-c", "sandbox_workspace_write.network_access=true"])
    if config.image_inputs:
        command.extend(["--image", *config.image_inputs, "--"])
    command.append(config.prompt)
    return command


def run_command(
    command: Sequence[str],
    *,
    cwd: Path,
    snapshot_callback: Callable[[], None] | None = None,
    snapshot_interval_seconds: int = 0,
    stdout_path: Path | None = None,
    stderr_path: Path | None = None,
    append_logs: bool = False,
) -> int:
    env = os.environ.copy()
    env.setdefault("PYTHONUTF8", "1")
    env.setdefault("PYTHONIOENCODING", "utf-8")
    if stdout_path:
        stdout_path.parent.mkdir(parents=True, exist_ok=True)
    if stderr_path:
        stderr_path.parent.mkdir(parents=True, exist_ok=True)
    log_mode = "a" if append_logs else "w"
    stdout_handle = stdout_path.open(log_mode, encoding="utf-8") if stdout_path else None
    stderr_handle = stderr_path.open(log_mode, encoding="utf-8") if stderr_path else None
    stdout_target = stdout_handle if stdout_handle is not None else None
    stderr_target = stderr_handle if stderr_handle is not None else None
    if snapshot_callback is None or snapshot_interval_seconds <= 0:
        try:
            return subprocess.run(
                list(command),
                cwd=cwd,
                env=env,
                text=True,
                check=False,
                stdout=stdout_target,
                stderr=stderr_target,
            ).returncode
        finally:
            if stdout_handle is not None:
                stdout_handle.close()
            if stderr_handle is not None:
                stderr_handle.close()

    process = subprocess.Popen(
        list(command),
        cwd=cwd,
        env=env,
        text=True,
        stdout=stdout_target,
        stderr=stderr_target,
    )
    next_snapshot = time.monotonic() + snapshot_interval_seconds
    try:
        while True:
            status = process.poll()
            now = time.monotonic()
            if now >= next_snapshot:
                snapshot_callback()
                next_snapshot = now + snapshot_interval_seconds
            if status is not None:
                return status
            sleep_for = max(0.1, min(1.0, next_snapshot - now))
            time.sleep(sleep_for)
    except BaseException:
        snapshot_callback()
        raise
    finally:
        if stdout_handle is not None:
            stdout_handle.close()
        if stderr_handle is not None:
            stderr_handle.close()


@dataclass(frozen=True)
class FileSnapshot:
    tracked_hashes: dict[str, str | None]
    protected_untracked_hashes: dict[str, str | None]
    protected_artifact_hashes: dict[str, str | None]
    untracked_files: frozenset[str]
    artifact_files: frozenset[str]
    allowed_prefixes: tuple[str, ...]
    allowed_files: frozenset[str]


def _run_git(root: Path, args: Sequence[str]) -> subprocess.CompletedProcess[str]:
    command = ["git", *args]
    try:
        return subprocess.run(
            command,
            cwd=root,
            check=False,
            capture_output=True,
            text=True,
        )
    except OSError as exc:
        return subprocess.CompletedProcess(command, 127, "", str(exc))


def _normalize_rel(path: str) -> str:
    return Path(path).as_posix().lstrip("./")


def _config_path_as_rel(config: CodexExecConfig, value: str) -> str:
    path = resolve_under_root(config.root, value).resolve()
    try:
        return path.relative_to(config.root.resolve()).as_posix()
    except ValueError:
        return _normalize_rel(value)


def _configured_output_files(config: CodexExecConfig) -> frozenset[str]:
    return frozenset(
        _config_path_as_rel(config, value)
        for value in [
            config.output,
            config.article_output,
            config.source_ledger_output,
            config.image_ledger_output,
            config.claim_ledger_output,
            config.preflight_result_output,
            config.run_manifest_output,
        ]
    )


def _prefix_as_rel(value: str) -> str:
    rel = _normalize_rel(value)
    return f"{rel.rstrip('/')}/"


def _is_allowed(
    path: str,
    prefixes: Sequence[str],
    files: frozenset[str] | None = None,
) -> bool:
    rel = _normalize_rel(path)
    if files and rel in files:
        return True
    return any(rel == prefix.rstrip("/") or rel.startswith(prefix) for prefix in prefixes)


def _file_hash(path: Path) -> str | None:
    if not path.exists():
        return None
    digest = sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _tracked_files(root: Path) -> list[str]:
    result = _run_git(root, ["ls-files"])
    if result.returncode != 0:
        return []
    return [_normalize_rel(line) for line in result.stdout.splitlines() if line.strip()]


def _untracked_files(root: Path) -> frozenset[str]:
    result = _run_git(root, ["status", "--porcelain", "--untracked-files=all"])
    if result.returncode != 0:
        return frozenset()
    files: set[str] = set()
    for line in result.stdout.splitlines():
        if line.startswith("?? "):
            files.add(_normalize_rel(line[3:]))
    return frozenset(files)


def _artifact_files(root: Path) -> frozenset[str]:
    files: set[str] = set()
    for top_level in ["work", "outputs"]:
        base = root / top_level
        if not base.exists():
            continue
        for path in base.rglob("*"):
            if path.is_file():
                files.add(path.relative_to(root).as_posix())
    return frozenset(files)


def make_path_snapshot(
    root: Path,
    *,
    allowed_prefixes: Sequence[str],
    allowed_files: Sequence[str] = (),
) -> FileSnapshot:
    root = root.resolve()
    normalized_prefixes = tuple(_prefix_as_rel(value) for value in allowed_prefixes)
    normalized_files = frozenset(_normalize_rel(value) for value in allowed_files)
    tracked_hashes: dict[str, str | None] = {}
    for rel in _tracked_files(root):
        if _is_allowed(rel, normalized_prefixes, normalized_files):
            continue
        tracked_hashes[rel] = _file_hash(root / rel)

    untracked_files = _untracked_files(root)
    protected_untracked_hashes: dict[str, str | None] = {}
    for rel in untracked_files:
        if _is_allowed(rel, normalized_prefixes, normalized_files):
            continue
        protected_untracked_hashes[rel] = _file_hash(root / rel)

    artifact_files = _artifact_files(root)
    protected_artifact_hashes: dict[str, str | None] = {}
    for rel in artifact_files:
        if _is_allowed(rel, normalized_prefixes, normalized_files):
            continue
        if rel in tracked_hashes or rel in protected_untracked_hashes:
            continue
        protected_artifact_hashes[rel] = _file_hash(root / rel)

    return FileSnapshot(
        tracked_hashes=tracked_hashes,
        protected_untracked_hashes=protected_untracked_hashes,
        protected_artifact_hashes=protected_artifact_hashes,
        untracked_files=untracked_files,
        artifact_files=artifact_files,
        allowed_prefixes=normalized_prefixes,
        allowed_files=normalized_files,
    )


def make_file_snapshot(config: CodexExecConfig) -> FileSnapshot:
    return make_path_snapshot(
        config.root,
        allowed_prefixes=(
            f"work/runs/{config.run_id}/",
            f"outputs/{config.run_id}/",
            f"logs/{config.run_id}/",
            "outputs/latest/",
        ),
        allowed_files=_configured_output_files(config),
    )


def validate_path_snapshot(root: Path, snapshot: FileSnapshot) -> list[str]:
    root = root.resolve()
    errors: list[str] = []
    for rel, before_hash in snapshot.tracked_hashes.items():
        after_hash = _file_hash(root / rel)
        if after_hash != before_hash:
            errors.append(f"protected tracked file changed: {rel}")

    for rel, before_hash in snapshot.protected_untracked_hashes.items():
        after_hash = _file_hash(root / rel)
        if after_hash != before_hash:
            errors.append(f"protected untracked file changed: {rel}")

    for rel, before_hash in snapshot.protected_artifact_hashes.items():
        after_hash = _file_hash(root / rel)
        if after_hash != before_hash:
            errors.append(f"protected work/output file changed: {rel}")

    new_untracked = _untracked_files(root) - snapshot.untracked_files
    for rel in sorted(new_untracked):
        if not _is_allowed(rel, snapshot.allowed_prefixes, snapshot.allowed_files):
            errors.append(f"new file outside generated artifact paths: {rel}")

    new_artifact_files = _artifact_files(root) - snapshot.artifact_files
    for rel in sorted(new_artifact_files):
        if not _is_allowed(rel, snapshot.allowed_prefixes, snapshot.allowed_files):
            errors.append(f"new work/output file outside run-scoped paths: {rel}")
    return errors


def validate_file_snapshot(config: CodexExecConfig, snapshot: FileSnapshot) -> list[str]:
    errors = validate_path_snapshot(config.root, snapshot)

    for source_value in [
        config.output,
        config.article_output,
        config.source_ledger_output,
        config.image_ledger_output,
        config.claim_ledger_output,
        config.preflight_result_output,
        config.run_manifest_output,
    ]:
        source = resolve_under_root(config.root, source_value)
        if not source.is_file():
            errors.append(
                f"missing configured output: {_config_path_as_rel(config, source_value)}"
            )
    return errors


def mirror_latest_outputs(config: CodexExecConfig) -> None:
    latest_dir = resolve_under_root(config.root, config.latest_dir)
    temp_dir = latest_dir.with_name(f".{latest_dir.name}.tmp")
    if temp_dir.exists():
        shutil.rmtree(temp_dir)
    temp_dir.mkdir(parents=True, exist_ok=True)
    for source_value in [
        config.output,
        config.article_output,
        config.source_ledger_output,
        config.image_ledger_output,
        config.claim_ledger_output,
        config.preflight_result_output,
        config.run_manifest_output,
        config.earnings_delta_matrix_output,
        config.earnings_delta_matrix_md,
    ]:
        source = resolve_under_root(config.root, source_value)
        if source_value in {config.earnings_delta_matrix_output, config.earnings_delta_matrix_md} and not source.is_file():
            continue
        if not source.is_file():
            raise FileNotFoundError(
                f"missing configured output: {_config_path_as_rel(config, source_value)}"
            )
        shutil.copy2(source, temp_dir / source.name)
    if latest_dir.exists():
        if latest_dir.is_dir():
            shutil.rmtree(latest_dir)
        else:
            latest_dir.unlink()
    temp_dir.replace(latest_dir)
