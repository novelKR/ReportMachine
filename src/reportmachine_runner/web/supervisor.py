"""Single-process supervisor for closed ReportMachine runner invocations."""

from __future__ import annotations

import os
import signal
import stat
import subprocess
import sys
import threading
from hashlib import sha256
from pathlib import Path
from typing import Callable, Mapping, Sequence
from uuid import uuid4

from ._launch_gate import START_FD_ENV, START_PERMIT
from .contracts import Action, ActionRequest, build_runner_argv, validate_idempotency_key
from .state import (
    MAX_ACCEPTED_REQUESTS,
    AcceptedRequest,
    BLOCKING_STATES,
    ExecutionReceipt,
    OperationState,
    StateError,
    StateStore,
    utc_now,
)


class SupervisorError(RuntimeError):
    """The requested operation cannot be safely supervised."""


_EVIDENCE_PATHS: dict[str, tuple[str, ...]] = {
    "run_summary": ("logs", "{run_id}", "run_summary.json"),
    "intent_contract": ("work", "runs", "{run_id}", "intake", "intent_contract.json"),
    "finalize_provenance": ("work", "runs", "{run_id}", "provenance", "finalize.json"),
    "preflight": ("outputs", "{run_id}", "preflight_result.json"),
    "run_manifest": ("outputs", "{run_id}", "run_manifest.json"),
    "article": ("outputs", "{run_id}", "article.md"),
    "article_result": ("outputs", "{run_id}", "article_result.json"),
    "final_sync": ("outputs", "{run_id}", "final_sync_report.json"),
    "latest_manifest": ("outputs", "latest", "run_manifest.json"),
}


def _evidence_file_fingerprint(root: Path, relative: tuple[str, ...]) -> str:
    current = root
    try:
        for index, part in enumerate(relative):
            current = current / part
            info = current.lstat()
            if stat.S_ISLNK(info.st_mode):
                return "unsafe:symlink"
            if index < len(relative) - 1 and not stat.S_ISDIR(info.st_mode):
                return "unsafe:parent"
        if not stat.S_ISREG(info.st_mode):
            return "unsafe:nonregular"
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(current, flags)
        try:
            opened = os.fstat(descriptor)
            if not stat.S_ISREG(opened.st_mode):
                return "unsafe:nonregular"
            digest = sha256()
            while True:
                chunk = os.read(descriptor, 1024 * 1024)
                if not chunk:
                    break
                digest.update(chunk)
        finally:
            os.close(descriptor)
        return f"regular:{opened.st_size}:{opened.st_mtime_ns}:{digest.hexdigest()}"
    except FileNotFoundError:
        return "missing"
    except OSError as exc:
        return f"error:{type(exc).__name__}"


def capture_authoritative_evidence(project_root: Path, run_id: str) -> dict[str, str]:
    """Fingerprint the fixed status evidence set without accepting browser paths."""

    return {
        name: _evidence_file_fingerprint(
            project_root,
            tuple(run_id if part == "{run_id}" else part for part in relative),
        )
        for name, relative in _EVIDENCE_PATHS.items()
    }


def capture_stable_authoritative_evidence(
    project_root: Path,
    run_id: str,
    *,
    owner_adapter: "OwnerStateAdapter",
    allowed_web_execution_id: str | None = None,
) -> dict[str, str]:
    """Observe a stable evidence snapshot without ever taking the writer lock."""

    def unavailable(reason: str) -> dict[str, str]:
        return {"_observation": reason}

    before, error = owner_adapter.read_safely()
    if error is not None:
        return unavailable("unavailable_owner_state")
    if before is not None and (
        allowed_web_execution_id is None
        or before.get("web_execution_id") != allowed_web_execution_id
    ):
        return unavailable("not_observed_foreign_writer")

    first = capture_authoritative_evidence(project_root, run_id)
    after, error = owner_adapter.read_safely()
    if error is not None or before != after:
        return unavailable("unstable_owner_changed")
    second = capture_authoritative_evidence(project_root, run_id)
    final, error = owner_adapter.read_safely()
    if error is not None or after != final or first != second:
        return unavailable("unstable_artifacts_changed")
    second["_observation"] = (
        "stable_owned_recovery_fence" if before is not None else "stable"
    )
    return second


