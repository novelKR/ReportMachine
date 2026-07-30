"""Normalize review issues and prove that revision notes cover every blocker."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Sequence

from .artifact_validation import validate_run_identity
from .paths import resolve_under_root
from .phase_contracts import RunArtifacts


REVIEW_PRIORITIES = frozenset({"blocking", "high", "normal"})
REVIEW_REPORT_NAMES = (
    "citation_logic",
    "red_team",
    "qa_report",
    "compression",
    "image_audit",
)
BLOCKING_VERDICTS = {
    "citation_logic": ("overall_status", frozenset({"fail"})),
    "red_team": (
        "verdict",
        frozenset({"thesis_needs_qualification", "thesis_unsupported"}),
    ),
    "qa_report": ("overall_status", frozenset({"fail"})),
}

ReviewIssueKey = tuple[str, str]


def review_report_paths(
    artifacts: RunArtifacts,
    *,
    include_images: bool,
) -> tuple[tuple[str, str], ...]:
    """Return the canonical revision-note namespace and path for each active report."""

    reports = [
        ("citation_logic", artifacts.citation_logic_report),
        ("red_team", artifacts.red_team_report),
        ("qa_report", artifacts.qa_report),
        ("compression", artifacts.compression_report),
    ]
    if include_images:
        reports.append(("image_audit", artifacts.image_audit_report))
    return tuple(reports)


def _read_json_object(root: Path, rel: str, label: str) -> tuple[dict[str, Any] | None, list[str]]:
    try:
        path = resolve_under_root(root, rel)
    except ValueError as exc:
        return None, [str(exc)]
    if not path.is_file():
        return None, [f"missing {label}: {rel}"]
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        return None, [f"invalid {label} JSON: {rel}: {exc.msg}"]
    if not isinstance(payload, dict):
        return None, [f"invalid {label}: expected a JSON object: {rel}"]
    return payload, []


def collect_review_issues(
    root: Path,
    artifacts: RunArtifacts,
    *,
    include_images: bool,
    expected_run_id: str,
    validate_report_contract: bool = True,
) -> tuple[dict[ReviewIssueKey, str], list[str]]:
    """Collect report-scoped issue ids and reject ambiguous issue contracts."""

    collected: dict[ReviewIssueKey, str] = {}
    errors: list[str] = []
    for source_report, rel in review_report_paths(artifacts, include_images=include_images):
        payload, read_errors = _read_json_object(root, rel, f"{source_report} review report")
        errors.extend(read_errors)
        if payload is None:
            continue
        if validate_report_contract:
            errors.extend(
                validate_run_identity(
                    payload,
                    expected_run_id=expected_run_id,
                    label=f"{source_report} review report",
                )
            )
        raw_issues = payload.get("issues")
        if not isinstance(raw_issues, list):
            errors.append(f"{source_report} review report has no valid issues array: {rel}")
            continue
        report_priorities: list[str] = []
        for index, item in enumerate(raw_issues, start=1):
            if not isinstance(item, dict):
                errors.append(f"{source_report} issue {index} is not an object")
                continue
            issue_id = str(item.get("issue_id") or "").strip()
            priority = str(item.get("priority") or "").strip()
            if not issue_id:
                errors.append(f"{source_report} issue {index} has no issue_id")
                continue
            if priority not in REVIEW_PRIORITIES:
                errors.append(
                    f"{source_report}:{issue_id} has invalid priority `{priority or 'missing'}`"
                )
                continue
            report_priorities.append(priority)
            key = (source_report, issue_id)
            if key in collected:
                errors.append(f"duplicate review issue id: {source_report}:{issue_id}")
                continue
            collected[key] = priority
        blocking_contract = BLOCKING_VERDICTS.get(source_report)
        if validate_report_contract and blocking_contract is not None:
            field, blocking_values = blocking_contract
            verdict = payload.get(field)
            if verdict in blocking_values and "blocking" not in report_priorities:
                errors.append(
                    f"{source_report} review report declares `{field}={verdict}` "
                    "without a blocking issue"
                )
    return collected, errors


def validate_review_reports(
    root: Path,
    artifacts: RunArtifacts,
    *,
    include_images: bool,
    expected_run_id: str,
) -> list[str]:
    """Validate review verdict consistency and run identity."""

    _issues, errors = collect_review_issues(
        root,
        artifacts,
        include_images=include_images,
        expected_run_id=expected_run_id,
    )
    return errors


def _note_keys(
    values: Any,
    *,
    field: str,
) -> tuple[list[ReviewIssueKey], list[str]]:
    if not isinstance(values, list):
        return [], [f"revision notes field `{field}` is not an array"]
    keys: list[ReviewIssueKey] = []
    errors: list[str] = []
    seen: set[ReviewIssueKey] = set()
    for index, item in enumerate(values, start=1):
        if not isinstance(item, dict):
            errors.append(f"revision notes `{field}` entry {index} is not an object")
            continue
        source_report = str(item.get("source_report") or "").strip()
        issue_id = str(item.get("issue_id") or "").strip()
        if source_report not in REVIEW_REPORT_NAMES or not issue_id:
            errors.append(
                f"revision notes `{field}` entry {index} has an invalid source_report or issue_id"
            )
            continue
        key = (source_report, issue_id)
        if key in seen:
            errors.append(f"duplicate revision issue reference in `{field}`: {source_report}:{issue_id}")
            continue
        seen.add(key)
        keys.append(key)
    return keys, errors


def _format_keys(keys: Sequence[ReviewIssueKey]) -> str:
    return ", ".join(f"{source}:{issue_id}" for source, issue_id in sorted(keys))


def validate_revision_coverage(
    root: Path,
    artifacts: RunArtifacts,
    *,
    include_images: bool,
    expected_run_id: str,
    validate_report_contract: bool = True,
) -> list[str]:
    """Cross-check revision notes against every active review report's blocking issues."""

    notes, errors = _read_json_object(root, artifacts.revision_notes, "revision notes")
    issues, issue_errors = collect_review_issues(
        root,
        artifacts,
        include_images=include_images,
        expected_run_id=expected_run_id,
        validate_report_contract=validate_report_contract,
    )
    errors.extend(issue_errors)
    if notes is None:
        return errors
    errors.extend(
        validate_run_identity(
            notes,
            expected_run_id=expected_run_id,
            label="revision notes",
        )
    )

    handled, handled_errors = _note_keys(notes.get("handled_issues"), field="handled_issues")
    unresolved, unresolved_errors = _note_keys(
        notes.get("unresolved_blocking_issues"),
        field="unresolved_blocking_issues",
    )
    errors.extend(handled_errors)
    errors.extend(unresolved_errors)

    issue_keys = set(issues)
    handled_keys = set(handled)
    unresolved_keys = set(unresolved)
    unknown_handled = handled_keys - issue_keys
    unknown_unresolved = unresolved_keys - issue_keys
    overlap = handled_keys & unresolved_keys
    blocking = {key for key, priority in issues.items() if priority == "blocking"}
    nonblocking_unresolved = unresolved_keys - blocking
    missing = blocking - handled_keys - unresolved_keys

    if unknown_handled:
        errors.append(f"revision notes handle unknown review issues: {_format_keys(unknown_handled)}")
    if unknown_unresolved:
        errors.append(
            "revision notes list unknown unresolved review issues: "
            + _format_keys(unknown_unresolved)
        )
    if overlap:
        errors.append(
            "review issues cannot be both handled and unresolved: " + _format_keys(overlap)
        )
    if nonblocking_unresolved - unknown_unresolved:
        errors.append(
            "unresolved_blocking_issues contains non-blocking review issues: "
            + _format_keys(nonblocking_unresolved - unknown_unresolved)
        )
    if missing:
        errors.append("blocking review issues are not accounted for: " + _format_keys(missing))
    if unresolved_keys & blocking:
        errors.append(
            "revision left blocking review issues unresolved: "
            + _format_keys(unresolved_keys & blocking)
        )
    return errors
