"""Cross-process execution control for one ReportMachine checkout.

Mutating entrypoints share one non-blocking writer lock keyed by the canonical
checkout root.  The lock lives outside the repository, while an atomic owner
record makes an unclean process exit visible to the next invocation.

``state_paths`` and ``read_owner_record`` are intentionally read-only public
helpers so a localhost UI can report the current owner without acquiring or
otherwise influencing the writer lock.
"""

from __future__ import annotations

import errno
import json
import os
import re
import signal
import stat
import tempfile
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
from typing import Callable, TypeVar
from uuid import uuid4


WEB_EXECUTION_ID_ENV = "REPORTMACHINE_WEB_EXECUTION_ID"
BUSY_EXIT_CODE = 75
RECOVERY_REQUIRED_EXIT_CODE = 76
SIGTERM_EXIT_CODE = 128 + signal.SIGTERM

_T = TypeVar("_T", bound=int)
_ACTIVE_WRITER_LOCK_FD: ContextVar[int | None] = ContextVar(
    "reportmachine_active_writer_lock_fd",
    default=None,
)
_WEB_EXECUTION_ID_PATTERN = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$"
)


@dataclass(frozen=True)
class ExecutionStatePaths:
    """Filesystem locations used to coordinate one canonical checkout."""

    state_root: Path
    checkout_id: str
    checkout_dir: Path
    lock_file: Path
    owner_record: Path


class OwnerRecordError(ValueError):
    """Raised when an existing owner record cannot be read safely."""


class ExecutionControlFailure(RuntimeError):
    """A fail-closed acquisition or settlement result with a CLI exit code."""

    def __init__(self, exit_code: int, message: str) -> None:
        self.exit_code = exit_code
        super().__init__(message)


class RecoveryFenceRequired(RuntimeError):
    """A catchable mutation failure could not prove that repository writes settled."""


class _ExecutionSigterm(SystemExit):
    def __init__(self) -> None:
        super().__init__(SIGTERM_EXIT_CODE)


class _SigtermFence:
    """Turn runner SIGTERM into a catchable recovery-fence outcome."""

    def __init__(self) -> None:
        self.seen = False
        self._installed = False
        self._previous: object | None = None

    def _handle(self, _signum: int, _frame: object) -> None:
        self.seen = True
        raise _ExecutionSigterm()

    def install(self) -> None:
        self._previous = signal.getsignal(signal.SIGTERM)
        signal.signal(signal.SIGTERM, self._handle)
        self._installed = True

    def restore(self) -> None:
        if self._installed:
            signal.signal(signal.SIGTERM, self._previous)  # type: ignore[arg-type]
            self._installed = False


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def canonical_checkout_root(root: Path) -> Path:
    """Return the symlink-resolved checkout identity used by every entrypoint."""

    return Path(root).expanduser().resolve()


def active_writer_lock_fd() -> int | None:
    """Return the current runner's lock descriptor for a role-child inheritance boundary."""

    return _ACTIVE_WRITER_LOCK_FD.get()


def _checkout_identity(canonical_root: Path) -> str:
    """Return one identity for every supported alias of the same physical checkout."""

    try:
        info = canonical_root.stat()
    except FileNotFoundError:
        # Legacy prepare paths may create an isolated project root inside the callback.
        # Such a path is not yet a physical checkout and therefore has no alias identity;
        # retain the former deterministic path key until it exists.
        return f"path:{os.path.normcase(str(canonical_root))}"
    except OSError as exc:
        raise OwnerRecordError(
            f"could not identify checkout root {canonical_root}: {exc}"
        ) from exc
    if not stat.S_ISDIR(info.st_mode):
        raise OwnerRecordError(f"checkout root is not a directory: {canonical_root}")
    if os.name == "posix" and info.st_ino:
        return f"posix:{info.st_dev}:{info.st_ino}"
    # Windows normcase collapses its supported case aliases. The POSIX fallback is
    # retained only for unusual filesystems that do not expose a usable inode.
    return f"path:{os.path.normcase(str(canonical_root))}"


def default_state_root() -> Path:
    """Return the fixed per-OS-user execution state root.

    Environment overrides are intentionally not accepted: two launch environments must
    never derive different writer locks for the same checkout.
    """

    if os.name == "posix":
        try:
            import pwd

            home = Path(pwd.getpwuid(os.getuid()).pw_dir)
        except (ImportError, KeyError, OSError):  # pragma: no cover - unusual POSIX host
            home = Path.home()
    else:  # pragma: no cover - Windows compatibility branch
        home = Path.home()
    return home.expanduser().resolve() / ".reportmachine" / "state"


