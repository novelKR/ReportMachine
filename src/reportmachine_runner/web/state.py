"""Durable, checkout-scoped operation receipts for the Web supervisor."""

from __future__ import annotations

import json
import os
import stat
from dataclasses import asdict, dataclass, field, replace
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Mapping
from uuid import uuid4

from ..execution_control import default_state_root, state_paths
from .security import redact_log_text


RECEIPT_VERSION = 3
MAX_ACCEPTED_REQUESTS = 32


class StateError(RuntimeError):
    """Durable Web state could not be safely read or written."""


class OperationState(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    CANCELLING = "cancelling"
    EXITED = "exited"
    CANCELLED = "cancelled"
    RECOVERED = "recovered"
    INTERRUPTED_UNKNOWN = "interrupted_unknown"
    RECOVERY_REQUIRED = "recovery_required"


TERMINAL_STATES = frozenset(
    {OperationState.EXITED, OperationState.CANCELLED, OperationState.RECOVERED}
)
BLOCKING_STATES = frozenset(
    {
        OperationState.QUEUED,
        OperationState.RUNNING,
        OperationState.CANCELLING,
        OperationState.INTERRUPTED_UNKNOWN,
        OperationState.RECOVERY_REQUIRED,
    }
)

VALID_TRANSITIONS: dict[OperationState, frozenset[OperationState]] = {
    OperationState.QUEUED: frozenset(
        {
            OperationState.RUNNING,
            OperationState.EXITED,
            OperationState.RECOVERY_REQUIRED,
        }
    ),
    OperationState.RUNNING: frozenset(
        {
            OperationState.CANCELLING,
            OperationState.EXITED,
            OperationState.INTERRUPTED_UNKNOWN,
            OperationState.RECOVERY_REQUIRED,
        }
    ),
    OperationState.CANCELLING: frozenset(
        {
            OperationState.EXITED,
            OperationState.CANCELLED,
            OperationState.INTERRUPTED_UNKNOWN,
            OperationState.RECOVERY_REQUIRED,
        }
    ),
    OperationState.EXITED: frozenset(),
    OperationState.CANCELLED: frozenset(),
    OperationState.INTERRUPTED_UNKNOWN: frozenset(
        {OperationState.RECOVERY_REQUIRED, OperationState.RECOVERED}
    ),
    OperationState.RECOVERY_REQUIRED: frozenset({OperationState.RECOVERED}),
}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


@dataclass(frozen=True)
class AcceptedRequest:
    """Compact identity retained only to prevent an old accepted request from respawning."""

    execution_id: str
    idempotency_key: str
    payload_digest: str
    run_id: str
    action: str
    created_at: str

    @classmethod
    def from_receipt(cls, receipt: "ExecutionReceipt") -> "AcceptedRequest":
        return cls(
            execution_id=receipt.execution_id,
            idempotency_key=receipt.idempotency_key,
            payload_digest=receipt.payload_digest,
            run_id=receipt.run_id,
            action=receipt.action,
            created_at=receipt.created_at,
        )

    @classmethod
    def from_mapping(cls, payload: Mapping[str, Any]) -> "AcceptedRequest":
        if not isinstance(payload, Mapping):
            raise StateError("accepted request entry must be a JSON object")
        values: dict[str, str] = {}
        for name in (
            "execution_id",
            "idempotency_key",
            "payload_digest",
            "run_id",
            "action",
            "created_at",
        ):
            value = payload.get(name)
            if not isinstance(value, str) or not value:
                raise StateError(f"accepted request field {name} is missing or invalid")
            values[name] = value
        return cls(**values)


@dataclass(frozen=True)
class ExecutionReceipt:
    execution_id: str
    idempotency_key: str
    payload_digest: str
    run_id: str
    action: str
    state: OperationState
    created_at: str
    updated_at: str
    pid: int | None = None
    process_birth_hint: str | None = None
    started_at: str | None = None
    cancel_requested_at: str | None = None
    finished_at: str | None = None
    exit_code: int | None = None
    message: str = ""
    requested_phase: str | None = None
    artifact_baseline: dict[str, str] = field(default_factory=dict)
    artifact_final: dict[str, str] = field(default_factory=dict)
    accepted_requests: tuple[AcceptedRequest, ...] = field(default_factory=tuple)
    version: int = RECEIPT_VERSION

    def transition(self, state: OperationState, **changes: Any) -> "ExecutionReceipt":
        if state is not self.state and state not in VALID_TRANSITIONS[self.state]:
            raise StateError(f"invalid operation transition: {self.state.value} -> {state.value}")
        return replace(self, state=state, updated_at=utc_now(), **changes)

    def to_mapping(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["state"] = self.state.value
        return payload

    @classmethod
    def from_mapping(cls, payload: Mapping[str, Any]) -> "ExecutionReceipt":
        if not isinstance(payload, Mapping):
            raise StateError("receipt must be a JSON object")
        required_strings = (
            "execution_id",
            "idempotency_key",
            "payload_digest",
            "run_id",
            "action",
            "created_at",
            "updated_at",
        )
        for name in required_strings:
            if not isinstance(payload.get(name), str) or not payload.get(name):
                raise StateError(f"receipt field {name} is missing or invalid")
        try:
            state = OperationState(str(payload.get("state")))
        except ValueError as exc:
            raise StateError("receipt state is invalid") from exc
        version = payload.get("version")
        if version not in {1, 2, RECEIPT_VERSION}:
            raise StateError("receipt version is unsupported")
        optional_strings = (
            "process_birth_hint",
            "started_at",
            "cancel_requested_at",
            "finished_at",
            "message",
            "requested_phase",
        )
        values: dict[str, Any] = {}
        for name in optional_strings:
            value = payload.get(name)
            if value is not None and not isinstance(value, str):
                raise StateError(f"receipt field {name} is invalid")
            values[name] = value
        pid = payload.get("pid")
        exit_code = payload.get("exit_code")
        if pid is not None and (not isinstance(pid, int) or isinstance(pid, bool) or pid <= 0):
            raise StateError("receipt pid is invalid")
        if exit_code is not None and (not isinstance(exit_code, int) or isinstance(exit_code, bool)):
            raise StateError("receipt exit_code is invalid")
        snapshots: dict[str, dict[str, str]] = {}
        for name in ("artifact_baseline", "artifact_final"):
            raw = payload.get(name, {})
            if not isinstance(raw, Mapping) or any(
                not isinstance(key, str) or not isinstance(value, str)
                for key, value in raw.items()
            ):
                raise StateError(f"receipt field {name} is invalid")
            snapshots[name] = dict(raw)
        raw_accepted = payload.get("accepted_requests", [])
        if not isinstance(raw_accepted, list):
            raise StateError("receipt field accepted_requests is invalid")
        if len(raw_accepted) > MAX_ACCEPTED_REQUESTS:
            raise StateError("receipt accepted request index exceeds its fixed bound")
        accepted_requests = tuple(
            AcceptedRequest.from_mapping(item) for item in raw_accepted
        )
        execution_ids = {item.execution_id for item in accepted_requests}
        idempotency_keys = {item.idempotency_key for item in accepted_requests}
        if len(execution_ids) != len(accepted_requests):
            raise StateError("receipt accepted request execution_id is duplicated")
        if len(idempotency_keys) != len(accepted_requests):
            raise StateError("receipt accepted request idempotency key is duplicated")
        return cls(
            version=RECEIPT_VERSION,
            execution_id=str(payload["execution_id"]),
            idempotency_key=str(payload["idempotency_key"]),
            payload_digest=str(payload["payload_digest"]),
            run_id=str(payload["run_id"]),
            action=str(payload["action"]),
            state=state,
            created_at=str(payload["created_at"]),
            updated_at=str(payload["updated_at"]),
            pid=pid,
            process_birth_hint=values["process_birth_hint"],
            started_at=values["started_at"],
            cancel_requested_at=values["cancel_requested_at"],
            finished_at=values["finished_at"],
            exit_code=exit_code,
            message=values["message"] or "",
            requested_phase=values["requested_phase"],
            artifact_baseline=snapshots["artifact_baseline"],
            artifact_final=snapshots["artifact_final"],
            accepted_requests=accepted_requests,
        )


def checkout_state_directory(project_root: Path, *, environ: Mapping[str, str] | None = None) -> Path:
    if environ is None:
        base = default_state_root()
    else:
        # Explicit mapping is a Python-level test seam, never ambient process config.
        configured = environ.get("REPORTMACHINE_STATE_DIR")
        if not configured:
            raise StateError("explicit test state mapping requires REPORTMACHINE_STATE_DIR")
        base = Path(configured).expanduser()
        if not base.is_absolute():
            raise StateError("REPORTMACHINE_STATE_DIR must be absolute")
    root = Path(project_root).expanduser().resolve()
    return state_paths(root, state_dir=base).checkout_dir / "web"


def _ensure_directory(path: Path) -> None:
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    if path.is_symlink() or not path.is_dir():
        raise StateError(f"state path is not a real directory: {path}")
    try:
        path.chmod(0o700)
    except OSError as exc:
        raise StateError(f"cannot secure state directory: {exc}") from exc


class StateStore:
    """Atomic receipt plus a redacted append-only Web log for one checkout."""

    def __init__(
        self,
        project_root: Path,
        *,
        environ: Mapping[str, str] | None = None,
        state_directory: Path | None = None,
    ) -> None:
        self.project_root = Path(project_root).expanduser().resolve()
        self.directory = (
            Path(state_directory).expanduser()
            if state_directory is not None
            else checkout_state_directory(self.project_root, environ=environ)
        )
        if not self.directory.is_absolute():
            raise StateError("state directory must be absolute")
        _ensure_directory(self.directory)
        self.receipt_path = self.directory / "receipt.json"
        self.log_path = self.directory / "web.log"

    def load(self) -> ExecutionReceipt | None:
        try:
            info = self.receipt_path.lstat()
        except FileNotFoundError:
            return None
        if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
            raise StateError("receipt path must be a regular non-symlink file")
        try:
            payload = json.loads(self.receipt_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise StateError(f"receipt is unreadable: {exc}") from exc
        return ExecutionReceipt.from_mapping(payload)

    def write(self, receipt: ExecutionReceipt) -> None:
        _ensure_directory(self.directory)
        if self.receipt_path.is_symlink():
            raise StateError("refusing to replace a symlink receipt")
        encoded = (
            json.dumps(receipt.to_mapping(), ensure_ascii=False, indent=2, sort_keys=True)
            + "\n"
        ).encode("utf-8")
        temporary = self.directory / f".receipt.{os.getpid()}.{uuid4().hex}.tmp"
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        try:
            descriptor = os.open(temporary, flags, 0o600)
            os.fchmod(descriptor, 0o600)
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(encoded)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, self.receipt_path)
            self.receipt_path.chmod(0o600)
            directory_fd = os.open(self.directory, os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        except OSError as exc:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass
            raise StateError(f"cannot atomically write receipt: {exc}") from exc

    def append_log(self, value: str | bytes) -> None:
        _ensure_directory(self.directory)
        if self.log_path.is_symlink():
            raise StateError("refusing to write a symlink Web log")
        text = redact_log_text(value)
        if not text:
            return
        flags = os.O_WRONLY | os.O_CREAT | os.O_APPEND
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        try:
            descriptor = os.open(self.log_path, flags, 0o600)
            try:
                os.fchmod(descriptor, 0o600)
                os.write(descriptor, text.encode("utf-8", errors="replace"))
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
        except OSError as exc:
            raise StateError(f"cannot append Web log: {exc}") from exc

    def read_log_tail(self, *, max_bytes: int = 64 * 1024) -> str:
        if max_bytes <= 0 or max_bytes > 1024 * 1024:
            raise ValueError("max_bytes must be between 1 and 1048576")
        try:
            info = self.log_path.lstat()
        except FileNotFoundError:
            return ""
        if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
            raise StateError("Web log must be a regular non-symlink file")
        with self.log_path.open("rb") as handle:
            handle.seek(max(0, info.st_size - max_bytes))
            data = handle.read(max_bytes)
        return redact_log_text(data)


__all__ = [
    "AcceptedRequest",
    "BLOCKING_STATES",
    "ExecutionReceipt",
    "MAX_ACCEPTED_REQUESTS",
    "OperationState",
    "StateError",
    "StateStore",
    "TERMINAL_STATES",
    "checkout_state_directory",
    "utc_now",
]
