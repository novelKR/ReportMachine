from __future__ import annotations

import csv
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .paths import resolve_under_root


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

USABLE_INGEST_STATUSES = {"downloaded", "registered_local"}


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


def _candidate_by_source_id(rows: list[dict[str, str]]) -> dict[str, dict[str, str]]:
    return {_source_id(row, f"source_{index:03d}"): row for index, row in enumerate(rows, start=1)}


def _raw_row_usable(root: Path, row: dict[str, str]) -> bool:
    local_path = row.get("local_path", "").strip()
    if not local_path:
        return False
    try:
        return resolve_under_root(root, local_path).is_file()
    except ValueError:
        return False


def _candidate_usable(root: Path, row: dict[str, str]) -> bool:
    status = row.get("ingest_status", "").strip()
    local_path = row.get("local_path", "").strip()
    digest = row.get("sha256", "").strip()
    if status not in USABLE_INGEST_STATUSES or not local_path or not digest:
        return False
    try:
        return resolve_under_root(root, local_path).is_file()
    except ValueError:
        return False


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
) -> PreflightResult:
    source_rows = _read_csv(resolve_under_root(root, sources_path))
    candidate_path = resolve_under_root(root, source_candidate_ledger)
    candidate_rows = _read_csv(candidate_path)
    candidates = _candidate_by_source_id(candidate_rows)
    warnings: list[str] = []
    blocking_errors: list[str] = []
    required_sources: dict[str, str] = {}

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


def write_preflight_result(root: Path, output_path: str, result: PreflightResult) -> None:
    target = resolve_under_root(root, output_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(result.payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