# Deliberately excludes proxy variables. A deployment that needs a proxy must opt in through
# an explicit future server-side configuration rather than inheriting ambient process state.
CHILD_ENV_ALLOWLIST = (
    "HOME",
    "CODEX_HOME",
    "TMPDIR",
    "TMP",
    "TEMP",
    "LANG",
    "LC_ALL",
    "LC_CTYPE",
    "SSL_CERT_FILE",
    "SSL_CERT_DIR",
    "REQUESTS_CA_BUNDLE",
    "CURL_CA_BUNDLE",
)


def build_child_environment(
    source: Mapping[str, str] | None = None,
    *,
    controlled_path: Sequence[str] | str | None = None,
    web_execution_id: str | None = None,
) -> dict[str, str]:
    """Build an empty-dict-based child environment with a controlled executable path."""

    values = os.environ if source is None else source
    environment = {
        name: values[name]
        for name in CHILD_ENV_ALLOWLIST
        if isinstance(values.get(name), str) and values[name]
    }
    if controlled_path is None:
        entries = [
            str(Path(sys.executable).resolve().parent),
            "/opt/homebrew/bin",
            "/usr/local/bin",
            "/usr/bin",
            "/bin",
        ]
        path_value = os.pathsep.join(dict.fromkeys(entries))
    elif isinstance(controlled_path, str):
        path_value = controlled_path
    else:
        path_value = os.pathsep.join(str(item) for item in controlled_path)
    if not path_value or any(not item for item in path_value.split(os.pathsep)):
        raise SupervisorError("controlled PATH must contain only non-empty entries")
    environment["PATH"] = path_value
    if web_execution_id is not None:
        environment["REPORTMACHINE_WEB_EXECUTION_ID"] = web_execution_id
    return environment


