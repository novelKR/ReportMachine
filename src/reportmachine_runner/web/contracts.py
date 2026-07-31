"""Strict request contracts for the localhost Web control plane.

The browser never supplies an executable, project root, arbitrary option, or path.  A
validated request is converted to one of a small number of server-owned command shapes and
is passed to ``subprocess.Popen`` as an argument vector with ``shell=False``.
"""

from __future__ import annotations

import json
import re
import sys
from dataclasses import dataclass
from enum import Enum
from hashlib import sha256
from pathlib import Path
from typing import Any, Mapping

from ..paths import validate_run_id


class ContractError(ValueError):
    """Raised when a Web request is outside the closed action contract."""


class Action(str, Enum):
    SMOKE = "smoke"
    PREPARE = "prepare"
    RUN_ALL = "run_all"
    ALLOWED_PHASE = "allowed_phase"
    AUDIT = "audit"
    APPROVE_SCOPE = "approve_scope"
    CANCEL = "cancel"
    RECOVER = "recover"


class Mode(str, Enum):
    INDUSTRY = "industry"
    EARNINGS = "earnings"
    PREVIEW = "preview"


# Prepare, audit, and all have distinct actions so a client cannot smuggle a broader action
# through the generic phase route.
ALLOWED_PHASES = frozenset(
    {
        "scope",
        "research-plan",
        "research",
        "evidence",
        "narrative",
        "draft",
        "review",
        "revise",
        "finalize",
    }
)

REQUEST_FIELDS = frozenset(
    {
        "action",
        "mode",
        "run_id",
        "topic",
        "phase",
        "scope_sha256",
        "execution_id",
        "owner_lease_id",
        "confirm_rerun",
    }
)

# These names are called out explicitly because accepting any one of them would turn the Web
# layer into an argv, filesystem, or network configuration proxy.
FORBIDDEN_FIELDS = frozenset(
    {
        "argv",
        "args",
        "command",
        "executable",
        "project_root",
        "project-root",
        "cwd",
        "config",
        "profile",
        "output",
        "article_output",
        "source_ledger_output",
        "image_ledger_output",
        "schema",
        "request_file",
        "request-file",
        "topic_file",
        "sources",
        "shell",
        "allow_shell_network",
        "allow-shell-network",
        "web_search",
        "web-search",
        "proxy",
        "env",
    }
)

MAX_TOPIC_CHARS = 20_000
IDEMPOTENCY_KEY_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{7,127}$")
EXECUTION_ID_PATTERN = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$"
)
SCOPE_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
OWNER_LEASE_ID_PATTERN = re.compile(r"^[0-9a-f]{32}$")


def _closed_string(payload: Mapping[str, Any], name: str, *, required: bool) -> str | None:
    value = payload.get(name)
    if value is None:
        if required:
            raise ContractError(f"{name} is required")
        return None
    if not isinstance(value, str):
        raise ContractError(f"{name} must be a string")
    if value != value.strip() or not value:
        raise ContractError(f"{name} must be non-empty and have no surrounding whitespace")
    if "\x00" in value:
        raise ContractError(f"{name} must not contain NUL")
    return value