def state_paths(
    checkout_root: Path,
    *,
    state_dir: Path | None = None,
) -> ExecutionStatePaths:
    """Return external state paths for ``checkout_root`` without creating them.

    ``state_dir`` is an internal dependency-injection seam for isolated tests. Normal
    entrypoints always use :func:`default_state_root`, so launch environments cannot split
    the lock identity.
    """

    canonical_root = canonical_checkout_root(checkout_root)
    identity_text = _checkout_identity(canonical_root)
    checkout_id = sha256(identity_text.encode("utf-8")).hexdigest()
    configured = state_dir if state_dir is not None else default_state_root()
    state_root = configured.expanduser().resolve()
    checkout_dir = state_root / "checkouts" / checkout_id
    return ExecutionStatePaths(
        state_root=state_root,
        checkout_id=checkout_id,
        checkout_dir=checkout_dir,
        lock_file=checkout_dir / "writer.lock",
        owner_record=checkout_dir / "owner.json",
    )


def _record_path(
    checkout_or_paths: Path | ExecutionStatePaths,
    *,
    state_dir: Path | None = None,
) -> Path:
    if isinstance(checkout_or_paths, ExecutionStatePaths):
        return checkout_or_paths.owner_record
    return state_paths(checkout_or_paths, state_dir=state_dir).owner_record


def read_owner_record(
    checkout_or_paths: Path | ExecutionStatePaths,
    *,
    state_dir: Path | None = None,
) -> dict[str, object] | None:
    """Read the current owner record without taking the writer lock.

    Missing records return ``None``.  Malformed, non-object, and non-regular
    records raise ``OwnerRecordError`` so callers do not mistake corrupt recovery
    state for an idle checkout.
    """

    path = _record_path(checkout_or_paths, state_dir=state_dir)
    try:
        mode = path.lstat().st_mode
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise OwnerRecordError(f"could not inspect owner record {path}: {exc}") from exc
    if not stat.S_ISREG(mode):
        raise OwnerRecordError(f"owner record is not a regular file: {path}")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise OwnerRecordError(f"owner record is unreadable: {path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise OwnerRecordError(f"owner record is not a JSON object: {path}")
    return payload


def _ensure_state_directory(paths: ExecutionStatePaths) -> None:
    paths.checkout_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    try:
        checkout_mode = paths.checkout_dir.lstat().st_mode
    except OSError as exc:
        raise OwnerRecordError(f"could not inspect execution state directory: {exc}") from exc
    if stat.S_ISLNK(checkout_mode) or not stat.S_ISDIR(checkout_mode):
        raise OwnerRecordError(
            f"execution state directory is not a real directory: {paths.checkout_dir}"
        )
    try:
        os.chmod(paths.checkout_dir, 0o700)
    except OSError:
        # Windows does not implement POSIX permission bits.  Owner-file creation
        # still uses the narrowest stdlib mode available on that platform.
        if os.name != "nt":
            raise


def _open_lock_file(path: Path) -> int:
    descriptor = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)
    try:
        os.chmod(path, 0o600)
        if os.name == "nt" and os.fstat(descriptor).st_size == 0:
            os.write(descriptor, b"\0")
            os.fsync(descriptor)
        os.lseek(descriptor, 0, os.SEEK_SET)
        return descriptor
    except BaseException:
        os.close(descriptor)
        raise


def _try_writer_lock(descriptor: int) -> bool:
    if os.name == "posix":
        import fcntl

        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return False
        except OSError as exc:
            if exc.errno in {errno.EACCES, errno.EAGAIN}:
                return False
            raise
        return True
    if os.name == "nt":
        import msvcrt

        os.lseek(descriptor, 0, os.SEEK_SET)
        try:
            msvcrt.locking(descriptor, msvcrt.LK_NBLCK, 1)
        except OSError as exc:
            if exc.errno in {errno.EACCES, errno.EAGAIN, errno.EDEADLK}:
                return False
            raise
        return True
    raise RuntimeError(f"unsupported execution-lock platform: {os.name}")


def _unlock_writer(descriptor: int) -> None:
    if os.name == "posix":
        import fcntl

        fcntl.flock(descriptor, fcntl.LOCK_UN)
        return
    if os.name == "nt":
        import msvcrt

        os.lseek(descriptor, 0, os.SEEK_SET)
        msvcrt.locking(descriptor, msvcrt.LK_UNLCK, 1)
        return
    raise RuntimeError(f"unsupported execution-lock platform: {os.name}")


def _atomic_write_owner(path: Path, payload: dict[str, object]) -> None:
    serialized = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=path.parent,
    )
    temporary = Path(temporary_name)
    try:
        if hasattr(os, "fchmod"):
            os.fchmod(descriptor, 0o600)
        else:  # pragma: no cover - Windows compatibility branch
            os.chmod(temporary, 0o600)
        handle = os.fdopen(descriptor, "w", encoding="utf-8")
        descriptor = -1
        with handle:
            handle.write(serialized)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        os.chmod(path, 0o600)
        _fsync_directory(path.parent)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def _fsync_directory(path: Path) -> None:
    if os.name != "posix":
        return
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    descriptor = os.open(path, flags)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _owner_entry_exists(path: Path) -> bool:
    return os.path.lexists(path)


