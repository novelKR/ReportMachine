"""Intent contract creation and the scope approval gate.

The intent contract is the run's record of what the user actually asked for. The scope gate
refuses to enter drafting phases while a material scope change is unapproved, which is the
defect that let a market-reaction question become a fundamentals-only article.
"""

from __future__ import annotations

import json
import os
import stat
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
from typing import Any

from .paths import resolve_under_root
from .schema_validation import validate_json_artifact


INTENT_CONTRACT_SCHEMA = "schemas/intent_contract.schema.json"

MISSING_ORIGINAL_REQUEST_WARNING = (
    "original_request was not captured for this run; intent fidelity cannot be verified. "
    "Pass --intake-original-request or --intake-original-request-file."
)

STALE_SCOPE_APPROVAL_WARNING = (
    "a material scope change was recorded after the last approval; "
    "drafting is blocked until the user approves the current scope again."
)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def material_scope_delta(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """Return the scope-delta entries that require user approval."""

    deltas = payload.get("scope_delta") or []
    return [item for item in deltas if isinstance(item, dict) and item.get("material")]


def scope_delta_fingerprint(payload: dict[str, Any]) -> str:
    """Fingerprint the material scope delta an approval applies to.

    Approval must not carry over to a delta the user never saw, so the fingerprint
    covers only the fields that describe the change. An empty delta hashes to a
    stable value so an approved aligned scope keeps passing.
    """

    entries = sorted(
        (str(item.get("change", "")), str(item.get("detail", "")))
        for item in material_scope_delta(payload)
    )
    canonical = json.dumps(entries, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return sha256(canonical.encode("utf-8")).hexdigest()


def has_unapproved_material_delta(payload: dict[str, Any]) -> bool:
    """Report whether a material scope change is not covered by the recorded approval."""

    if not material_scope_delta(payload):
        return False
    recorded = str(payload.get("approved_scope_delta_sha256") or "").strip().lower()
    return recorded != scope_delta_fingerprint(payload)


# A freshly seeded contract carries no scope delta yet, so an up-front approval covers
# the empty delta only. Any material change the guardian later records re-blocks.
EMPTY_SCOPE_DELTA_FINGERPRINT = scope_delta_fingerprint({})


@dataclass(frozen=True)
class ScopeGateResult:
    allowed: bool
    status: str
    messages: tuple[str, ...]


@dataclass(frozen=True)
class ScopeApprovalCASResult:
    """Result of binding an approval to one exact current scope fingerprint."""

    payload: dict[str, Any]
    changed: bool
    errors: tuple[str, ...] = ()


def default_analysis_modes(mode: str) -> list[str]:
    if mode == "earnings":
        return ["earnings_quality", "company_fundamental"]
    return ["industry_structure"]


def build_intent_contract(
    *,
    run_id: str,
    mode: str,
    original_request: str | None,
    primary_question: str,
    analysis_modes: list[str] | None,
    depth: str,
    approved: bool,
    base_date: str | None = None,
    must_include: list[str] | None = None,
    must_not_include: list[str] | None = None,
) -> dict[str, Any]:
    warnings: list[str] = []
    if not original_request:
        warnings.append(MISSING_ORIGINAL_REQUEST_WARNING)
    modes = analysis_modes or default_analysis_modes(mode)
    return {
        "version": 1,
        "run_id": run_id,
        "article_type": mode,
        "original_request": original_request or None,
        "primary_question": primary_question,
        "must_include": must_include or [],
        "must_not_include": must_not_include
        or [
            "target price",
            "buy/sell/hold recommendation",
            "valuation conclusion",
        ],
        "proposed_exclusions": [],
        "scope_delta": [],
        "scope_status": "aligned" if approved else "approval_required",
        "analysis_modes": modes,
        "depth": depth,
        "approval_status": "approved" if approved else "pending",
        "approved_at": _now_iso() if approved else None,
        "approved_scope_delta_sha256": EMPTY_SCOPE_DELTA_FINGERPRINT if approved else None,
        "base_date": base_date or datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        "required_user_decisions": [],
        "warnings": warnings,
    }


def _replace_intent_contract_bytes(target: Path, data: bytes) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    target_mode = 0o644
    try:
        current_mode = target.lstat().st_mode
        if stat.S_ISREG(current_mode):
            target_mode = stat.S_IMODE(current_mode)
    except FileNotFoundError:
        pass
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{target.name}.",
        suffix=".tmp",
        dir=target.parent,
    )
    temporary = Path(temporary_name)
    try:
        if hasattr(os, "fchmod"):
            os.fchmod(descriptor, target_mode)
        else:  # pragma: no cover - Windows compatibility branch
            os.chmod(temporary, target_mode)
        handle = os.fdopen(descriptor, "wb")
        descriptor = -1
        with handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, target)
        if os.name == "posix":
            flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
            directory_descriptor = os.open(target.parent, flags)
            try:
                os.fsync(directory_descriptor)
            finally:
                os.close(directory_descriptor)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def write_intent_contract(root: Path, rel: str, payload: dict[str, Any]) -> None:
    target = resolve_under_root(root, rel)
    serialized = (
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")
    _replace_intent_contract_bytes(target, serialized)


def restore_intent_contract_bytes(root: Path, rel: str, data: bytes) -> None:
    """Atomically restore the exact bytes captured before a runner-owned rewrite."""

    # Validate encoding before creating a replacement file. Intent contracts are UTF-8
    # JSON, and restoring undecodable bytes would only turn a catchable mutation failure
    # into a malformed canonical artifact.
    data.decode("utf-8")
    _replace_intent_contract_bytes(resolve_under_root(root, rel), data)


def write_original_request(root: Path, rel: str, text: str | None) -> None:
    target = resolve_under_root(root, rel)
    target.parent.mkdir(parents=True, exist_ok=True)
    if text:
        body = text.strip() + "\n"
    else:
        body = (
            "# Original request not captured\n\n"
            "This run did not record the verbatim user request. Scope fidelity cannot be audited.\n"
        )
    target.write_text(body, encoding="utf-8")


def read_intent_contract(root: Path, rel: str) -> tuple[dict[str, Any] | None, list[str]]:
    payload, errors = validate_json_artifact(
        root=root,
        artifact=rel,
        schema=INTENT_CONTRACT_SCHEMA,
        label="intent contract",
    )
    if payload is None:
        return None, errors
    if not isinstance(payload, dict):
        return None, [f"intent contract is not a JSON object: {rel}"]
    return payload, errors


def read_intent_contract_unvalidated(root: Path, rel: str) -> dict[str, Any] | None:
    """Parse an existing intent contract without validating it against its schema.

    Seeding and CLI approval run during `prepare`, which must stay usable without the
    schema-validation dependency so the App-native fallback keeps working. The scope
    gate still reads the contract through ``read_intent_contract``.
    """

    path = resolve_under_root(root, rel)
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def migrate_intent_contract(payload: dict[str, Any]) -> dict[str, Any]:
    """Backfill additive fields required by the current intent schema.

    A legacy approval cannot be bound retroactively to a material delta it may not
    have covered. Backfilling ``None`` therefore preserves aligned approvals while
    making any material delta require an explicit current approval.
    """

    if "approved_scope_delta_sha256" in payload:
        return payload
    updated = dict(payload)
    updated["approved_scope_delta_sha256"] = None
    return updated


def apply_scope_approval(payload: dict[str, Any], *, approved: bool) -> dict[str, Any]:
    """Record a CLI-provided approval without overriding a conflicting verdict."""

    if not approved:
        return payload
    if payload.get("scope_status") == "conflicting":
        return payload
    updated = dict(payload)
    updated["approval_status"] = "approved"
    updated["approved_at"] = _now_iso()
    # Bind the approval to the delta it approved so a later material change re-blocks.
    updated["approved_scope_delta_sha256"] = scope_delta_fingerprint(payload)
    return updated


def apply_scope_approval_cas(
    payload: dict[str, Any],
    *,
    expected_run_id: str,
    expected_mode: str,
    expected_scope_sha256: str,
) -> ScopeApprovalCASResult:
    """Approve only the exact validated contract state the caller observed.

    Every comparison happens before a replacement payload is created.  A stale
    fingerprint, wrong run or mode, or conflicting scope therefore leaves the
    caller with the original object and no write to perform.
    """

    actual_run_id = str(payload.get("run_id") or "").strip()
    if actual_run_id != expected_run_id:
        return ScopeApprovalCASResult(
            payload=payload,
            changed=False,
            errors=(
                "intent contract run_id mismatch: "
                f"expected {expected_run_id}, found {actual_run_id or 'missing'}",
            ),
        )
    actual_mode = str(payload.get("article_type") or "").strip()
    if actual_mode != expected_mode:
        return ScopeApprovalCASResult(
            payload=payload,
            changed=False,
            errors=(
                "intent contract article_type mismatch: "
                f"expected {expected_mode}, found {actual_mode or 'missing'}",
            ),
        )
    if payload.get("scope_status") == "conflicting":
        return ScopeApprovalCASResult(
            payload=payload,
            changed=False,
            errors=("scope_status is `conflicting`; scope approval cannot override it",),
        )
    current_scope_sha256 = scope_delta_fingerprint(payload)
    if expected_scope_sha256.lower() != current_scope_sha256:
        return ScopeApprovalCASResult(
            payload=payload,
            changed=False,
            errors=(
                "material scope fingerprint mismatch: "
                f"expected {expected_scope_sha256.lower()}, current {current_scope_sha256}",
            ),
        )
    recorded = str(payload.get("approved_scope_delta_sha256") or "").strip().lower()
    if payload.get("approval_status") == "approved" and recorded == current_scope_sha256:
        return ScopeApprovalCASResult(payload=payload, changed=False)
    updated = apply_scope_approval(payload, approved=True)
    return ScopeApprovalCASResult(payload=updated, changed=updated is not payload)


def evaluate_scope_gate(payload: dict[str, Any]) -> ScopeGateResult:
    """Decide whether the run may proceed past the scope gate."""

    scope_status = str(payload.get("scope_status", ""))
    approval_status = str(payload.get("approval_status", ""))
    messages: list[str] = []

    if scope_status == "conflicting":
        messages.append(
            "scope_status is `conflicting`: the request cannot be satisfied under current policy. "
            "Resolve the conflict with the user; --intake-approve-scope cannot override this."
        )
        messages.extend(_decision_lines(payload))
        return ScopeGateResult(allowed=False, status="scope_conflicting", messages=tuple(messages))

    if approval_status != "approved":
        messages.append(
            f"intent contract approval_status is `{approval_status or 'missing'}`; "
            "drafting is blocked until the user approves the scope."
        )
        messages.extend(_scope_delta_lines(payload))
        messages.extend(_decision_lines(payload))
        return ScopeGateResult(allowed=False, status="scope_approval_required", messages=tuple(messages))

    # An approval recorded before a material delta was discovered does not cover it.
    # Checked independently of `scope_status` so a guardian that records a material
    # change without raising the status still cannot slip past the gate.
    if has_unapproved_material_delta(payload):
        messages.append(STALE_SCOPE_APPROVAL_WARNING)
        messages.extend(_scope_delta_lines(payload))
        messages.extend(_decision_lines(payload))
        return ScopeGateResult(
            allowed=False,
            status="scope_approval_required",
            messages=tuple(messages),
        )

    for warning in payload.get("warnings") or []:
        messages.append(f"intent warning: {warning}")
    return ScopeGateResult(allowed=True, status="scope_approved", messages=tuple(messages))


def _scope_delta_lines(payload: dict[str, Any]) -> list[str]:
    lines: list[str] = []
    material = material_scope_delta(payload)
    if material:
        lines.append("material scope changes:")
        for item in material:
            lines.append(f"  - [{item.get('change')}] {item.get('detail')}")
    return lines


def _decision_lines(payload: dict[str, Any]) -> list[str]:
    lines: list[str] = []
    decisions = payload.get("required_user_decisions") or []
    if decisions:
        lines.append("required user decisions:")
        lines.extend(f"  - {item}" for item in decisions)
    return lines