@dataclass(frozen=True)
class ActionRequest:
    """One validated action, independent of HTTP or Flask."""

    action: Action
    mode: Mode | None = None
    run_id: str | None = None
    topic: str | None = None
    phase: str | None = None
    scope_sha256: str | None = None
    execution_id: str | None = None
    owner_lease_id: str | None = None
    confirm_rerun: bool = False

    @classmethod
    def from_mapping(cls, payload: Mapping[str, Any]) -> "ActionRequest":
        if not isinstance(payload, Mapping):
            raise ContractError("request body must be a JSON object")
        keys = set(payload)
        forbidden = sorted(keys & FORBIDDEN_FIELDS)
        if forbidden:
            raise ContractError(f"forbidden request field: {forbidden[0]}")
        unknown = sorted(keys - REQUEST_FIELDS)
        if unknown:
            raise ContractError(f"unknown request field: {unknown[0]}")

        raw_action = _closed_string(payload, "action", required=True)
        try:
            action = Action(raw_action)
        except ValueError as exc:
            raise ContractError(f"unsupported action: {raw_action}") from exc

        confirm_rerun = payload.get("confirm_rerun", False)
        if not isinstance(confirm_rerun, bool):
            raise ContractError("confirm_rerun must be a boolean")

        if action in {Action.CANCEL, Action.RECOVER}:
            allowed = {"action", "execution_id"}
            if action is Action.RECOVER:
                allowed.add("owner_lease_id")
            cls._reject_irrelevant(keys, allowed, action)
            execution_id = _closed_string(payload, "execution_id", required=True)
            if not EXECUTION_ID_PATTERN.fullmatch(execution_id):
                raise ContractError("execution_id must be a lowercase UUID")
            owner_lease_id = _closed_string(
                payload,
                "owner_lease_id",
                required=False,
            )
            if owner_lease_id is not None and not OWNER_LEASE_ID_PATTERN.fullmatch(
                owner_lease_id
            ):
                raise ContractError("owner_lease_id must be 32 lowercase hexadecimal characters")
            return cls(
                action=action,
                execution_id=execution_id,
                owner_lease_id=owner_lease_id,
            )

        mode_value = _closed_string(payload, "mode", required=True)
        try:
            mode = Mode(mode_value)
        except ValueError as exc:
            raise ContractError(f"unsupported mode: {mode_value}") from exc

        run_id = _closed_string(payload, "run_id", required=True)
        try:
            validate_run_id(run_id, "run_id")
        except ValueError as exc:
            raise ContractError(str(exc)) from exc

        topic = _closed_string(payload, "topic", required=False)
        if topic is not None:
            if len(topic) > MAX_TOPIC_CHARS:
                raise ContractError(f"topic exceeds {MAX_TOPIC_CHARS} characters")
            if any(ord(char) < 0x20 and char not in "\n\r\t" for char in topic):
                raise ContractError("topic contains a disallowed control character")

        phase = _closed_string(payload, "phase", required=False)
        scope_sha256 = _closed_string(payload, "scope_sha256", required=False)

        if action is Action.ALLOWED_PHASE:
            cls._reject_irrelevant(
                keys,
                {"action", "mode", "run_id", "phase", "confirm_rerun"},
                action,
            )
            if phase is None or phase not in ALLOWED_PHASES:
                raise ContractError(
                    "phase must be one of: " + ", ".join(sorted(ALLOWED_PHASES))
                )
        elif action is Action.APPROVE_SCOPE:
            cls._reject_irrelevant(
                keys,
                {"action", "mode", "run_id", "scope_sha256", "confirm_rerun"},
                action,
            )
            if scope_sha256 is None or not SCOPE_SHA256_PATTERN.fullmatch(scope_sha256):
                raise ContractError("scope_sha256 must be 64 lowercase hexadecimal characters")
        else:
            allowed = {"action", "mode", "run_id"}
            # Only fresh input-building actions accept browser topic text. Resumed phases and
            # audit consume the already-normalized run-local artifacts.
            if action in {Action.SMOKE, Action.PREPARE, Action.RUN_ALL}:
                allowed.add("topic")
            allowed.add("confirm_rerun")
            cls._reject_irrelevant(keys, allowed, action)

        return cls(
            action=action,
            mode=mode,
            run_id=run_id,
            topic=topic,
            phase=phase,
            scope_sha256=scope_sha256,
            confirm_rerun=confirm_rerun,
        )

    @staticmethod
    def _reject_irrelevant(keys: set[str], allowed: set[str], action: Action) -> None:
        extras = sorted(keys - allowed)
        if extras:
            raise ContractError(f"field {extras[0]} is not valid for action {action.value}")

    def canonical_payload(self) -> dict[str, str]:
        values = {
            "action": self.action.value,
            "mode": self.mode.value if self.mode is not None else None,
            "run_id": self.run_id,
            "topic": self.topic,
            "phase": self.phase,
            "scope_sha256": self.scope_sha256,
            "execution_id": self.execution_id,
            "owner_lease_id": self.owner_lease_id,
        }
        return {key: value for key, value in values.items() if value is not None}

    def payload_digest(self) -> str:
        encoded = json.dumps(
            self.canonical_payload(),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return sha256(encoded).hexdigest()

    def requested_phase(self) -> str:
        if self.action is Action.ALLOWED_PHASE:
            assert self.phase is not None
            return self.phase
        return {
            Action.SMOKE: "smoke",
            Action.PREPARE: "prepare",
            Action.RUN_ALL: "all",
            Action.AUDIT: "audit",
            Action.APPROVE_SCOPE: "scope_approval",
        }[self.action]


def validate_idempotency_key(value: str | None) -> str:
    if value is None or not isinstance(value, str):
        raise ContractError("Idempotency-Key header is required")
    if not IDEMPOTENCY_KEY_PATTERN.fullmatch(value):
        raise ContractError(
            "Idempotency-Key must be 8-128 characters using letters, digits, dot, underscore, colon, or hyphen"
        )
    return value


def build_runner_argv(
    request: ActionRequest,
    *,
    project_root: Path,
    python_executable: str | None = None,
) -> tuple[str, ...]:
    """Return the closed argv shape for a server-owned article runner.

    ``project_root`` and ``python_executable`` are constructor-side configuration. Neither
    appears in :class:`ActionRequest`, and callers must still invoke the result with
    ``shell=False``.
    """

    if request.action in {Action.CANCEL, Action.RECOVER}:
        raise ContractError(
            f"{request.action.value} is handled by the supervisor and has no runner argv"
        )
    root = Path(project_root).expanduser().resolve()
    script = root / "scripts" / "run_article.py"
    if not script.is_file():
        raise ContractError("server-owned article runner is missing")
    executable = python_executable or sys.executable
    if not executable or not Path(executable).is_absolute():
        raise ContractError("server-owned Python executable must be absolute")
    assert request.mode is not None and request.run_id is not None

    argv = [
        executable,
        str(script),
        "--mode",
        request.mode.value,
        "--run-id",
        request.run_id,
    ]
    if request.topic is not None:
        argv.extend(
            [
                "--intake-topic",
                request.topic,
                "--intake-original-request",
                request.topic,
            ]
        )

    if request.action is Action.SMOKE:
        argv.append("--smoke-test")
    elif request.action is Action.PREPARE:
        argv.append("--prepare-only")
    elif request.action is Action.RUN_ALL:
        argv.extend(["--phase", "all"])
    elif request.action is Action.ALLOWED_PHASE:
        assert request.phase in ALLOWED_PHASES
        argv.extend(["--phase", request.phase])
    elif request.action is Action.AUDIT:
        argv.extend(["--phase", "audit"])
    elif request.action is Action.APPROVE_SCOPE:
        assert request.scope_sha256 is not None
        argv.extend(
            [
                "--intake-approve-scope-only",
                "--intake-approve-scope-sha256",
                request.scope_sha256,
            ]
        )
    else:  # pragma: no cover - enum exhaustiveness guard
        raise ContractError(f"unsupported executable action: {request.action.value}")
    return tuple(argv)


parse_action_request = ActionRequest.from_mapping


__all__ = [
    "ALLOWED_PHASES",
    "Action",
    "ActionRequest",
    "ContractError",
    "Mode",
    "build_runner_argv",
    "parse_action_request",
    "validate_idempotency_key",
]