def acknowledge_recovery_fence(
    checkout_root: Path,
    *,
    expected_lease_id: str | None,
    state_dir: Path | None = None,
    on_acknowledged: Callable[[], None] | None = None,
) -> bool:
    """Explicitly remove one stale owner fence after acquiring the real writer lock.

    The exact lease id is a compare-and-swap token.  Acquiring the OS lock proves that the
    recorded execution no longer owns the writer boundary; this function never retries the
    interrupted workflow and never changes repository artifacts.  ``False`` means the fence
    was already absent, making repeated acknowledgement safe.
    """

    paths = state_paths(checkout_root, state_dir=state_dir)
    descriptor: int | None = None
    locked = False
    try:
        _ensure_state_directory(paths)
        descriptor = _open_lock_file(paths.lock_file)
        locked = _try_writer_lock(descriptor)
        if not locked:
            raise ExecutionControlFailure(
                BUSY_EXIT_CODE,
                "busy: the recorded execution may still be active",
            )
        owner = read_owner_record(paths)
        if owner is None:
            if on_acknowledged is not None:
                try:
                    on_acknowledged()
                except BaseException as exc:
                    raise ExecutionControlFailure(
                        RECOVERY_REQUIRED_EXIT_CODE,
                        f"recovery_required: acknowledgement settlement failed: {exc}",
                    ) from exc
            return False
        if not isinstance(expected_lease_id, str) or not expected_lease_id:
            raise ExecutionControlFailure(
                RECOVERY_REQUIRED_EXIT_CODE,
                "recovery_required: an exact execution lease id is required",
            )
        if owner.get("lease_id") != expected_lease_id:
            raise ExecutionControlFailure(
                RECOVERY_REQUIRED_EXIT_CODE,
                "recovery_required: the owner lease changed; inspect current state again",
            )
        state = owner.get("state")
        if state not in {"active", "recovery_required"}:
            raise ExecutionControlFailure(
                RECOVERY_REQUIRED_EXIT_CODE,
                f"recovery_required: unsupported owner state {state!r}",
            )
        try:
            paths.owner_record.unlink()
            _fsync_directory(paths.owner_record.parent)
        except OSError as exc:
            raise ExecutionControlFailure(
                RECOVERY_REQUIRED_EXIT_CODE,
                f"recovery_required: could not remove the owner fence: {exc}",
            ) from exc
        if on_acknowledged is not None:
            try:
                on_acknowledged()
            except BaseException as exc:
                try:
                    _atomic_write_owner(paths.owner_record, owner)
                except BaseException as restore_exc:
                    raise ExecutionControlFailure(
                        RECOVERY_REQUIRED_EXIT_CODE,
                        "recovery_required: acknowledgement failed and the owner fence "
                        f"could not be restored: {restore_exc}",
                    ) from restore_exc
                raise ExecutionControlFailure(
                    RECOVERY_REQUIRED_EXIT_CODE,
                    f"recovery_required: acknowledgement settlement failed: {exc}",
                ) from exc
        return True
    finally:
        if descriptor is not None:
            try:
                if locked:
                    _unlock_writer(descriptor)
            finally:
                os.close(descriptor)


def _settle_clean(paths: ExecutionStatePaths, lease_id: str) -> None:
    try:
        owner = read_owner_record(paths)
    except OwnerRecordError as exc:
        raise ExecutionControlFailure(
            RECOVERY_REQUIRED_EXIT_CODE,
            f"recovery_required: {exc}",
        ) from exc
    if owner is None:
        raise ExecutionControlFailure(
            RECOVERY_REQUIRED_EXIT_CODE,
            "recovery_required: the execution owner record disappeared before settlement",
        )
    if owner.get("lease_id") != lease_id:
        raise ExecutionControlFailure(
            RECOVERY_REQUIRED_EXIT_CODE,
            "recovery_required: the execution owner record changed before settlement",
        )
    try:
        paths.owner_record.unlink()
        _fsync_directory(paths.owner_record.parent)
    except OSError as exc:
        raise ExecutionControlFailure(
            RECOVERY_REQUIRED_EXIT_CODE,
            f"recovery_required: could not remove the execution owner record: {exc}",
        ) from exc


def _leave_recovery_fence(
    paths: ExecutionStatePaths,
    owner: dict[str, object],
    *,
    exit_code: int = SIGTERM_EXIT_CODE,
    message: str | None = None,
) -> None:
    fenced = dict(owner)
    fenced["state"] = "recovery_required"
    fenced["ended_at"] = _now_iso()
    fenced["exit_code"] = exit_code
    if message:
        fenced["message"] = message
    _atomic_write_owner(paths.owner_record, fenced)


