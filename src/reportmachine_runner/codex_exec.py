from __future__ import annotations

import os
import signal
import shutil
import stat
import subprocess
import time
from dataclasses import dataclass, field
from hashlib import sha256
from pathlib import Path, PureWindowsPath
from typing import Callable, Sequence

from .artifacts import ArtifactSyncConfig
from .execution_control import active_writer_lock_fd
from .paths import resolve_under_root


_ROLE_GROUP_TERM_TIMEOUT_SECONDS = 5.0
_ROLE_GROUP_KILL_TIMEOUT_SECONDS = 5.0
_ROLE_GROUP_POLL_SECONDS = 0.05

# One runner-owned contract drives both promotion and read-only membership checks.
# Tuple items are (run_manifest.paths key, CodexExecConfig field name).
LATEST_REQUIRED_OUTPUTS = (
    ("article_result", "output"),
    ("article", "article_output"),
    ("source_ledger", "source_ledger_output"),
    ("image_ledger", "image_ledger_output"),
    ("claim_ledger", "claim_ledger_output"),
    ("preflight_result", "preflight_result_output"),
    ("run_manifest", "run_manifest_output"),
)
LATEST_OPTIONAL_OUTPUTS = (
    ("earnings_delta_matrix", "earnings_delta_matrix_output"),
    ("earnings_delta_matrix_md", "earnings_delta_matrix_md"),
)


class RoleProcessCleanupError(RuntimeError):
    """Raised when the runner cannot prove that a role process group is gone."""


class _RoleSigtermTermination(SystemExit):
    """Catchable role-only representation of the runner receiving SIGTERM."""

    def __init__(self, signum: int) -> None:
        self.signum = signum
        self.returncode = 128 + signum
        super().__init__(self.returncode)


def _active_writer_pass_fds() -> tuple[int, ...]:
    """Return the one POSIX lease descriptor admitted to supported child commands."""

    writer_lock_fd = active_writer_lock_fd()
    return (writer_lock_fd,) if writer_lock_fd is not None else ()


class _RoleSigtermGuard:
    """Latch runner SIGTERM until one complete role boundary has settled."""

    def __init__(self) -> None:
        self._previous_handler: object | None = None
        self._installed = False
        self._raise_during_execution = False
        self._termination: _RoleSigtermTermination | None = None

    @property
    def installed(self) -> bool:
        return self._installed

    @property
    def termination(self) -> _RoleSigtermTermination | None:
        return self._termination

    @property
    def returncode(self) -> int | None:
        return self._termination.returncode if self._termination is not None else None

    def _handle_sigterm(self, signum: int, _frame: object) -> None:
        if self._termination is not None:
            return
        self._termination = _RoleSigtermTermination(signum)
        if self._raise_during_execution:
            raise self._termination

    def install(self) -> None:
        if self._installed:
            return
        self._previous_handler = signal.getsignal(signal.SIGTERM)
        # Mark the guard installed before signal.signal(). A test hook or a real signal
        # may invoke the new handler as soon as the OS-level handler changes.
        self._installed = True
        try:
            signal.signal(signal.SIGTERM, self._handle_sigterm)
        except BaseException:
            self._installed = False
            raise

    def arm_execution(self) -> None:
        self._raise_during_execution = True
        if self._termination is not None:
            self._raise_during_execution = False
            raise self._termination

    def begin_cleanup(self) -> None:
        self._raise_during_execution = False

    def restore(self) -> int | None:
        self._raise_during_execution = False
        if self._installed:
            previous_handler = self._previous_handler
            signal.signal(signal.SIGTERM, previous_handler)  # type: ignore[arg-type]
            self._installed = False
        return self.returncode


class RoleCommand(list[str]):
    """Role command plus the SIGTERM latch shared with its PhaseRunner boundary."""

    def __init__(self, values: Sequence[str] = ()) -> None:
        super().__init__(values)
        self._sigterm_guard = _RoleSigtermGuard()
        self._phase_retains_sigterm_guard = False

    @property
    def sigterm_returncode(self) -> int | None:
        return self._sigterm_guard.returncode

    def retain_sigterm_guard(self) -> None:
        self._phase_retains_sigterm_guard = True
        self._sigterm_guard.install()

    def release_sigterm_guard(self) -> int | None:
        try:
            return self._sigterm_guard.restore()
        finally:
            self._phase_retains_sigterm_guard = False


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
    role: str = ""
    role_model: str = ""
    role_reasoning_effort: str = ""
    role_prompt_path: str = ""
    role_output_artifacts: tuple[str, ...] = ()


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
        config.source_ingest_manifest,
        config.source_candidate_ledger,
        config.pdf_index,
        config.rendered_images_ledger,
        config.events_log,
        config.stderr_log,
    ]:
        resolve_under_root(config.root, value).parent.mkdir(parents=True, exist_ok=True)
    work_run_dir = resolve_under_root(config.root, config.work_run_dir)
    for name in [
        "research",
        "ledgers",
        "images",
        "drafts",
        "reviews",
        "intake",
        "evidence",
        "agents",
        "contracts",
    ]:
        (work_run_dir / name).mkdir(parents=True, exist_ok=True)
    for name in [
        "research/sources",
        "research/pdf_text",
        "research/lanes",
        "research/extracted_text",
        "images/rendered",
    ]:
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