def process_birth_hint(pid: int) -> str | None:
    """Return a best-effort value that distinguishes PID reuse."""

    if pid <= 0:
        return None
    proc_stat = Path(f"/proc/{pid}/stat")
    try:
        # Field 22 is process start time. The executable name can contain spaces and
        # parentheses, so split only after the final closing parenthesis.
        suffix = proc_stat.read_text(encoding="utf-8").rsplit(")", 1)[1].strip().split()
        if len(suffix) >= 20:
            return f"proc:{suffix[19]}"
    except (OSError, IndexError, UnicodeError):
        pass
    try:
        result = subprocess.run(
            ["/bin/ps", "-o", "lstart=", "-p", str(pid)],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=2,
            check=False,
            shell=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    value = result.stdout.strip()
    return f"ps:{value}" if result.returncode == 0 and value else None


def _pid_exists(pid: int | None) -> bool:
    if pid is None or pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def process_identity_is_live(pid: int | None, birth_hint: str | None) -> bool:
    if not _pid_exists(pid):
        return False
    if birth_hint is None or pid is None:
        return True
    observed = process_birth_hint(pid)
    return observed is None or observed == birth_hint


class OwnerStateAdapter:
    """Read-only, optional bridge to the core writer-owner record."""

    def __init__(self, project_root: Path, *, state_dir: Path | None = None) -> None:
        self.project_root = Path(project_root).expanduser().resolve()
        self.state_dir = state_dir

    def read(self) -> dict[str, object] | None:
        try:
            from ..execution_control import read_owner_record
        except ImportError:
            return None
        return read_owner_record(self.project_root, state_dir=self.state_dir)

    def read_safely(self) -> tuple[dict[str, object] | None, str | None]:
        try:
            return self.read(), None
        except (OSError, RuntimeError, ValueError) as exc:
            return None, str(exc)

    def acknowledge(
        self,
        expected_lease_id: str | None,
        *,
        on_acknowledged: Callable[[], None] | None = None,
    ) -> bool:
        from ..execution_control import acknowledge_recovery_fence

        return acknowledge_recovery_fence(
            self.project_root,
            expected_lease_id=expected_lease_id,
            state_dir=self.state_dir,
            on_acknowledged=on_acknowledged,
        )


class SingleSupervisor:
    """At most one active execution for this Web process and checkout.

    There is no durable queue. A receipt is written as ``queued`` immediately before spawn;
    seeing that state after process restart is an ambiguity fence, never a retry instruction.
    """

    def __init__(
        self,
        project_root: Path,
        *,
        store: StateStore | None = None,
        owner_adapter: OwnerStateAdapter | None = None,
        python_executable: str | None = None,
        controlled_path: Sequence[str] | str | None = None,
        source_environment: Mapping[str, str] | None = None,
        core_state_dir: Path | None = None,
        recover: bool = True,
    ) -> None:
        self.project_root = Path(project_root).expanduser().resolve()
        self.store = store or StateStore(self.project_root)
        self.owner_adapter = owner_adapter or OwnerStateAdapter(
            self.project_root,
            state_dir=core_state_dir,
        )
        self.python_executable = python_executable or sys.executable
        self.controlled_path = controlled_path
        self.source_environment = source_environment
        self._lock = threading.RLock()
        self._process_reap_lock = threading.Lock()
        self._process: subprocess.Popen[str] | None = None
        self._watcher: threading.Thread | None = None
        self._state_error: str | None = None
        if recover:
            self._recover_existing()

    def _recover_existing(self) -> None:
        try:
            receipt = self.store.load()
        except StateError as exc:
            self._state_error = str(exc)
            return
        if receipt is None or receipt.state not in BLOCKING_STATES:
            return
        if receipt.state is OperationState.QUEUED:
            state = OperationState.RECOVERY_REQUIRED
            message = "spawn-window ambiguity: manual recovery is required; operation was not retried"
        elif receipt.state is OperationState.RECOVERY_REQUIRED:
            # This is already the most conservative restart state. In particular, do not
            # attempt the forbidden RECOVERY_REQUIRED -> INTERRUPTED_UNKNOWN reversal when
            # its recorded PID happens to look live or has been reused.
            return
        elif process_identity_is_live(receipt.pid, receipt.process_birth_hint):
            state = OperationState.INTERRUPTED_UNKNOWN
            message = "supervisor restarted while the recorded process may still be active"
        else:
            state = OperationState.RECOVERY_REQUIRED
            message = "recorded execution did not settle cleanly; manual recovery is required"
        try:
            self.store.write(receipt.transition(state, message=message))
        except StateError as exc:
            self._state_error = str(exc)

    def current_receipt(self) -> ExecutionReceipt | None:
        with self._lock:
            try:
                return self.store.load()
            except StateError as exc:
                raise SupervisorError(str(exc)) from exc

    def start(self, request: ActionRequest, *, idempotency_key: str) -> ExecutionReceipt:
        key = validate_idempotency_key(idempotency_key)
        if request.action in {Action.CANCEL, Action.RECOVER}:
            raise SupervisorError(f"{request.action.value} must use its supervisor method")
        digest = request.payload_digest()
        with self._lock:
            if os.name != "posix":
                raise SupervisorError("Web runner start barrier is supported only on POSIX")
            if self._state_error:
                raise SupervisorError(f"durable state requires recovery: {self._state_error}")
            existing = self.current_receipt()
            if existing is not None and existing.idempotency_key == key:
                if existing.payload_digest != digest:
                    raise SupervisorError("idempotency key was already used for a different payload")
                return existing
            if existing is not None:
                for accepted in existing.accepted_requests:
                    if accepted.idempotency_key != key:
                        continue
                    if accepted.payload_digest != digest:
                        raise SupervisorError(
                            "idempotency key was already used for a different payload"
                        )
                    raise SupervisorError(
                        "request was already accepted as execution "
                        f"{accepted.execution_id}; use the existing result"
                    )
            same_target_action = bool(
                existing is not None
                and existing.run_id == request.run_id
                and existing.action == request.action.value
            )
            if same_target_action and not request.confirm_rerun:
                assert existing is not None
                if existing.payload_digest == digest:
                    return existing
                raise SupervisorError(
                    "the same run_id and action already have a receipt; explicit confirm_rerun is required"
                )
            if existing is not None and not request.confirm_rerun:
                for accepted in existing.accepted_requests:
                    if (
                        accepted.run_id != request.run_id
                        or accepted.action != request.action.value
                    ):
                        continue
                    if accepted.payload_digest == digest:
                        raise SupervisorError(
                            "request was already accepted as execution "
                            f"{accepted.execution_id}; use the existing result"
                        )
                    raise SupervisorError(
                        "the same run_id and action were already accepted; "
                        "explicit confirm_rerun is required"
                    )
            if existing is not None and existing.state in BLOCKING_STATES:
                raise SupervisorError(
                    f"execution {existing.execution_id} blocks new work in state {existing.state.value}"
                )

            execution_id = str(uuid4())
            now = utc_now()
            assert request.run_id is not None
            accepted_requests: tuple[AcceptedRequest, ...] = ()
            if existing is not None:
                current = AcceptedRequest.from_receipt(existing)
                accepted_requests = (
                    current,
                    *tuple(
                        accepted
                        for accepted in existing.accepted_requests
                        if accepted.execution_id != current.execution_id
                        and accepted.idempotency_key != current.idempotency_key
                    ),
                )[:MAX_ACCEPTED_REQUESTS]
            artifact_baseline = capture_stable_authoritative_evidence(
                self.project_root,
                request.run_id,
                owner_adapter=self.owner_adapter,
            )
            receipt = ExecutionReceipt(
                execution_id=execution_id,
                idempotency_key=key,
                payload_digest=digest,
                run_id=request.run_id,
                action=request.action.value,
                state=OperationState.QUEUED,
                created_at=now,
                updated_at=now,
                requested_phase=request.requested_phase(),
                artifact_baseline=artifact_baseline,
                accepted_requests=accepted_requests,
            )
            self.store.write(receipt)
            self.store.append_log(
                f"\n[{now}] execution={execution_id} run={request.run_id} action={request.action.value} queued\n"
            )

            argv = build_runner_argv(
                request,
                project_root=self.project_root,
                python_executable=self.python_executable,
            )
            environment = build_child_environment(
                self.source_environment,
                controlled_path=self.controlled_path,
                web_execution_id=execution_id,
            )
            launcher = Path(__file__).with_name("_launch_gate.py")
            read_fd: int | None = None
            write_fd: int | None = None
            try:
                read_fd, write_fd = os.pipe()
                environment[START_FD_ENV] = str(read_fd)
                process = subprocess.Popen(
                    [self.python_executable, str(launcher), *argv],
                    cwd=self.project_root,
                    env=environment,
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    bufsize=1,
                    shell=False,
                    start_new_session=True,
                    close_fds=True,
                    pass_fds=(read_fd,),
                )
            except OSError as exc:
                for descriptor in (write_fd, read_fd):
                    if descriptor is not None:
                        try:
                            os.close(descriptor)
                        except OSError:
                            pass
                failed = receipt.transition(
                    OperationState.EXITED,
                    exit_code=126,
                    finished_at=utc_now(),
                    message=f"runner spawn failed: {type(exc).__name__}",
                    artifact_final=capture_stable_authoritative_evidence(
                        self.project_root,
                        request.run_id,
                        owner_adapter=self.owner_adapter,
                    ),
                )
                self.store.write(failed)
                self.store.append_log(f"runner spawn failed: {type(exc).__name__}\n")
                raise SupervisorError("server-owned runner could not be started") from exc

            self._process = process
            birth_hint = process_birth_hint(process.pid)
            watcher = threading.Thread(
                target=self._watch_process,
                args=(process, execution_id),
                name=f"reportmachine-web-{execution_id[:8]}",
                daemon=True,
            )
            self._watcher = watcher
            if birth_hint is None:
                for descriptor in (write_fd, read_fd):
                    assert descriptor is not None
                    try:
                        os.close(descriptor)
                    except OSError:
                        pass
                watcher.start()
                raise SupervisorError(
                    "runner PID identity could not be committed; start permit was denied"
                )
            try:
                running = receipt.transition(
                    OperationState.RUNNING,
                    pid=process.pid,
                    process_birth_hint=birth_hint,
                    started_at=utc_now(),
                )
                self.store.write(running)
            except StateError as exc:
                # Closing the only parent write end denies the still-gated child. Leave the
                # durable queued receipt as a conservative recovery fence.
                for descriptor in (write_fd, read_fd):
                    assert descriptor is not None
                    try:
                        os.close(descriptor)
                    except OSError:
                        pass
                watcher.start()
                raise SupervisorError(
                    "runner PID receipt was not committed; start permit was denied and manual recovery is required"
                ) from exc
            assert read_fd is not None and write_fd is not None
            try:
                if os.write(write_fd, START_PERMIT) != len(START_PERMIT):
                    raise OSError("partial start permit write")
            except OSError as exc:
                watcher.start()
                raise SupervisorError(
                    "runner start permit could not be delivered"
                ) from exc
            finally:
                for descriptor in (write_fd, read_fd):
                    try:
                        os.close(descriptor)
                    except OSError:
                        pass
            watcher.start()
            return running

    def _watch_process(self, process: subprocess.Popen[str], execution_id: str) -> None:
        try:
            if process.stdout is not None:
                try:
                    for line in process.stdout:
                        try:
                            self.store.append_log(line)
                        except StateError:
                            # The receipt will report a state error if it too becomes unwritable;
                            # never route the raw line to another sink as a fallback.
                            pass
                finally:
                    process.stdout.close()
            # Serialize the reap boundary with cancellation. A waiter may block on the
            # child's final exit, but it never holds the supervisor state lock while doing
            # so; cancel can therefore win before exit, or observe the completed Popen
            # without ever signalling a recycled numeric PID.
            with self._process_reap_lock:
                return_code = process.wait()
        except BaseException as exc:  # daemon boundary must settle conservatively
            with self._lock:
                self._settle_watch_failure(execution_id, type(exc).__name__)
            return

        with self._lock:
            try:
                receipt = self.store.load()
            except StateError as exc:
                self._state_error = str(exc)
                return
            if receipt is None or receipt.execution_id != execution_id:
                self._state_error = "execution receipt changed while child was active"
                return
            owner, owner_error = self.owner_adapter.read_safely()
            own_owner = bool(
                owner is not None
                and owner.get("web_execution_id") == execution_id
                and owner.get("pid") == process.pid
            )
            foreign_owner = owner is not None and not own_owner
            clean_process_exit = not process_identity_is_live(
                receipt.pid or process.pid,
                receipt.process_birth_hint,
            )
            finished = utc_now()
            artifact_final = capture_stable_authoritative_evidence(
                self.project_root,
                receipt.run_id,
                owner_adapter=self.owner_adapter,
                allowed_web_execution_id=execution_id,
            )

            if receipt.state is OperationState.QUEUED:
                settled = receipt.transition(
                    OperationState.RECOVERY_REQUIRED,
                    exit_code=return_code,
                    finished_at=finished,
                    message="spawn-window ambiguity; manual recovery is required",
                    artifact_final=artifact_final,
                )
            elif receipt.state is OperationState.CANCELLING:
                if (
                    return_code == 0
                    and not own_owner
                    and owner_error is None
                    and clean_process_exit
                ):
                    settled = receipt.transition(
                        OperationState.EXITED,
                        exit_code=return_code,
                        finished_at=finished,
                        message=(
                            "runner exited naturally before cancellation took effect; "
                            "an independent writer is currently active"
                            if foreign_owner
                            else "runner exited naturally before cancellation took effect"
                        ),
                        artifact_final=artifact_final,
                    )
                elif (
                    return_code == 143
                    and not own_owner
                    and owner_error is None
                    and clean_process_exit
                ):
                    settled = receipt.transition(
                        OperationState.CANCELLED,
                        exit_code=return_code,
                        finished_at=finished,
                        message="top-level runner acknowledged cancellation and left no owner fence",
                        artifact_final=artifact_final,
                    )
                elif clean_process_exit:
                    settled = receipt.transition(
                        OperationState.RECOVERY_REQUIRED,
                        exit_code=return_code,
                        finished_at=finished,
                        message="cancellation did not produce clean fence-free terminal evidence",
                        artifact_final=artifact_final,
                    )
                else:
                    settled = receipt.transition(
                        OperationState.INTERRUPTED_UNKNOWN,
                        exit_code=return_code,
                        finished_at=finished,
                        message="runner termination state is unknown after cancellation",
                        artifact_final=artifact_final,
                    )
            elif receipt.state is OperationState.RUNNING:
                if owner_error is not None or own_owner:
                    settled = receipt.transition(
                        OperationState.RECOVERY_REQUIRED,
                        exit_code=return_code,
                        finished_at=finished,
                        message="runner exited while a core owner fence remained or was unreadable",
                        artifact_final=artifact_final,
                    )
                else:
                    settled = receipt.transition(
                        OperationState.EXITED,
                        exit_code=return_code,
                        finished_at=finished,
                        artifact_final=artifact_final,
                        message=(
                            "runner exited; an independent writer is currently active"
                            if foreign_owner
                            else ""
                        ),
                    )
            else:
                return
            try:
                self.store.write(settled)
                self.store.append_log(
                    f"[{finished}] execution={execution_id} state={settled.state.value} exit={return_code}\n"
                )
            except StateError as exc:
                self._state_error = str(exc)
            finally:
                if self._process is process:
                    self._process = None

    def _settle_watch_failure(self, execution_id: str, error_name: str) -> None:
        try:
            receipt = self.store.load()
            if receipt is None or receipt.execution_id != execution_id:
                return
            if receipt.state is OperationState.RUNNING:
                state = OperationState.INTERRUPTED_UNKNOWN
            elif receipt.state in {OperationState.QUEUED, OperationState.CANCELLING}:
                state = OperationState.RECOVERY_REQUIRED
            else:
                return
            self.store.write(
                receipt.transition(
                    state,
                    message=f"supervisor watcher failed: {error_name}",
                    artifact_final=capture_stable_authoritative_evidence(
                        self.project_root,
                        receipt.run_id,
                        owner_adapter=self.owner_adapter,
                        allowed_web_execution_id=execution_id,
                    ),
                )
            )
        except StateError as exc:
            self._state_error = str(exc)

    def cancel(self, execution_id: str) -> ExecutionReceipt:
        with self._lock:
            receipt = self.current_receipt()
            if receipt is None or receipt.execution_id != execution_id:
                raise SupervisorError("execution_id is not the active Web execution")
            if receipt.state is OperationState.CANCELLING:
                return receipt
            if receipt.state is not OperationState.RUNNING:
                raise SupervisorError(f"execution cannot be cancelled in state {receipt.state.value}")
            process = self._process
            if process is None or process.pid != receipt.pid:
                raise SupervisorError("top-level runner is not owned by this supervisor process")

            with self._process_reap_lock:
                if process.poll() is not None:
                    raise SupervisorError(
                        "top-level runner already exited; terminal settlement is pending"
                    )
                cancelling = receipt.transition(
                    OperationState.CANCELLING,
                    cancel_requested_at=utc_now(),
                    message="SIGTERM requested for the top-level runner only",
                )
                self.store.write(cancelling)
                try:
                    os.kill(process.pid, signal.SIGTERM)
                except ProcessLookupError:
                    # The watcher owns terminal classification and will use the observed exit.
                    pass
                except OSError as exc:
                    raise SupervisorError("could not signal the top-level runner") from exc
                return cancelling

    def acknowledge_recovery(
        self,
        execution_id: str,
        *,
        owner_lease_id: str | None = None,
    ) -> ExecutionReceipt:
        """Explicitly settle one ambiguous receipt without retrying its workflow."""

        with self._lock:
            receipt = self.current_receipt()
            if receipt is None or receipt.execution_id != execution_id:
                raise SupervisorError("execution_id is not the current Web execution")
            if receipt.state is OperationState.RECOVERED:
                self._state_error = None
                return receipt
            if receipt.state not in {
                OperationState.INTERRUPTED_UNKNOWN,
                OperationState.RECOVERY_REQUIRED,
            }:
                raise SupervisorError(
                    f"execution does not require recovery in state {receipt.state.value}"
                )
            if process_identity_is_live(receipt.pid, receipt.process_birth_hint):
                raise SupervisorError(
                    "recorded process may still be alive; recovery acknowledgement is blocked"
                )
            owner, owner_error = self.owner_adapter.read_safely()
            if owner_error is not None:
                raise SupervisorError(
                    "core writer state is unreadable; recovery acknowledgement is blocked"
                )
            lease_id: str | None = None
            if owner is not None:
                lease_id = owner.get("lease_id")
                if owner.get("run_id") not in {None, receipt.run_id}:
                    raise SupervisorError(
                        "core owner belongs to another run; inspect current state again"
                    )
                if not isinstance(lease_id, str) or not lease_id:
                    raise SupervisorError("core owner lease is unavailable")
                if (
                    owner.get("web_execution_id") != receipt.execution_id
                    or owner.get("pid") != receipt.pid
                ):
                    raise SupervisorError(
                        "core owner is not bound to this Web execution; use the CLI recovery path"
                    )
                if owner_lease_id != lease_id:
                    raise SupervisorError(
                        "exact owner_lease_id is required to acknowledge the current fence"
                    )
            elif owner_lease_id is not None:
                raise SupervisorError(
                    "core owner fence changed; reload status before acknowledging recovery"
                )

            finished = utc_now()
            evidence = capture_stable_authoritative_evidence(
                self.project_root,
                receipt.run_id,
                owner_adapter=self.owner_adapter,
                allowed_web_execution_id=(receipt.execution_id if owner is not None else None),
            )
            if evidence.get("_observation") not in {
                "stable",
                "stable_owned_recovery_fence",
            }:
                raise SupervisorError(
                    "artifact/provenance evidence was not stable; recovery remains blocked"
                )
            try:
                from .status import StatusReader

                inspected = StatusReader(
                    self.project_root,
                    supervisor=self,
                    owner_adapter=self.owner_adapter,
                ).read(receipt.run_id)
            except Exception as exc:
                raise SupervisorError(
                    f"artifact/provenance inspection failed: {type(exc).__name__}"
                ) from exc
            if inspected.article.get("state") == "not_checked":
                raise SupervisorError(
                    "artifact/provenance inspection was not stable; recovery remains blocked"
                )
            recovered = receipt.transition(
                OperationState.RECOVERED,
                finished_at=receipt.finished_at or finished,
                message=(
                    "operator acknowledged ambiguous termination after process and owner checks; "
                    "artifact/provenance state was inspected and the workflow was not retried"
                ),
                artifact_final=evidence,
            )
            try:
                self.owner_adapter.acknowledge(
                    lease_id,
                    on_acknowledged=lambda: self.store.write(recovered),
                )
            except Exception as exc:
                raise SupervisorError(
                    f"core recovery fence was not acknowledged: {type(exc).__name__}"
                ) from exc
            self._state_error = None
            self.store.append_log(
                f"[{finished}] execution={execution_id} state=recovered retry=false\n"
            )
            return recovered


Supervisor = SingleSupervisor


__all__ = [
    "CHILD_ENV_ALLOWLIST",
    "OwnerStateAdapter",
    "SingleSupervisor",
    "Supervisor",
    "SupervisorError",
    "build_child_environment",
    "capture_authoritative_evidence",
    "process_birth_hint",
    "process_identity_is_live",
]
