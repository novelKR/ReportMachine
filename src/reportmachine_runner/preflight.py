from __future__ import annotations

import csv
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .agent_attempts import file_sha256
from .intake import SOURCE_DISCOVERY_REQUIRED_ID
from .paths import resolve_under_root
from .phase_contracts import SOURCE_STATUS_VERIFIED


EARNINGS_REQUIRED_SLOTS = {
    "current_earnings_release": {
        "period_roles": {"current", "current_quarter", "this_quarter"},
        "doc_kinds": {"earnings_release", "shareholder_letter"},
    },
    "current_investor_presentation": {
        "period_roles": {"current", "current_quarter", "this_quarter"},
        "doc_kinds": {"investor_presentation", "ir_deck"},
    },
    "previous_earnings_release": {
        "period_roles": {"previous", "previous_quarter", "prior", "prior_quarter"},
        "doc_kinds": {"earnings_release", "shareholder_letter"},
    },
    "previous_investor_presentation": {
        "period_roles": {"previous", "previous_quarter", "prior", "prior_quarter"},
        "doc_kinds": {"investor_presentation", "ir_deck"},
    },
}

USABLE_INGEST_STATUSES = frozenset(SOURCE_STATUS_VERIFIED)
BLOCKED_INGEST_STATUSES = {"blocked", "remote_unverified", "stale"}


@dataclass(frozen=True)
class PreflightResult:
    payload: dict[str, Any]

    @property
    def passed(self) -> bool:
        return self.payload.get("status") == "pass"


def _read_csv(path: Path) -> list[dict[str, str]]:
    if not path.is_file():
        return []
    with path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        return [
            {key: (value or "") for key, value in row.items() if key is not None}
            for row in reader
        ]


def _normalize(value: str) -> str:
    return value.strip().lower().replace("-", "_").replace(" ", "_")


def _slot_for(row: dict[str, str]) -> str | None:
    period_role = _normalize(row.get("period_role", ""))
    doc_kind = _normalize(row.get("doc_kind", ""))
    for slot, contract in EARNINGS_REQUIRED_SLOTS.items():
        if period_role in contract["period_roles"] and doc_kind in contract["doc_kinds"]:
            return slot
    return None


def _source_id(row: dict[str, str], fallback: str) -> str:
    value = row.get("source_id", "").strip()
    return value or fallback


def _index_by_source_id(
    rows: list[dict[str, str]],
) -> tuple[dict[str, dict[str, str]], list[str]]:
    """Index only unambiguous identities and report every duplicate source id."""

    indexed: dict[str, dict[str, str]] = {}
    duplicates: set[str] = set()
    for index, row in enumerate(rows, start=1):
        source_id = _source_id(row, f"source_{index:03d}")
        if source_id in indexed:
            duplicates.add(source_id)
            del indexed[source_id]
            continue
        if source_id not in duplicates:
            indexed[source_id] = row
    return indexed, sorted(duplicates)


def _raw_row_usable(root: Path, row: dict[str, str]) -> bool:
    local_path = row.get("local_path", "").strip()
    if not local_path:
        return False
    try:
        return resolve_under_root(root, local_path).is_file()
    except ValueError:
        return False


def _candidate_usable(root: Path, row: dict[str, str]) -> bool:
    return _candidate_integrity_defect(root, row) is None


def _candidate_integrity_defect(root: Path, row: dict[str, str]) -> str | None:
    """Return why a candidate is not a verified file, including digest mismatch."""

    status = row.get("ingest_status", "").strip().lower()
    if status not in USABLE_INGEST_STATUSES:
        return f"has an unusable ingest status `{status or 'unknown'}`"
    local_path = row.get("local_path", "").strip()
    if not local_path:
        return f"is `{status}` but records no local path"
    recorded = row.get("sha256", "").strip().lower()
    if not recorded:
        return f"is `{status}` but records no sha256 digest"
    if len(recorded) != 64 or any(character not in "0123456789abcdef" for character in recorded):
        return f"is `{status}` but records an invalid sha256 digest"
    try:
        path = resolve_under_root(root, local_path)
    except ValueError:
        return f"records a local path outside the project: {local_path}"
    if not path.is_file():
        return f"is `{status}` but its local copy is missing from disk: {local_path}"
    try:
        actual = file_sha256(path)
    except OSError as exc:
        return f"is `{status}` but its local copy cannot be read: {local_path}: {exc}"
    if actual != recorded:
        return (
            f"is `{status}` but its sha256 digest does not match the local file: "
            f"{local_path} (recorded {recorded}, actual {actual or 'unavailable'})"
        )
    return None


def _row_usable(root: Path, raw_row: dict[str, str], candidate_row: dict[str, str] | None) -> bool:
    if candidate_row is not None:
        return _candidate_usable(root, candidate_row)
    return _raw_row_usable(root, raw_row)


def _has_transcript(rows: list[dict[str, str]], period_roles: set[str]) -> bool:
    for row in rows:
        if _normalize(row.get("period_role", "")) not in period_roles:
            continue
        if _normalize(row.get("doc_kind", "")) == "conference_call_transcript":
            return True
    return False