def _owner_payload(
    *,
    checkout_root: Path,
    operation: str,
    run_id: str | None,
    mode: str | None,
    lease_id: str,
) -> dict[str, object]:
    web_execution_id = os.environ.get(WEB_EXECUTION_ID_ENV)
    if not isinstance(web_execution_id, str) or not _WEB_EXECUTION_ID_PATTERN.fullmatch(
        web_execution_id
    ):
        web_execution_id = None
    return {
        "version": 1,
        "state": "active",
        "lease_id": lease_id,
        "pid": os.getpid(),
        "started_at": _now_iso(),
        "checkout_root": str(checkout_root),
        "operation": operation,
        "run_id": run_id,
        "mode": mode,
        "web_execution_id": web_execution_id,
    }


def run_with_execution_control(
    checkout_root: Path,
    callback: Callable[[], _T],
    *,
    operation: str,
    run_id: str | None = None,
    mode: str | None = None,
    state_dir: Path | None = None,
) -> int:
    """Run one mutating callback under the checkout's fail-closed writer lease.

    Contention returns 75.  A record left by an unclean prior owner returns 76
    after the OS lock is acquired, without replacing that record.  Normal returns
    and ordinary catchable exceptions remove the record. Runner SIGTERM and an
    explicit :class:`RecoveryFenceRequired` outcome retain a recovery fence.
    """

    canonical_root = canonical_checkout_root(checkout_root)
    # A supported custom project root may not exist yet.  Materialize it before
    # deriving the physical directory identity so this lease and every contender
    # use the same inode-based state path after the callback begins writing.
    canonical_root.mkdir(parents=True, exist_ok=True)
    paths = state_paths(canonical_root, state_dir=state_dir)
    descriptor: int | None = None
    locked = False
    preserve_inherited_posix_lease = False
    try:
        _ensure_state_directory(paths)
        descriptor = _open_lock_file(paths.lock_file)
        locked = _try_writer_lock(descriptor)
        if not locked:
            raise ExecutionControlFailure(
                BUSY_EXIT_CODE,
                "busy: another mutating ReportMachine execution owns this checkout",
            )
        if _owner_entry_exists(paths.owner_record):
            raise ExecutionControlFailure(
                RECOVERY_REQUIRED_EXIT_CODE,
                "recovery_required: an earlier execution did not settle cleanly",
            )

        lease_id = uuid4().hex
        owner = _owner_payload(
            checkout_root=canonical_root,
            operation=operation,
            run_id=run_id,
            mode=mode,
            lease_id=lease_id,
        )
        _atomic_write_owner(paths.owner_record, owner)
        guard = _SigtermFence()
        status: int | None = None
        callback_error: BaseException | None = None
        lock_token = _ACTIVE_WRITER_LOCK_FD.set(descriptor)
        try:
            try:
                guard.install()
                try:
                    status = int(callback())
                except _ExecutionSigterm:
                    status = SIGTERM_EXIT_CODE
                except BaseException as exc:
                    callback_error = exc
            finally:
                guard.restore()
        finally:
            _ACTIVE_WRITER_LOCK_FD.reset(lock_token)

        # RoleCommand owns SIGTERM through its full descendant cleanup and attempt
        # settlement boundary.  Its clean 143 return must propagate without turning
        # into an unclean runner fence.  Only this outer guard's direct observation
        # proves SIGTERM landed outside that protected role boundary.
        if guard.seen:
            preserve_inherited_posix_lease = True
            _leave_recovery_fence(paths, owner)
            return SIGTERM_EXIT_CODE

        if isinstance(callback_error, RecoveryFenceRequired):
            message = f"recovery_required: {callback_error}"
            preserve_inherited_posix_lease = True
            _leave_recovery_fence(
                paths,
                owner,
                exit_code=RECOVERY_REQUIRED_EXIT_CODE,
                message=str(callback_error),
            )
            print(message, file=os.sys.stderr)
            return RECOVERY_REQUIRED_EXIT_CODE

        _settle_clean(paths, lease_id)
        if callback_error is not None:
            raise callback_error.with_traceback(callback_error.__traceback__)
        assert status is not None
        return status
    except ExecutionControlFailure as exc:
        print(str(exc), file=os.sys.stderr)
        return exc.exit_code
    finally:
        if descriptor is not None:
            try:
                if locked and not (
                    preserve_inherited_posix_lease and os.name == "posix"
                ):
                    _unlock_writer(descriptor)
            finally:
                # On POSIX, closing only the parent's copy leaves the shared flock
                # active while any explicitly admitted child still owns its copy.
                os.close(descriptor)