def build_role_command(config: CodexExecConfig) -> RoleCommand:
    """Build a codex exec command for a single pipeline role.

    Role invocations pin the model and reasoning effort from the role registry so that
    high-recall discovery and high-precision adjudication do not share one model setting.
    """

    command = RoleCommand(
        [
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
    )
    if config.role_model:
        command.extend(["-c", f'model="{config.role_model}"'])
    if config.role_reasoning_effort:
        command.extend(["-c", f'model_reasoning_effort="{config.role_reasoning_effort}"'])
    if config.json_events:
        command.append("--json")
    if config.structured_output:
        command.extend(["--output-schema", config.schema, "-o", config.output])
    if config.allow_shell_network:
        command.extend(["-c", "sandbox_workspace_write.network_access=true"])
    if config.image_inputs:
        command.extend(["--image", *config.image_inputs, "--"])
    command.append(config.prompt)
    return command


def _role_process_group_exists(process_group_id: int) -> bool:
    try:
        os.killpg(process_group_id, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _wait_for_role_process_group_exit(
    process: subprocess.Popen[str],
    process_group_id: int,
    timeout_seconds: float,
) -> bool:
    deadline = time.monotonic() + max(0.0, timeout_seconds)
    while True:
        # Reap the session leader while polling. Otherwise its zombie alone can keep the
        # process group observable even after every descendant has exited.
        process.poll()
        if not _role_process_group_exists(process_group_id):
            return True
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return False
        time.sleep(min(_ROLE_GROUP_POLL_SECONDS, remaining))


def _cleanup_role_process_group(process: subprocess.Popen[str]) -> None:
    """Terminate, reap, and verify a dedicated role process group within fixed bounds."""

    process_group_id = process.pid
    cleanup_exceptions: list[BaseException] = []
    try:
        os.killpg(process_group_id, signal.SIGTERM)
    except ProcessLookupError:
        pass
    except BaseException as exc:
        cleanup_exceptions.append(exc)

    try:
        gone = _wait_for_role_process_group_exit(
            process,
            process_group_id,
            _ROLE_GROUP_TERM_TIMEOUT_SECONDS,
        )
    except BaseException as exc:
        cleanup_exceptions.append(exc)
        gone = False

    if not gone:
        try:
            os.killpg(process_group_id, signal.SIGKILL)
        except ProcessLookupError:
            pass
        except BaseException as exc:
            cleanup_exceptions.append(exc)
        try:
            gone = _wait_for_role_process_group_exit(
                process,
                process_group_id,
                _ROLE_GROUP_KILL_TIMEOUT_SECONDS,
            )
        except BaseException as exc:
            cleanup_exceptions.append(exc)
            try:
                process.poll()
                gone = not _role_process_group_exists(process_group_id)
            except BaseException as verify_exc:
                cleanup_exceptions.append(verify_exc)
                gone = False

    if not gone:
        detail = "; ".join(
            f"{type(exc).__name__}: {exc}" if str(exc) else type(exc).__name__
            for exc in cleanup_exceptions
        )
        suffix = f" ({detail})" if detail else ""
        raise RoleProcessCleanupError(
            "role process-group cleanup could not verify that all descendants exited "
            f"after SIGTERM and SIGKILL: pgid={process_group_id}{suffix}"
        )

    # Cleanup was proven safe, but an interrupt or execution error that landed inside the
    # bounded cleanup must still propagate to PhaseRunner after the proof completes.
    if cleanup_exceptions:
        first = cleanup_exceptions[0]
        raise first.with_traceback(first.__traceback__)


def _cleanup_error_with_subordinates(
    cleanup_error: RoleProcessCleanupError,
    subordinate_errors: Sequence[tuple[str, BaseException]],
) -> RoleProcessCleanupError:
    if not subordinate_errors:
        return cleanup_error
    details = "; ".join(
        f"{label} raised {type(exc).__name__}"
        + (f": {exc}" if str(exc) else "")
        for label, exc in subordinate_errors
    )
    return RoleProcessCleanupError(f"{cleanup_error}; subordinate errors: {details}")


def _run_role_command(
    command: RoleCommand,
    *,
    cwd: Path,
    snapshot_callback: Callable[[], None] | None = None,
    snapshot_interval_seconds: int = 0,
    stdout_path: Path | None = None,
    stderr_path: Path | None = None,
    append_logs: bool = False,
) -> int:
    if os.name != "posix":
        raise RuntimeError("pipeline role execution requires POSIX process-group isolation")

    env = os.environ.copy()
    env.setdefault("PYTHONUTF8", "1")
    env.setdefault("PYTHONIOENCODING", "utf-8")
    env["GIT_OPTIONAL_LOCKS"] = "0"
    if stdout_path:
        stdout_path.parent.mkdir(parents=True, exist_ok=True)
    if stderr_path:
        stderr_path.parent.mkdir(parents=True, exist_ok=True)
    log_mode = "a" if append_logs else "w"
    stdout_handle = stdout_path.open(log_mode, encoding="utf-8") if stdout_path else None
    stderr_handle = stderr_path.open(log_mode, encoding="utf-8") if stderr_path else None
    stdout_target = stdout_handle if stdout_handle is not None else None
    stderr_target = stderr_handle if stderr_handle is not None else None
    process: subprocess.Popen[str] | None = None
    status: int | None = None
    execution_error: BaseException | None = None
    cleanup_error: BaseException | None = None
    failure_snapshot_error: BaseException | None = None
    close_errors: list[tuple[str, BaseException]] = []
    execution_interrupted = False
    cleanup_attempted = False
    periodic = snapshot_callback is not None and snapshot_interval_seconds > 0
    sigterm_guard = command._sigterm_guard
    owns_sigterm_guard = not command._phase_retains_sigterm_guard

    def cleanup_role_once() -> None:
        nonlocal cleanup_attempted, cleanup_error
        sigterm_guard.begin_cleanup()
        if cleanup_attempted:
            return
        cleanup_attempted = True
        if process is not None:
            try:
                _cleanup_role_process_group(process)
            except BaseException as exc:
                cleanup_error = exc

    try:
        try:
            sigterm_guard.install()
            if sigterm_guard.termination is not None:
                raise sigterm_guard.termination
            try:
                process = subprocess.Popen(
                    list(command),
                    cwd=cwd,
                    env=env,
                    text=True,
                    stdout=stdout_target,
                    stderr=stderr_target,
                    start_new_session=True,
                    pass_fds=_active_writer_pass_fds(),
                )
                # A signal during Popen is latched without unwinding before assignment.
                # Once the process object is owned, arming either raises the pending
                # termination into the normal cleanup path or begins live interruption.
                sigterm_guard.arm_execution()
                try:
                    if not periodic:
                        status = process.wait()
                    else:
                        assert snapshot_callback is not None
                        next_snapshot = time.monotonic() + snapshot_interval_seconds
                        while True:
                            status = process.poll()
                            now = time.monotonic()
                            if now >= next_snapshot:
                                snapshot_callback()
                                next_snapshot = now + snapshot_interval_seconds
                            if status is not None:
                                break
                            sleep_for = max(0.1, min(1.0, next_snapshot - now))
                            time.sleep(sleep_for)
                except BaseException as exc:
                    execution_error = exc
                    execution_interrupted = True
            except BaseException as exc:
                execution_error = exc
                execution_interrupted = process is not None
            finally:
                cleanup_role_once()
        except _RoleSigtermTermination as exc:
            if execution_error is None:
                execution_error = exc
            execution_interrupted = process is not None
            cleanup_role_once()

        # Preserve the existing periodic failure snapshot: it runs exactly once after
        # process cleanup when execution was interrupted.
        if execution_interrupted and periodic:
            assert snapshot_callback is not None
            try:
                snapshot_callback()
            except BaseException as exc:
                failure_snapshot_error = exc
    finally:
        try:
            if stdout_handle is not None:
                try:
                    stdout_handle.close()
                except BaseException as exc:
                    close_errors.append(("stdout log close", exc))
            if stderr_handle is not None:
                try:
                    stderr_handle.close()
                except BaseException as exc:
                    close_errors.append(("stderr log close", exc))
        finally:
            if owns_sigterm_guard:
                sigterm_guard.restore()

    if isinstance(cleanup_error, RoleProcessCleanupError):
        subordinate_errors: list[tuple[str, BaseException]] = []
        if execution_error is not None:
            subordinate_errors.append(("role execution", execution_error))
        if failure_snapshot_error is not None:
            subordinate_errors.append(("periodic failure snapshot", failure_snapshot_error))
        sigterm_termination = sigterm_guard.termination
        if sigterm_termination is not None and execution_error is not sigterm_termination:
            subordinate_errors.append(("runner SIGTERM", sigterm_termination))
        subordinate_errors.extend(close_errors)
        primary = _cleanup_error_with_subordinates(cleanup_error, subordinate_errors)
        if primary is cleanup_error:
            raise cleanup_error.with_traceback(cleanup_error.__traceback__)
        raise primary.with_traceback(cleanup_error.__traceback__) from cleanup_error
    if close_errors:
        close_error = close_errors[0][1]
        raise close_error.with_traceback(close_error.__traceback__)
    if failure_snapshot_error is not None:
        raise failure_snapshot_error.with_traceback(failure_snapshot_error.__traceback__)
    if cleanup_error is not None:
        raise cleanup_error.with_traceback(cleanup_error.__traceback__)
    if isinstance(execution_error, _RoleSigtermTermination):
        return execution_error.returncode
    if execution_error is not None:
        raise execution_error.with_traceback(execution_error.__traceback__)
    if sigterm_guard.returncode is not None:
        return sigterm_guard.returncode
    assert status is not None
    return status


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
    if isinstance(command, RoleCommand):
        return _run_role_command(
            command,
            cwd=cwd,
            snapshot_callback=snapshot_callback,
            snapshot_interval_seconds=snapshot_interval_seconds,
            stdout_path=stdout_path,
            stderr_path=stderr_path,
            append_logs=append_logs,
        )

    env = os.environ.copy()
    env.setdefault("PYTHONUTF8", "1")
    env.setdefault("PYTHONIOENCODING", "utf-8")
    # Read-only Git commands otherwise opportunistically refresh .git/index and look like
    # a role write outside the exact allowlist. Explicit Git writes remain detectable.
    env["GIT_OPTIONAL_LOCKS"] = "0"
    if stdout_path:
        stdout_path.parent.mkdir(parents=True, exist_ok=True)
    if stderr_path:
        stderr_path.parent.mkdir(parents=True, exist_ok=True)
    log_mode = "a" if append_logs else "w"
    stdout_handle = stdout_path.open(log_mode, encoding="utf-8") if stdout_path else None
    stderr_handle = stderr_path.open(log_mode, encoding="utf-8") if stderr_path else None
    stdout_target = stdout_handle if stdout_handle is not None else None
    stderr_target = stderr_handle if stderr_handle is not None else None
    inherited_lock_kwargs: dict[str, object] = {}
    if os.name == "posix":
        inherited_lock_kwargs["pass_fds"] = _active_writer_pass_fds()
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
                **inherited_lock_kwargs,
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
        **inherited_lock_kwargs,
    )
    try:
        next_snapshot = time.monotonic() + snapshot_interval_seconds
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
        # A catchable interrupt must not leave the child alive to keep writing after the
        # caller validates or restores the workspace. This is deliberately bounded; host
        # crashes and uncatchable termination remain outside the runner's recovery model.
        try:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    try:
                        process.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        pass
        except OSError:
            pass
        finally:
            snapshot_callback()
        raise
    finally:
        if stdout_handle is not None:
            stdout_handle.close()
        if stderr_handle is not None:
            stderr_handle.close()


@dataclass(frozen=True)
class RoleTreeEntry:
    kind: str
    value: str | None = None


@dataclass(frozen=True)
class RunnerLogBaseline:
    data: bytes
    device: int
    inode: int
    link_count: int


@dataclass(frozen=True)
class RoleWriteSnapshot:
    root: Path
    entries: dict[str, RoleTreeEntry]
    allowed_files: frozenset[str]
    runner_logs: dict[str, RunnerLogBaseline]


@dataclass(frozen=True)
class LatestTreeEntry:
    kind: str
    data: bytes | str | None = None


@dataclass(frozen=True)
class LatestTreeSnapshot:
    root: Path
    existed: bool
    entries: dict[str, LatestTreeEntry]


def _lexical_repo_rel(value: str) -> str:
    """Normalize a declared repo path without resolving or following symlinks."""

    if not value:
        raise ValueError("Path value must not be empty")
    windows_path = PureWindowsPath(value)
    path = Path(value)
    if windows_path.drive or windows_path.is_absolute():
        raise ValueError(f"Absolute paths are not allowed: {value}")
    if path.is_absolute():
        raise ValueError(f"Absolute paths are not allowed: {value}")
    parts = tuple(part for part in path.parts if part not in {"", "."})
    if not parts or any(part == ".." for part in parts):
        raise ValueError(f"Path escapes project root: {value}")
    return Path(*parts).as_posix()


def _entry_kind(mode: int) -> str:
    if stat.S_ISREG(mode):
        return "regular"
    if stat.S_ISDIR(mode):
        return "directory"
    if stat.S_ISLNK(mode):
        return "symlink"
    if stat.S_ISFIFO(mode):
        return "fifo"
    if stat.S_ISSOCK(mode):
        return "socket"
    if stat.S_ISCHR(mode):
        return "character-device"
    if stat.S_ISBLK(mode):
        return "block-device"
    return "special"


def _ensure_real_parent(root: Path, rel: str) -> list[str]:
    """Create a target's parents while refusing symlink or non-directory parents."""

    errors: list[str] = []
    current = root.resolve()
    for part in Path(rel).parts[:-1]:
        current = current / part
        try:
            mode = current.lstat().st_mode
        except FileNotFoundError:
            try:
                current.mkdir()
            except OSError as exc:
                errors.append(f"failed to create role output parent {current}: {exc}")
                break
            continue
        except OSError as exc:
            errors.append(f"failed to inspect role output parent {current}: {exc}")
            break
        if not stat.S_ISDIR(mode):
            errors.append(
                f"role output parent is not a real directory: "
                f"{current.relative_to(root.resolve()).as_posix()}"
            )
            break
    return errors


def _target_type_error(root: Path, rel: str, *, require_present: bool, label: str) -> str | None:
    try:
        _check_real_parent_chain(root, rel, label=label)
    except ValueError as exc:
        return str(exc)
    target = root.resolve() / rel
    try:
        mode = target.lstat().st_mode
    except FileNotFoundError:
        return f"missing {label}: {rel}" if require_present else None
    except OSError as exc:
        return f"failed to inspect {label} {rel}: {exc}"
    kind = _entry_kind(mode)
    if kind != "regular":
        return f"{label} must be a regular file, found {kind}: {rel}"
    return None


def _runner_side_effect_values(config: CodexExecConfig) -> tuple[str, ...]:
    if not config.json_events:
        return ()
    return (config.events_log, config.stderr_log)


def _ensure_runner_log_file(root: Path, rel: str) -> str | None:
    """Create one missing runner-owned log without following an existing symlink."""

    path = root.resolve() / rel
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    file_descriptor: int | None = None
    try:
        file_descriptor = os.open(path, flags, 0o666)
    except FileExistsError:
        pass
    except OSError as exc:
        return f"failed to create runner log file {rel}: {exc}"
    finally:
        if file_descriptor is not None:
            os.close(file_descriptor)
    type_error = _target_type_error(
        root,
        rel,
        require_present=True,
        label="runner log file",
    )
    if type_error:
        return type_error
    try:
        link_count = path.lstat().st_nlink
    except OSError as exc:
        return f"failed to inspect runner log file {rel}: {exc}"
    if link_count != 1:
        return (
            "runner log file must have exactly one hard link, "
            f"found {link_count}: {rel}"
        )
    return None


def prepare_role_invocation(config: CodexExecConfig) -> list[str]:
    """Prepare exact role outputs and reject ambiguous or unsafe output targets."""

    if config.role and os.name != "posix":
        return [
            "pipeline role execution requires POSIX process-group isolation; "
            f"unsupported platform: os.name={os.name}"
        ]

    errors: list[str] = []
    outputs: list[str] = []
    try:
        for value in config.role_output_artifacts:
            rel = _lexical_repo_rel(value)
            if rel in outputs:
                errors.append(f"duplicate declared role output: {rel}")
            else:
                outputs.append(rel)
        structured_output = _lexical_repo_rel(config.output)
        if config.structured_output and structured_output not in outputs:
            errors.append(
                f"structured -o output is not a declared role output: {structured_output}"
            )
    except ValueError as exc:
        errors.append(str(exc))

    runner_files: list[str] = []
    for value in _runner_side_effect_values(config):
        try:
            rel = _lexical_repo_rel(value)
        except ValueError as exc:
            errors.append(str(exc))
            continue
        if rel in runner_files:
            continue
        runner_files.append(rel)
        if rel in outputs:
            errors.append(f"role output collides with runner log file: {rel}")

    prohibited: set[str] = set()
    declared_paths = [*outputs, *runner_files]
    for index, left in enumerate(declared_paths):
        for right in declared_paths[index + 1 :]:
            if left.startswith(f"{right}/") or right.startswith(f"{left}/"):
                prohibited.update({left, right})
                errors.append(
                    "role outputs and runner files cannot overlap as ancestor and "
                    f"descendant paths: {left}, {right}"
                )
    try:
        latest_rel = _lexical_repo_rel(config.latest_dir)
        for rel in (*outputs, *runner_files):
            if (
                rel == latest_rel
                or rel.startswith(f"{latest_rel}/")
                or latest_rel.startswith(f"{rel}/")
            ):
                prohibited.add(rel)
                errors.append(f"outputs/latest is never writable by a role: {rel}")
    except ValueError as exc:
        errors.append(str(exc))

    for rel in (*outputs, *runner_files):
        if rel in prohibited:
            continue
        parent_errors = _ensure_real_parent(config.root, rel)
        errors.extend(parent_errors)
        if parent_errors:
            continue
        is_runner_file = rel in runner_files
        error = _target_type_error(
            config.root,
            rel,
            require_present=False,
            label="runner log file" if is_runner_file else "declared role output",
        )
        if error:
            errors.append(error)
            continue
        if is_runner_file:
            creation_error = _ensure_runner_log_file(config.root, rel)
            if creation_error:
                errors.append(creation_error)
    return errors


def validate_role_outputs(config: CodexExecConfig, *, require_present: bool) -> list[str]:
    errors: list[str] = []
    for value in config.role_output_artifacts:
        try:
            rel = _lexical_repo_rel(value)
        except ValueError as exc:
            errors.append(str(exc))
            continue
        error = _target_type_error(
            config.root,
            rel,
            require_present=require_present,
            label="declared role output",
        )
        if error:
            errors.append(error)
    return errors


def _read_runner_log_baseline(root: Path, rel: str) -> RunnerLogBaseline:
    """Read bytes and identity from one regular log without accepting a path swap."""

    _check_real_parent_chain(root, rel, label="runner log file")
    path = root.resolve() / rel
    before = path.lstat()
    if not stat.S_ISREG(before.st_mode):
        raise OSError(f"runner log file must be a regular file: {rel}")
    if before.st_nlink != 1:
        raise OSError(
            "runner log file must have exactly one hard link, "
            f"found {before.st_nlink}: {rel}"
        )

    flags = os.O_RDONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    if hasattr(os, "O_NONBLOCK"):
        flags |= os.O_NONBLOCK
    file_descriptor = os.open(path, flags)
    try:
        opened = os.fstat(file_descriptor)
        if not stat.S_ISREG(opened.st_mode):
            raise OSError(f"runner log file must be a regular file: {rel}")
        if opened.st_nlink != 1:
            raise OSError(
                "runner log file must have exactly one hard link, "
                f"found {opened.st_nlink}: {rel}"
            )
        if (opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino):
            raise OSError(f"runner log file changed identity before it was opened: {rel}")
        chunks: list[bytes] = []
        while True:
            chunk = os.read(file_descriptor, 1024 * 1024)
            if not chunk:
                break
            chunks.append(chunk)
        data = b"".join(chunks)
    finally:
        os.close(file_descriptor)

    current = path.lstat()
    if not stat.S_ISREG(current.st_mode):
        raise OSError(f"runner log file must be a regular file: {rel}")
    if current.st_nlink != 1:
        raise OSError(
            "runner log file must have exactly one hard link, "
            f"found {current.st_nlink}: {rel}"
        )
    if (current.st_dev, current.st_ino) != (opened.st_dev, opened.st_ino):
        raise OSError(f"runner log file changed identity while it was read: {rel}")
    return RunnerLogBaseline(
        data=data,
        device=opened.st_dev,
        inode=opened.st_ino,
        link_count=opened.st_nlink,
    )


def validate_runner_side_effects(snapshot: RoleWriteSnapshot) -> list[str]:
    """Allow append-only runner logs while rejecting replacement or prefix mutation."""

    errors: list[str] = []
    for rel, baseline in sorted(snapshot.runner_logs.items()):
        try:
            current = _read_runner_log_baseline(snapshot.root, rel)
        except (FileNotFoundError, OSError, ValueError) as exc:
            errors.append(f"runner log validation failed for {rel}: {exc}")
            continue
        if (current.device, current.inode) != (baseline.device, baseline.inode):
            errors.append(f"runner log file identity changed during role invocation: {rel}")
        if current.link_count != baseline.link_count or current.link_count != 1:
            errors.append(f"runner log file link count changed during role invocation: {rel}")
        if not current.data.startswith(baseline.data):
            errors.append(f"runner log file did not preserve its pre-call byte prefix: {rel}")
    return errors


def regular_file_signature(root: Path, value: str) -> tuple[str | None, int | None]:
    """Hash one lexical regular file target without accepting symlink substitutes."""

    try:
        rel = _lexical_repo_rel(value)
        _check_real_parent_chain(root, rel, label="declared role output")
        path = root.resolve() / rel
        info = path.lstat()
    except (FileNotFoundError, OSError, ValueError):
        return (None, None)
    if not stat.S_ISREG(info.st_mode):
        return (None, None)
    try:
        return (_file_hash(path), info.st_mtime_ns)
    except OSError:
        return (None, None)


def _role_tree_entries(root: Path) -> dict[str, RoleTreeEntry]:
    """Snapshot repo entries without following symlinks or reading special files."""

    resolved_root = root.resolve()
    entries: dict[str, RoleTreeEntry] = {}

    def visit(directory: Path, prefix: Path) -> None:
        with os.scandir(directory) as children:
            for child in sorted(children, key=lambda item: item.name):
                rel_path = prefix / child.name
                rel = rel_path.as_posix()
                info = child.stat(follow_symlinks=False)
                kind = _entry_kind(info.st_mode)
                if kind == "regular":
                    value = ":".join(
                        [
                            str(_file_hash(resolved_root / rel)),
                            str(info.st_dev),
                            str(info.st_ino),
                            str(info.st_nlink),
                            str(info.st_size),
                            str(info.st_mtime_ns),
                            str(info.st_ctime_ns),
                            str(stat.S_IMODE(info.st_mode)),
                        ]
                    )
                elif kind == "symlink":
                    value = ":".join(
                        [
                            os.readlink(resolved_root / rel),
                            str(info.st_dev),
                            str(info.st_ino),
                            str(info.st_ctime_ns),
                            str(stat.S_IMODE(info.st_mode)),
                        ]
                    )
                elif kind == "directory":
                    value = ":".join(
                        [
                            str(info.st_dev),
                            str(info.st_ino),
                            str(stat.S_IMODE(info.st_mode)),
                        ]
                    )
                else:
                    value = ":".join(
                        [
                            str(stat.S_IFMT(info.st_mode)),
                            str(info.st_rdev),
                            str(info.st_dev),
                            str(info.st_ino),
                            str(info.st_ctime_ns),
                        ]
                    )
                entries[rel] = RoleTreeEntry(kind=kind, value=value)
                if kind == "directory":
                    visit(resolved_root / rel, rel_path)

    visit(resolved_root, Path())
    return entries


def make_role_write_snapshot(config: CodexExecConfig) -> RoleWriteSnapshot:
    outputs = frozenset(_lexical_repo_rel(value) for value in config.role_output_artifacts)
    runner_files = frozenset(
        _lexical_repo_rel(value) for value in _runner_side_effect_values(config)
    )
    runner_logs = {
        rel: _read_runner_log_baseline(config.root, rel) for rel in sorted(runner_files)
    }
    return RoleWriteSnapshot(
        root=config.root.resolve(),
        entries=_role_tree_entries(config.root),
        allowed_files=outputs,
        runner_logs=runner_logs,
    )


def validate_role_write_snapshot(snapshot: RoleWriteSnapshot) -> list[str]:
    try:
        current = _role_tree_entries(snapshot.root)
    except OSError as exc:
        return [f"failed to validate role write scope: {exc}"]

    errors: list[str] = []
    for rel in sorted(set(snapshot.entries) | set(current)):
        if (
            snapshot.entries.get(rel) == current.get(rel)
            or rel in snapshot.allowed_files
            or rel in snapshot.runner_logs
        ):
            continue
        if rel not in snapshot.entries:
            action = "created"
        elif rel not in current:
            action = "deleted"
        else:
            action = "changed"
        errors.append(f"role write outside exact output allowlist ({action}): {rel}")
    return errors


def _latest_rel(config: CodexExecConfig) -> str:
    return _lexical_repo_rel(config.latest_dir)


def _check_real_parent_chain(root: Path, rel: str, *, label: str = "outputs/latest") -> None:
    current = root.resolve()
    for part in Path(rel).parts[:-1]:
        current = current / part
        try:
            mode = current.lstat().st_mode
        except FileNotFoundError:
            return
        if not stat.S_ISDIR(mode):
            raise ValueError(
                f"{label} parent is not a real directory: "
                f"{current.relative_to(root.resolve()).as_posix()}"
            )


def snapshot_latest_tree(config: CodexExecConfig) -> LatestTreeSnapshot:
    """Capture outputs/latest exactly while preserving, but never following, symlinks."""

    root = config.root.resolve()
    rel = _latest_rel(config)
    _check_real_parent_chain(root, rel)
    latest = root / rel
    try:
        root_mode = latest.lstat().st_mode
    except FileNotFoundError:
        return LatestTreeSnapshot(root=latest, existed=False, entries={})
    kind = _entry_kind(root_mode)
    if kind != "directory":
        raise ValueError(
            f"outputs/latest root must be absent or a real directory, found {kind}: {rel}"
        )

    entries: dict[str, LatestTreeEntry] = {}

    def visit(directory: Path, prefix: Path) -> None:
        with os.scandir(directory) as children:
            for child in sorted(children, key=lambda item: item.name):
                child_rel_path = prefix / child.name
                child_rel = child_rel_path.as_posix()
                path = latest / child_rel_path
                info = child.stat(follow_symlinks=False)
                child_kind = _entry_kind(info.st_mode)
                if child_kind == "directory":
                    entries[child_rel] = LatestTreeEntry(kind="directory")
                    visit(path, child_rel_path)
                elif child_kind == "regular":
                    entries[child_rel] = LatestTreeEntry(
                        kind="regular",
                        data=path.read_bytes(),
                    )
                elif child_kind == "symlink":
                    entries[child_rel] = LatestTreeEntry(
                        kind="symlink",
                        data=os.readlink(path),
                    )
                else:
                    raise ValueError(
                        "outputs/latest contains unsupported special entry "
                        f"({child_kind}): {child_rel}"
                    )

    visit(latest, Path())
    return LatestTreeSnapshot(root=latest, existed=True, entries=entries)


def validate_latest_tree(config: CodexExecConfig, snapshot: LatestTreeSnapshot) -> list[str]:
    try:
        current = snapshot_latest_tree(config)
    except (OSError, ValueError) as exc:
        return [f"outputs/latest validation failed: {exc}"]
    if current.existed != snapshot.existed or current.entries != snapshot.entries:
        return ["outputs/latest changed during role invocation"]
    return []


def _remove_entry_no_follow(path: Path) -> None:
    try:
        mode = path.lstat().st_mode
    except FileNotFoundError:
        return
    if stat.S_ISDIR(mode):
        with os.scandir(path) as children:
            for child in children:
                _remove_entry_no_follow(path / child.name)
        path.rmdir()
    else:
        path.unlink()


def restore_latest_tree(
    config: CodexExecConfig,
    snapshot: LatestTreeSnapshot,
) -> list[str]:
    """Restore and revalidate the latest tree without following any role-created link."""

    try:
        _check_real_parent_chain(config.root, _latest_rel(config))
        _remove_entry_no_follow(snapshot.root)
        if snapshot.existed:
            rel = _latest_rel(config)
            parent_errors = _ensure_real_parent(config.root, rel)
            if parent_errors:
                return parent_errors
            snapshot.root.mkdir()
            directories = sorted(
                (
                    rel
                    for rel, entry in snapshot.entries.items()
                    if entry.kind == "directory"
                ),
                key=lambda value: (len(Path(value).parts), value),
            )
            for rel in directories:
                (snapshot.root / rel).mkdir()
            for rel, entry in sorted(snapshot.entries.items()):
                path = snapshot.root / rel
                if entry.kind == "regular":
                    path.write_bytes(entry.data if isinstance(entry.data, bytes) else b"")
                elif entry.kind == "symlink":
                    os.symlink(str(entry.data), path)
    except (OSError, ValueError) as exc:
        return [f"outputs/latest restore failed: {exc}"]

    errors = validate_latest_tree(config, snapshot)
    return [f"outputs/latest restore verification failed: {error}" for error in errors]


@dataclass(frozen=True)
class FileSnapshot:
    tracked_hashes: dict[str, str | None]
    protected_untracked_hashes: dict[str, str | None]
    protected_artifact_hashes: dict[str, str | None]
    untracked_files: frozenset[str]
    artifact_files: frozenset[str]
    allowed_prefixes: tuple[str, ...]
    allowed_files: frozenset[str]
    # Paths inside an allowed subtree that a role still may not touch. These are the
    # provenance records the resume gate itself trusts, so a role that could rewrite them
    # could approve its own tampering.
    protected_prefixes: tuple[str, ...] = ()
    protected_files: frozenset[str] = frozenset()
    protected_gate_hashes: dict[str, str | None] = field(default_factory=dict)


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
    if config.role:
        return frozenset(
            _lexical_repo_rel(value)
            for value in (
                *config.role_output_artifacts,
                *_runner_side_effect_values(config),
            )
        )
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
    try:
        info = path.lstat()
    except (FileNotFoundError, OSError):
        return None
    if stat.S_ISLNK(info.st_mode):
        try:
            target = os.readlink(path)
        except OSError:
            return None
        return f"symlink:{sha256(target.encode('utf-8')).hexdigest()}"
    if not stat.S_ISREG(info.st_mode):
        return f"special:{stat.S_IFMT(info.st_mode)}:{info.st_rdev}"
    digest = sha256()
    try:
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError:
        return None
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
            try:
                mode = path.lstat().st_mode
            except OSError:
                continue
            if not stat.S_ISDIR(mode):
                files.add(path.relative_to(root).as_posix())
    return frozenset(files)


def make_path_snapshot(
    root: Path,
    *,
    allowed_prefixes: Sequence[str],
    allowed_files: Sequence[str] = (),
    protected_prefixes: Sequence[str] = (),
    protected_files: Sequence[str] = (),
) -> FileSnapshot:
    root = root.resolve()
    normalized_prefixes = tuple(_prefix_as_rel(value) for value in allowed_prefixes)
    normalized_files = frozenset(_normalize_rel(value) for value in allowed_files)
    normalized_protected_prefixes = tuple(
        _prefix_as_rel(value) for value in protected_prefixes
    )
    normalized_protected_files = frozenset(
        _normalize_rel(value) for value in protected_files
    )
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

    # Gate records live inside the run's own writable subtree, so they need an explicit
    # exception: hash them here and compare after the call, including files that did not
    # exist before, so a role cannot forge one mid-call either.
    protected_gate_hashes: dict[str, str | None] = {}
    for rel in _protected_gate_files(
        root,
        normalized_protected_prefixes,
        normalized_protected_files,
    ):
        protected_gate_hashes[rel] = _file_hash(root / rel)

    return FileSnapshot(
        tracked_hashes=tracked_hashes,
        protected_untracked_hashes=protected_untracked_hashes,
        protected_artifact_hashes=protected_artifact_hashes,
        untracked_files=untracked_files,
        artifact_files=artifact_files,
        allowed_prefixes=normalized_prefixes,
        allowed_files=normalized_files,
        protected_prefixes=normalized_protected_prefixes,
        protected_files=normalized_protected_files,
        protected_gate_hashes=protected_gate_hashes,
    )


def _protected_gate_files(
    root: Path,
    prefixes: Sequence[str],
    files: frozenset[str],
) -> frozenset[str]:
    """Every existing file the given protected prefixes or exact paths cover."""

    found: set[str] = set()
    for prefix in prefixes:
        base = root / prefix.rstrip("/")
        if not base.is_dir():
            continue
        for path in base.rglob("*"):
            try:
                mode = path.lstat().st_mode
            except OSError:
                continue
            if not stat.S_ISDIR(mode):
                found.add(path.relative_to(root).as_posix())
    for rel in files:
        try:
            mode = (root / rel).lstat().st_mode
        except OSError:
            continue
        if not stat.S_ISDIR(mode):
            found.add(rel)
    return frozenset(found)


def _is_protected_gate_path(rel: str, snapshot: FileSnapshot) -> bool:
    normalized = _normalize_rel(rel)
    if normalized in snapshot.protected_files:
        return True
    return any(
        normalized == prefix.rstrip("/") or normalized.startswith(prefix)
        for prefix in snapshot.protected_prefixes
    )


def make_file_snapshot(config: CodexExecConfig) -> FileSnapshot:
    allowed_prefixes: tuple[str, ...]
    if config.role:
        allowed_prefixes = ()
    else:
        allowed_prefixes = (
            f"work/runs/{config.run_id}/",
            f"outputs/{config.run_id}/",
            f"logs/{config.run_id}/",
            "outputs/latest/",
        )
    return make_path_snapshot(
        config.root,
        allowed_prefixes=allowed_prefixes,
        allowed_files=_configured_output_files(config),
        # The provenance records and the run manifest marker decide whether a resumed phase
        # may trust its upstream state. They live in the run's writable subtree, so lock them
        # for the duration of the call rather than letting a role rewrite its own warrant.
        protected_prefixes=(f"{_normalize_rel(config.work_run_dir)}/provenance/",),
        protected_files=(config.run_manifest_output,),
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

    errors.extend(_protected_gate_errors(root, snapshot))
    return errors


def _protected_gate_errors(root: Path, snapshot: FileSnapshot) -> list[str]:
    """Reject any change to the provenance records the resume gate trusts.

    These files sit inside the run's writable subtree, so a role could otherwise rewrite the
    very records that decide whether its own outputs are trustworthy. Creation and deletion
    count too: forging a checkpoint or receipt is as effective as editing one.
    """

    errors: list[str] = []
    current = _protected_gate_files(
        root,
        snapshot.protected_prefixes,
        snapshot.protected_files,
    )
    for rel, before_hash in sorted(snapshot.protected_gate_hashes.items()):
        if _file_hash(root / rel) != before_hash:
            errors.append(f"protected provenance file changed: {rel}")
    for rel in sorted(current - set(snapshot.protected_gate_hashes)):
        errors.append(f"protected provenance file created: {rel}")
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
    for _manifest_key, field_name in (
        *LATEST_REQUIRED_OUTPUTS,
        *LATEST_OPTIONAL_OUTPUTS,
    ):
        source_value = getattr(config, field_name)
        source = resolve_under_root(config.root, source_value)
        if (
            (_manifest_key, field_name) in LATEST_OPTIONAL_OUTPUTS
            and not source.is_file()
        ):
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