def build_preflight_result(
    *,
    root: Path,
    mode: str,
    run_id: str,
    sources_path: str,
    source_candidate_ledger: str,
    preview_prompt: str = "prompts/PREVIEW_PROMPT.md",
    waived_blocked_sources: tuple[str, ...] = (),
) -> PreflightResult:
    source_rows = _read_csv(resolve_under_root(root, sources_path))
    candidate_path = resolve_under_root(root, source_candidate_ledger)
    candidate_rows = _read_csv(candidate_path)
    _source_index, duplicate_source_ids = _index_by_source_id(source_rows)
    candidates, duplicate_candidate_ids = _index_by_source_id(candidate_rows)
    warnings: list[str] = []
    blocking_errors = [
        f"sources file contains duplicate source_id: {source_id}"
        for source_id in duplicate_source_ids
    ]
    blocking_errors.extend(
        f"source candidate ledger contains duplicate source_id: {source_id}"
        for source_id in duplicate_candidate_ids
    )
    required_sources: dict[str, str] = {}
    waived = {value.strip() for value in waived_blocked_sources if value.strip()}

    if mode == "preview" and not resolve_under_root(root, preview_prompt).is_file():
        required_sources["preview_prompt"] = "fail"
        blocking_errors.append("prompts/PREVIEW_PROMPT.md is missing; preview article contract is unavailable.")
    elif mode == "preview":
        required_sources["preview_prompt"] = "pass"

    if mode == "earnings":
        slot_rows: dict[str, list[tuple[dict[str, str], dict[str, str] | None]]] = {
            slot: [] for slot in EARNINGS_REQUIRED_SLOTS
        }
        for index, raw_row in enumerate(source_rows, start=1):
            slot = _slot_for(raw_row)
            if not slot:
                continue
            source_id = _source_id(raw_row, f"source_{index:03d}")
            slot_rows[slot].append((raw_row, candidates.get(source_id)))

        for slot, rows in slot_rows.items():
            usable = any(_row_usable(root, raw_row, candidate_row) for raw_row, candidate_row in rows)
            required_sources[slot] = "pass" if usable else "fail"
            if not usable:
                blocking_errors.append(f"{slot} is missing or not usable after source ingest.")

        if not _has_transcript(source_rows, {"current", "current_quarter", "this_quarter"}):
            warnings.append("current conference_call_transcript is not listed; continue only if unavailable.")
        if not _has_transcript(source_rows, {"previous", "previous_quarter", "prior", "prior_quarter"}):
            warnings.append("previous conference_call_transcript is not listed; continue only if unavailable.")

    if mode == "industry" and not source_rows:
        warnings.append("sources file has no source rows; final claims still need source support.")

    blocked_required, blocked_warnings = _blocked_required_sources(
        root=root,
        source_rows=source_rows,
        candidates=candidates,
        waived=waived,
    )
    blocking_errors.extend(blocked_required)
    warnings.extend(blocked_warnings)

    status = "fail" if blocking_errors else "pass"
    return PreflightResult(
        {
            "status": status,
            "article_type": mode,
            "run_id": run_id,
            "required_sources": required_sources,
            "warnings": warnings,
            "blocking_errors": blocking_errors,
        }
    )


def _blocked_required_sources(
    *,
    root: Path,
    source_rows: list[dict[str, str]],
    candidates: dict[str, dict[str, str]],
    waived: set[str],
) -> tuple[list[str], list[str]]:
    """Block on required sources that are not verifiably usable, unless explicitly waived.

    Usability is decided positively: a recorded status alone is not proof, because a
    ledger can say `downloaded` after the local file was removed. Every required source
    must therefore still resolve to a hashed file on disk.
    """

    blocking: list[str] = []
    warnings: list[str] = []
    for index, row in enumerate(source_rows, start=1):
        required = _normalize(row.get("required", "")) in {"true", "yes", "1"}
        if not required:
            continue
        source_id = _source_id(row, f"source_{index:03d}")
        if source_id == SOURCE_DISCOVERY_REQUIRED_ID:
            warnings.append(
                "source discovery is still required before publishable claims; "
                "the intake placeholder is not a concrete source"
            )
            continue
        candidate = candidates.get(source_id)
        detail = _required_source_defect(root, source_id, row, candidate)
        if detail is None:
            continue
        if source_id in waived:
            warnings.append(f"{detail} (waived by --waive-blocked-source)")
        else:
            blocking.append(
                f"{detail}. Provide a local copy, or pass --waive-blocked-source {source_id} to continue."
            )
    return blocking, warnings


def _required_source_defect(
    root: Path,
    source_id: str,
    raw_row: dict[str, str],
    candidate: dict[str, str] | None,
) -> str | None:
    """Describe why a required source is unusable, or None when it is verifiably usable."""

    if candidate is None:
        # No candidate row means ingest never verified this source, for example under
        # --no-source-ingest. A usable local file is then the only proof it is available.
        if _raw_row_usable(root, raw_row):
            return None
        return (
            f"required source {source_id} has no verified local copy; "
            "source ingest did not produce a candidate row for it"
        )
    status = candidate.get("ingest_status", "").strip().lower()
    if status in BLOCKED_INGEST_STATUSES:
        # Preserve the ingest reason so operators see why the fetch failed.
        reason = (candidate.get("blocked_reason") or "").strip()
        detail = f"required source {source_id} is `{status}`"
        return f"{detail}: {reason}" if reason else detail
    defect = _candidate_integrity_defect(root, candidate)
    return f"required source {source_id} {defect}" if defect else None


def write_preflight_result(root: Path, output_path: str, result: PreflightResult) -> None:
    target = resolve_under_root(root, output_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(result.payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
