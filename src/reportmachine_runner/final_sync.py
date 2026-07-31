"""Code-level cross audit of the frozen article against every ledger.

The previous audit trusted the claim ledger's self-declared `support_status`. This module
re-derives the facts instead: claim anchors must exist verbatim in the frozen article,
source ids must exist in the source ledger, quotes must appear in extracted source text,
images must exist with matching hashes, and the recorded article hash must match the file.
"""

from __future__ import annotations

import csv
import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
from typing import Any

from .artifact_validation import (
    duplicate_object_ids,
    read_jsonl,
    validate_jsonl_artifact,
    validate_run_identity,
)
from .paths import resolve_under_root
from .phase_contracts import (
    SOURCE_STATUS_HUMAN_REVIEW,
    SOURCE_STATUS_VERIFIED,
    RunArtifacts,
)
from .schema_validation import validate_json_artifact
from .source_ingest_paths import build_source_ingest_paths


KEEP_VALUES = {"keep", "kept", "true", "yes"}

_IMAGE_REPORT_SCALAR_FIELDS = (
    "visual_question",
    "why_prose_is_insufficient",
    "actual_slide_title",
    "caption_candidate",
    "article_anchor_before",
    "article_anchor_after",
    "drop_reason",
)

_IMAGE_REPORT_LIST_FIELDS = (
    "supported_claim_ids",
    "detected_key_datapoints",
)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


@dataclass
class CheckResult:
    check_id: str
    status: str = "pass"
    details: list[str] = field(default_factory=list)

    def fail(self, message: str) -> None:
        self.status = "fail"
        self.details.append(message)

    def warn(self, message: str) -> None:
        if self.status != "fail":
            self.status = "warn"
        self.details.append(message)

    def skip(self, message: str) -> None:
        self.status = "skipped"
        self.details.append(message)

    def payload(self) -> dict[str, Any]:
        return {"check_id": self.check_id, "status": self.status, "details": self.details}


@dataclass
class FinalSyncResult:
    payload: dict[str, Any]
    errors: list[str]
    warnings: list[str]

    @property
    def passed(self) -> bool:
        return not self.errors


def _read_text(root: Path, rel: str) -> str:
    try:
        path = resolve_under_root(root, rel)
    except ValueError:
        return ""
    return path.read_text(encoding="utf-8") if path.is_file() else ""


def _read_json(root: Path, rel: str, default: Any) -> Any:
    try:
        path = resolve_under_root(root, rel)
    except ValueError:
        return default
    if not path.is_file():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return default


def _read_csv(root: Path, rel: str) -> list[dict[str, str]]:
    try:
        path = resolve_under_root(root, rel)
    except ValueError:
        return []
    if not path.is_file():
        return []
    with path.open(newline="", encoding="utf-8") as handle:
        return [
            {key: (value or "") for key, value in row.items() if key is not None}
            for row in csv.DictReader(handle)
        ]


def _normalized_scalar(value: Any) -> str:
    return "" if value is None else str(value).strip()


def _normalized_digest(value: Any) -> str:
    return _normalized_scalar(value).lower().removeprefix("sha256:")


def _normalized_list(value: Any, *, legacy_delimiters: bool = False) -> list[str]:
    """Normalize JSON-array CSV cells while retaining legacy claim-id cells.

    New final ledgers serialize list-valued fields as compact JSON arrays. Older ledgers
    stored claim ids as comma/semicolon/pipe-delimited text, so that one field remains
    readable without weakening the canonical comparison for arbitrary datapoint strings.
    """

    if isinstance(value, list):
        return [item for item in (_normalized_scalar(item) for item in value) if item]
    text = _normalized_scalar(value)
    if not text:
        return []
    if text.startswith("["):
        try:
            payload = json.loads(text)
        except json.JSONDecodeError:
            payload = None
        if isinstance(payload, list):
            return [item for item in (_normalized_scalar(item) for item in payload) if item]
    if legacy_delimiters:
        return [item.strip() for item in re.split(r"[,;|]", text) if item.strip()]
    return [text]


def _rows_by_image_path(rows: list[dict[str, str]]) -> dict[str, list[dict[str, str]]]:
    by_path: dict[str, list[dict[str, str]]] = {}
    for row in rows:
        image_path = _normalized_scalar(row.get("image_path"))
        by_path.setdefault(image_path, []).append(row)
    return by_path


def _canonical_rendered_ledger(artifacts: RunArtifacts, override: str | None) -> str:
    return override or build_source_ingest_paths(artifacts.work_run_dir).rendered_images_ledger


def _validate_rendered_link(
    *,
    root: Path,
    image_path: str,
    report_row: dict[str, Any],
    rendered_row: dict[str, str],
) -> list[str]:
    errors: list[str] = []
    metadata = (
        ("source_doc", "source_doc"),
        ("source_id", "source_id"),
        ("page_or_slide_number", "page"),
    )
    for report_field, rendered_field in metadata:
        expected = _normalized_scalar(report_row.get(report_field))
        actual = _normalized_scalar(rendered_row.get(rendered_field))
        if not actual:
            errors.append(
                f"rendered image row for {image_path} is missing {rendered_field}"
            )
        if expected != actual:
            errors.append(
                f"image audit {report_field} mismatch for {image_path}: "
                f"report={expected!r} rendered={actual!r}"
            )

    report_source_hash = _normalized_digest(report_row.get("source_hash"))
    rendered_source_hash = _normalized_digest(rendered_row.get("source_hash"))
    if not rendered_source_hash:
        errors.append(f"rendered image row for {image_path} is missing source_hash")
    if report_source_hash != rendered_source_hash:
        errors.append(
            f"image audit source_hash mismatch for {image_path}: "
            f"report={report_source_hash!r} rendered={rendered_source_hash!r}"
        )

    rendered_image_hash = _normalized_digest(rendered_row.get("image_hash"))
    if not rendered_image_hash:
        errors.append(f"rendered image row for {image_path} is missing image_hash")
    try:
        image_file = resolve_under_root(root, image_path)
    except ValueError as exc:
        errors.append(f"image audit path is invalid for {image_path}: {exc}")
        return errors
    actual_image_hash = file_sha256_hex(image_file)
    if actual_image_hash is None:
        errors.append(f"image audit file does not exist: {image_path}")
    elif rendered_image_hash and actual_image_hash != rendered_image_hash:
        errors.append(
            f"rendered image hash mismatch for {image_path}: "
            f"ledger={rendered_image_hash} actual={actual_image_hash}"
        )
    return errors


def validate_image_audit_report(
    *,
    root: Path,
    run_id: str,
    artifacts: RunArtifacts,
    rendered_images_ledger: str | None = None,
) -> list[str]:
    """Validate the canonical review report against runner-rendered image metadata.

    A report is required only when an inspectable rendered image exists. Zero images remains
    valid, including a no-source-ingest run whose rendered ledger is absent. A legacy candidate
    ledger is intentionally never consulted.
    """

    rendered_rel = _canonical_rendered_ledger(artifacts, rendered_images_ledger)
    rendered_rows = [
        row
        for row in _read_csv(root, rendered_rel)
        if _normalized_scalar(row.get("status")).lower() == "ok"
        and _normalized_scalar(row.get("image_path"))
    ]
    rendered_by_path = _rows_by_image_path(rendered_rows)
    try:
        report_path = resolve_under_root(root, artifacts.image_audit_report)
    except ValueError as exc:
        return [f"image audit report path is invalid: {exc}"]
    if not report_path.is_file():
        if rendered_rows:
            return [f"missing canonical image audit report: {artifacts.image_audit_report}"]
        return []

    payload, errors = validate_json_artifact(
        root=root,
        artifact=artifacts.image_audit_report,
        schema="schemas/image_audit_report.schema.json",
        label="image audit report",
    )
    errors.extend(
        validate_run_identity(
            payload,
            expected_run_id=run_id,
            label="image audit report",
        )
    )
    if not isinstance(payload, dict):
        return sorted(set(errors))
    report_images = payload.get("images")
    if not isinstance(report_images, list):
        return sorted(set(errors))

    seen_paths: set[str] = set()
    for index, report_row in enumerate(report_images, start=1):
        if not isinstance(report_row, dict):
            continue
        image_path = _normalized_scalar(report_row.get("image_path"))
        if not image_path:
            continue
        if image_path in seen_paths:
            errors.append(f"image audit report contains duplicate image_path: {image_path}")
            continue
        seen_paths.add(image_path)
        matches = rendered_by_path.get(image_path, [])
        if len(matches) != 1:
            errors.append(
                f"image audit row {index} must match exactly one status=ok rendered row: "
                f"{image_path} (found {len(matches)})"
            )
            continue
        errors.extend(
            _validate_rendered_link(
                root=root,
                image_path=image_path,
                report_row=report_row,
                rendered_row=matches[0],
            )
        )
        decision = _normalized_scalar(report_row.get("keep_or_drop")).lower()
        if decision == "keep":
            for field in (
                "visual_question",
                "why_prose_is_insufficient",
                "caption_candidate",
                "article_anchor_before",
                "article_anchor_after",
            ):
                if not _normalized_scalar(report_row.get(field)):
                    errors.append(
                        f"kept image audit row {index} is missing {field}: {image_path}"
                    )
            if not _normalized_list(report_row.get("supported_claim_ids")):
                errors.append(
                    f"kept image audit row {index} is missing supported_claim_ids: {image_path}"
                )
        elif decision == "drop" and not _normalized_scalar(report_row.get("drop_reason")):
            errors.append(f"dropped image audit row {index} is missing drop_reason: {image_path}")
    for image_path in sorted(set(rendered_by_path) - seen_paths):
        errors.append(
            f"image audit report is missing a keep/drop decision for rendered image: {image_path}"
        )
    return sorted(set(errors))


def validate_final_image_ledger(
    *,
    root: Path,
    run_id: str,
    artifacts: RunArtifacts,
    rendered_images_ledger: str | None = None,
) -> list[str]:
    """Cross-check final image rows against the canonical audit and rendered ledger.

    `caption_candidate` is canonical and must be preserved. `caption` is intentionally not
    compared: the finalizer may derive contextual wording after the article is frozen, while
    the separate finalize audit still checks every number against visible/source evidence.
    """

    errors = validate_image_audit_report(
        root=root,
        run_id=run_id,
        artifacts=artifacts,
        rendered_images_ledger=rendered_images_ledger,
    )
    rendered_rel = _canonical_rendered_ledger(artifacts, rendered_images_ledger)
    rendered_rows = [
        row
        for row in _read_csv(root, rendered_rel)
        if _normalized_scalar(row.get("status")).lower() == "ok"
        and _normalized_scalar(row.get("image_path"))
    ]
    rendered_by_path = _rows_by_image_path(rendered_rows)
    report_payload = _read_json(root, artifacts.image_audit_report, None)
    report_images = (
        report_payload.get("images")
        if isinstance(report_payload, dict) and isinstance(report_payload.get("images"), list)
        else []
    )
    report_rows = [row for row in report_images if isinstance(row, dict)]
    report_by_path: dict[str, dict[str, Any]] = {}
    for row in report_rows:
        image_path = _normalized_scalar(row.get("image_path"))
        if image_path and image_path not in report_by_path:
            report_by_path[image_path] = row

    final_rows: list[dict[str, str]] = []
    for row in _read_csv(root, artifacts.image_ledger):
        if _normalized_scalar(row.get("image_path")):
            final_rows.append(row)
            continue
        # Preserve the established no-image representation: a single drop placeholder
        # with no image-specific data. It carries no decision and is therefore excluded
        # from the canonical report path set.
        decision = _normalized_scalar(row.get("keep_or_drop")).lower()
        meaningful = {
            key: _normalized_scalar(value)
            for key, value in row.items()
            if key not in {"image_path", "keep_or_drop"}
            and _normalized_scalar(value)
        }
        if decision not in {"", "drop"} or meaningful:
            errors.append("final image ledger contains a row with no image_path")
    final_by_path = _rows_by_image_path(final_rows)
    for image_path, rows in final_by_path.items():
        if len(rows) > 1:
            errors.append(f"final image ledger contains duplicate image_path: {image_path}")

    report_paths = set(report_by_path)
    final_paths = {path for path in final_by_path if path}
    for image_path in sorted(report_paths - final_paths):
        errors.append(f"final image ledger is missing audited image: {image_path}")
    for image_path in sorted(final_paths - report_paths):
        errors.append(f"final image ledger contains unaudited image: {image_path}")

    for image_path in sorted(report_paths & final_paths):
        report_row = report_by_path[image_path]
        final_row = final_by_path[image_path][0]
        rendered_matches = rendered_by_path.get(image_path, [])
        if len(rendered_matches) != 1:
            # The report validator already explains the cardinality failure.
            continue
        rendered_row = rendered_matches[0]

        for field in ("source_doc", "source_id", "page_or_slide_number"):
            final_value = _normalized_scalar(final_row.get(field))
            report_value = _normalized_scalar(report_row.get(field))
            if final_value != report_value:
                errors.append(
                    f"final image ledger {field} differs from image audit for {image_path}: "
                    f"final={final_value!r} report={report_value!r}"
                )
        final_source_hash = _normalized_digest(final_row.get("source_hash"))
        report_source_hash = _normalized_digest(report_row.get("source_hash"))
        if final_source_hash != report_source_hash:
            errors.append(
                f"final image ledger source_hash differs from image audit for {image_path}"
            )

        final_decision = _normalized_scalar(final_row.get("keep_or_drop")).lower()
        report_decision = _normalized_scalar(report_row.get("keep_or_drop")).lower()
        if final_decision != report_decision:
            errors.append(
                f"final image ledger keep_or_drop differs from image audit for {image_path}: "
                f"final={final_decision!r} report={report_decision!r}"
            )
        for field in _IMAGE_REPORT_SCALAR_FIELDS:
            final_value = _normalized_scalar(final_row.get(field))
            report_value = _normalized_scalar(report_row.get(field))
            if final_value != report_value:
                errors.append(
                    f"final image ledger {field} differs from image audit for {image_path}"
                )
        for field in _IMAGE_REPORT_LIST_FIELDS:
            legacy = field == "supported_claim_ids"
            final_value = _normalized_list(
                final_row.get(field), legacy_delimiters=legacy
            )
            report_value = _normalized_list(report_row.get(field))
            if final_value != report_value:
                errors.append(
                    f"final image ledger {field} differs from image audit for {image_path}"
                )

        rendered_status = _normalized_scalar(rendered_row.get("status")).lower()
        final_status = _normalized_scalar(final_row.get("inspected_status")).lower()
        if final_status != rendered_status:
            errors.append(
                f"final image ledger inspected_status differs from rendered ledger for "
                f"{image_path}: final={final_status!r} rendered={rendered_status!r}"
            )
        final_image_hash = _normalized_digest(final_row.get("image_hash"))
        rendered_image_hash = _normalized_digest(rendered_row.get("image_hash"))
        if final_image_hash != rendered_image_hash:
            errors.append(
                f"final image ledger image_hash differs from rendered ledger for {image_path}"
            )
        try:
            actual_image_hash = file_sha256_hex(resolve_under_root(root, image_path))
        except ValueError:
            actual_image_hash = None
        if actual_image_hash is None:
            errors.append(f"final image ledger file does not exist: {image_path}")
        elif final_image_hash != actual_image_hash:
            errors.append(
                f"final image ledger image_hash differs from actual file for {image_path}"
            )
    return sorted(set(errors))


def file_sha256_hex(path: Path) -> str | None:
    if not path.is_file():
        return None
    digest = sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def article_sha256(root: Path, rel: str) -> str | None:
    try:
        return file_sha256_hex(resolve_under_root(root, rel))
    except ValueError:
        return None


def _normalize_whitespace(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


def article_headings(article: str) -> set[str]:
    headings: set[str] = set()
    for line in article.splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            headings.add(stripped.lstrip("#").strip())
    return headings


def _source_status(row: dict[str, str]) -> str:
    for key in ("ingest_status", "status", "source_status"):
        value = (row.get(key) or "").strip().lower()
        if value:
            return value
    return ""


def _extracted_text(root: Path, artifacts: RunArtifacts) -> dict[str, str]:
    """Load normalized extracted source text keyed by source id."""

    texts: dict[str, str] = {}
    try:
        directory = resolve_under_root(root, artifacts.extracted_text_dir)
    except ValueError:
        return texts
    if not directory.is_dir():
        return texts
    for path in sorted(directory.glob("*.txt")):
        texts[path.stem] = _normalize_whitespace(path.read_text(encoding="utf-8", errors="replace"))
    return texts


def _pdf_page_texts(
    root: Path,
    artifacts: RunArtifacts,
) -> tuple[dict[tuple[str, int], str | None], set[str]]:
    """Load page-scoped PDF text so page locators can be checked, not merely source quotes."""

    index_rel = f"{artifacts.work_run_dir}/research/pdf_index.jsonl"
    rows, _errors = read_jsonl(root, index_rel)
    texts: dict[tuple[str, int], str | None] = {}
    indexed_sources: set[str] = set()
    for row in rows:
        if not isinstance(row, dict):
            continue
        source_id = str(row.get("source_id") or "").strip()
        page = row.get("page")
        if not source_id or not isinstance(page, int):
            continue
        indexed_sources.add(source_id)
        text_path = str(row.get("text_path") or "").strip()
        if not text_path:
            texts[(source_id, page)] = None
            continue
        text = _read_text(root, text_path)
        texts[(source_id, page)] = _normalize_whitespace(text) if text else None
    return texts, indexed_sources


def run_final_sync_audit(
    *,
    root: Path,
    run_id: str,
    artifacts: RunArtifacts,
    require_evidence_cards: bool = True,
) -> FinalSyncResult:
    article = _read_text(root, artifacts.article)
    result_payload = _read_json(root, artifacts.article_result, {})
    claims = _read_json(root, artifacts.claim_ledger, [])
    source_rows = _read_csv(root, artifacts.source_ledger)
    image_rows = _read_csv(root, artifacts.image_ledger)
    paragraph_map = _read_json(root, artifacts.paragraph_map, {})
    evidence_rows, _ = read_jsonl(root, artifacts.evidence_cards)

    checks: list[CheckResult] = []
    digest = article_sha256(root, artifacts.article)

    checks.append(
        _check_required_artifacts(
            root,
            artifacts,
            require_evidence_cards=require_evidence_cards,
        )
    )
    checks.append(
        _check_schema_contracts(
            root,
            artifacts,
            require_evidence_cards=require_evidence_cards,
            expected_run_id=run_id,
        )
    )
    checks.append(_check_csv_contracts(root, artifacts))
    checks.append(_check_article_hash(article, digest, result_payload))
    checks.append(_check_source_ids(claims, source_rows))
    checks.append(_check_source_status(claims, source_rows))
    checks.append(_check_anchors(claims, article, paragraph_map))
    checks.append(_check_unique_evidence_ids(evidence_rows))
    checks.append(
        _check_evidence_links(claims, evidence_rows, require_evidence_cards=require_evidence_cards)
    )
    checks.append(_check_claim_evidence_classes(claims, evidence_rows))
    checks.append(_check_quotes(root, artifacts, evidence_rows))
    checks.append(_check_image_authority(root, run_id, artifacts))
    checks.append(_check_images(root, article, image_rows))
    checks.append(_check_paragraph_map(paragraph_map, article, digest))
    regenerated_after_freeze = _regenerated_after_freeze(root, artifacts)
    freeze_check = CheckResult("ledgers_regenerated_after_article_freeze")
    if not regenerated_after_freeze:
        freeze_check.fail("one or more ledgers or paragraph_map were missing or older than article.md")
    checks.append(freeze_check)

    errors: list[str] = []
    warnings: list[str] = []
    for check in checks:
        if check.status == "fail":
            errors.extend(f"[{check.check_id}] {detail}" for detail in check.details)
        elif check.status == "warn":
            warnings.extend(f"[{check.check_id}] {detail}" for detail in check.details)

    status = "fail" if errors else ("pass_with_warnings" if warnings else "pass")
    payload = {
        "version": 1,
        "run_id": run_id,
        "generated_at": _now_iso(),
        "article_sha256": digest or "0" * 64,
        "artifact_hashes": _artifact_hashes(root, artifacts),
        "regenerated_after_freeze": regenerated_after_freeze,
        "status": status,
        "checks": [check.payload() for check in checks],
        "errors": errors,
        "warnings": warnings,
    }
    return FinalSyncResult(payload=payload, errors=errors, warnings=warnings)


def _check_required_artifacts(
    root: Path,
    artifacts: RunArtifacts,
    *,
    require_evidence_cards: bool,
) -> CheckResult:
    check = CheckResult("required_final_artifacts_exist")
    required = {
        "article": artifacts.article,
        "research appendix": artifacts.research_appendix,
        "paragraph map": artifacts.paragraph_map,
        "claim ledger": artifacts.claim_ledger,
        "source ledger": artifacts.source_ledger,
        "image ledger": artifacts.image_ledger,
        "article result": artifacts.article_result,
    }
    if require_evidence_cards:
        required["evidence cards"] = artifacts.evidence_cards
    for label, rel in required.items():
        try:
            path = resolve_under_root(root, rel)
        except ValueError as exc:
            check.fail(f"{label} path is invalid: {exc}")
            continue
        if not path.is_file():
            check.fail(f"missing {label}: {rel}")
            continue
        if path.stat().st_size == 0:
            check.fail(f"empty {label}: {rel}")
    return check


def _check_schema_contracts(
    root: Path,
    artifacts: RunArtifacts,
    *,
    require_evidence_cards: bool,
    expected_run_id: str,
) -> CheckResult:
    check = CheckResult("structured_final_artifacts_match_schemas")
    json_contracts = (
        (artifacts.article_result, "schemas/article_result.schema.json", "article result", False),
        (artifacts.claim_ledger, "schemas/claim_ledger.schema.json", "claim ledger", False),
        (artifacts.paragraph_map, "schemas/paragraph_map.schema.json", "paragraph map", True),
    )
    for artifact, schema, label, run_id_scoped in json_contracts:
        payload, errors = validate_json_artifact(
            root=root,
            artifact=artifact,
            schema=schema,
            label=label,
        )
        if run_id_scoped:
            errors.extend(
                validate_run_identity(
                    payload,
                    expected_run_id=expected_run_id,
                    label=label,
                    artifact=artifact,
                )
            )
        for error in errors:
            check.fail(error)
    if require_evidence_cards:
        _rows, errors = validate_jsonl_artifact(
            root=root,
            artifact=artifacts.evidence_cards,
            schema="schemas/evidence_card.schema.json",
            label="evidence cards",
            require_rows=True,
        )
        for error in errors:
            check.fail(error)
    return check


def _check_csv_contracts(root: Path, artifacts: RunArtifacts) -> CheckResult:
    check = CheckResult("final_csv_ledgers_have_required_columns")
    contracts = {
        "source ledger": (
            artifacts.source_ledger,
            {
                "source_id",
                "url",
                "local_path",
                "sha256",
                "ingest_status",
                "allowed_domain",
                "blocked_reason",
            },
        ),
        "image ledger": (
            artifacts.image_ledger,
            {
                "image_path",
                "keep_or_drop",
                "visual_question",
                "supported_claim_ids",
                "why_prose_is_insufficient",
            },
        ),
    }
    for label, (rel, required) in contracts.items():
        try:
            path = resolve_under_root(root, rel)
        except ValueError as exc:
            check.fail(f"{label} path is invalid: {exc}")
            continue
        if not path.is_file():
            continue
        with path.open(newline="", encoding="utf-8") as handle:
            fieldnames = set(csv.DictReader(handle).fieldnames or [])
        missing = sorted(required - fieldnames)
        if missing:
            check.fail(f"{label} is missing required columns: {', '.join(missing)}")
    return check


def _check_article_hash(article: str, digest: str | None, result_payload: Any) -> CheckResult:
    check = CheckResult("article_hash_matches_result")
    if not article:
        check.fail("final article is missing or empty")
        return check
    if digest is None:
        check.fail("could not compute article SHA-256")
        return check
    if not isinstance(result_payload, dict):
        check.fail("article result is not a JSON object")
        return check
    recorded = str(result_payload.get("article_sha256", "")).strip().lower()
    if not recorded:
        check.fail("article_result.json does not record article_sha256")
        return check
    if recorded != digest:
        check.fail(f"article_sha256 mismatch: result={recorded} actual={digest}")
    return check


def _check_source_ids(claims: Any, source_rows: list[dict[str, str]]) -> CheckResult:
    check = CheckResult("claim_source_ids_exist_in_source_ledger")
    if not isinstance(claims, list):
        check.fail("claim ledger is not a JSON array")
        return check
    known = {(row.get("source_id") or "").strip() for row in source_rows}
    known.discard("")
    for claim in claims:
        if not isinstance(claim, dict):
            continue
        claim_id = str(claim.get("claim_id", "unknown"))
        for source_id in claim.get("source_ids") or []:
            if str(source_id).strip() not in known:
                check.fail(f"claim {claim_id} cites unknown source_id: {source_id}")
    return check


def _check_source_status(claims: Any, source_rows: list[dict[str, str]]) -> CheckResult:
    check = CheckResult("unverified_sources_not_silently_supported")
    if not isinstance(claims, list):
        check.skip("claim ledger unavailable")
        return check
    status_by_id = {
        (row.get("source_id") or "").strip(): _source_status(row) for row in source_rows
    }
    for claim in claims:
        if not isinstance(claim, dict):
            continue
        claim_id = str(claim.get("claim_id", "unknown"))
        source_ids = [str(value).strip() for value in (claim.get("source_ids") or [])]
        if not source_ids:
            continue
        statuses = [status_by_id.get(source_id, "") for source_id in source_ids]
        support_status = str(claim.get("support_status", ""))
        needs_review = claim.get("needs_human_review") is True
        unverified = [
            source_id
            for source_id, status in zip(source_ids, statuses)
            if status not in SOURCE_STATUS_VERIFIED and status not in SOURCE_STATUS_HUMAN_REVIEW
        ]
        verified_exists = any(status in SOURCE_STATUS_VERIFIED for status in statuses)
        if unverified and not verified_exists and support_status == "supported" and not needs_review:
            check.fail(
                f"claim {claim_id} is `supported` with needs_human_review=false but relies only on "
                f"unverified sources: {', '.join(unverified)}"
            )
        manual = [
            source_id
            for source_id, status in zip(source_ids, statuses)
            if status in SOURCE_STATUS_HUMAN_REVIEW
        ]
        if manual and not needs_review:
            check.fail(
                f"claim {claim_id} uses remote_manual_verified sources "
                f"({', '.join(manual)}) but needs_human_review is false"
            )
    return check


def _check_claim_evidence_classes(claims: Any, evidence_rows: list[Any]) -> CheckResult:
    """Reject promotion of issuer-only evidence into a non-issuer fact class."""

    check = CheckResult("issuer_claims_not_promoted_to_independent_facts")
    if not isinstance(claims, list) or not evidence_rows:
        check.skip("claim or evidence ledger unavailable")
        return check
    duplicate_ids = set(duplicate_object_ids(evidence_rows, id_field="evidence_id"))
    evidence_by_id = {
        str(row.get("evidence_id", "")).strip(): row
        for row in evidence_rows
        if isinstance(row, dict)
        and row.get("evidence_id")
        and str(row.get("evidence_id", "")).strip() not in duplicate_ids
    }
    for claim in claims:
        if not isinstance(claim, dict):
            continue
        claim_class = str(claim.get("claim_class") or "").strip()
        if not claim_class or claim_class == "issuer_claim":
            continue
        linked = [
            evidence_by_id.get(str(evidence_id).strip())
            for evidence_id in (claim.get("evidence_ids") or [])
        ]
        linked = [row for row in linked if isinstance(row, dict)]
        if not linked:
            continue
        issuer_only = all(
            str(row.get("evidence_class") or "") == "issuer_claim"
            or str(row.get("independence") or "") == "non_independent"
            for row in linked
        )
        if issuer_only and str(claim.get("support_status") or "") == "supported":
            claim_id = str(claim.get("claim_id") or "unknown")
            check.fail(
                f"claim {claim_id} is classified as {claim_class} and supported, "
                "but every linked evidence card is an issuer claim or non-independent"
            )
    return check


def _check_anchors(claims: Any, article: str, paragraph_map: Any) -> CheckResult:
    check = CheckResult("claim_anchors_exist_in_frozen_article")
    if not isinstance(claims, list):
        check.skip("claim ledger unavailable")
        return check
    if not article:
        check.fail("final article is missing or empty")
        return check
    headings = article_headings(article)
    paragraph_ids = {
        str(item.get("paragraph_id"))
        for item in (paragraph_map.get("paragraphs") or [])
        if isinstance(paragraph_map, dict) and isinstance(item, dict)
    }
    for claim in claims:
        if not isinstance(claim, dict):
            continue
        claim_id = str(claim.get("claim_id", "unknown"))
        anchor = claim.get("article_anchor")
        if not isinstance(anchor, dict):
            section = str(claim.get("article_section", "")).strip()
            if section and section not in headings:
                check.fail(
                    f"claim {claim_id} article_section is not a heading of the final article: {section}"
                )
            continue
        heading = str(anchor.get("heading", "")).strip()
        if heading and heading not in headings:
            check.fail(
                f"claim {claim_id} anchor heading is not a verbatim heading of the final article: {heading}"
            )
        paragraph_id = str(anchor.get("paragraph_id", "")).strip()
        if paragraph_ids and paragraph_id and paragraph_id not in paragraph_ids:
            check.fail(f"claim {claim_id} anchor paragraph_id is not in the paragraph map: {paragraph_id}")
    return check


def _check_evidence_links(
    claims: Any,
    evidence_rows: list[Any],
    *,
    require_evidence_cards: bool,
) -> CheckResult:
    check = CheckResult("claim_evidence_ids_exist")
    if not isinstance(claims, list):
        check.skip("claim ledger unavailable")
        return check
    duplicate_ids = set(duplicate_object_ids(evidence_rows, id_field="evidence_id"))
    known = {
        str(row.get("evidence_id")).strip()
        for row in evidence_rows
        if isinstance(row, dict)
        and row.get("evidence_id")
        and str(row.get("evidence_id")).strip() not in duplicate_ids
    }
    if not known:
        message = "no evidence cards were found for this run"
        if require_evidence_cards:
            check.fail(message)
        else:
            check.skip(message)
        return check
    for claim in claims:
        if not isinstance(claim, dict):
            continue
        claim_id = str(claim.get("claim_id", "unknown"))
        for evidence_id in claim.get("evidence_ids") or []:
            if str(evidence_id).strip() not in known:
                check.fail(f"claim {claim_id} references unknown evidence_id: {evidence_id}")
    return check


def _check_unique_evidence_ids(evidence_rows: list[Any]) -> CheckResult:
    check = CheckResult("evidence_ids_are_unique")
    for evidence_id in duplicate_object_ids(evidence_rows, id_field="evidence_id"):
        check.fail(f"evidence cards contain duplicate evidence_id: {evidence_id}")
    return check


def _check_quotes(root: Path, artifacts: RunArtifacts, evidence_rows: list[Any]) -> CheckResult:
    check = CheckResult("evidence_quotes_exist_in_source_text")
    if not evidence_rows:
        check.skip("no evidence cards to verify")
        return check
    texts = _extracted_text(root, artifacts)
    pdf_pages, indexed_pdf_sources = _pdf_page_texts(root, artifacts)
    for row in evidence_rows:
        if not isinstance(row, dict):
            continue
        evidence_id = str(row.get("evidence_id", "unknown"))
        quote = str(row.get("exact_quote", "")).strip()
        source_id = str(row.get("source_id", "")).strip()
        if not quote:
            continue
        locator = row.get("locator") if isinstance(row.get("locator"), dict) else {}
        page = locator.get("page") if isinstance(locator, dict) else None
        if isinstance(page, int) and source_id in indexed_pdf_sources:
            key = (source_id, page)
            if key not in pdf_pages:
                check.fail(
                    f"evidence {evidence_id}: locator page {page} is not indexed for source {source_id}"
                )
                continue
            page_text = pdf_pages[key]
            if page_text is None:
                check.warn(
                    f"evidence {evidence_id}: page {page} text is unavailable for source {source_id}; "
                    "locator could not be verified by code"
                )
                continue
            if _normalize_whitespace(quote) not in page_text:
                check.fail(
                    f"evidence {evidence_id}: exact_quote was not found on locator page {page} "
                    f"of source {source_id}"
                )
            continue
        haystack = texts.get(source_id)
        if haystack is None:
            check.warn(
                f"evidence {evidence_id}: no extracted text for source {source_id}; "
                "quote could not be verified by code"
            )
            continue
        if _normalize_whitespace(quote) not in haystack:
            check.fail(
                f"evidence {evidence_id}: exact_quote was not found in extracted text of {source_id}"
            )
    return check


def _check_images(root: Path, article: str, image_rows: list[dict[str, str]]) -> CheckResult:
    check = CheckResult("kept_images_exist_and_match_hashes")
    if not image_rows:
        check.skip("image ledger has no rows")
        return check
    for index, row in enumerate(image_rows, start=1):
        if (row.get("keep_or_drop") or "").strip().lower() not in KEEP_VALUES:
            continue
        image_path = (row.get("image_path") or "").strip()
        if not image_path:
            check.fail(f"kept image row {index} has no image_path")
            continue
        try:
            resolved = resolve_under_root(root, image_path)
        except ValueError as exc:
            check.fail(f"kept image row {index} path is invalid: {exc}")
            continue
        if not resolved.is_file():
            check.fail(f"kept image file does not exist: {image_path}")
            continue
        recorded_hash = (row.get("image_hash") or "").strip().lower()
        if recorded_hash:
            actual = file_sha256_hex(resolved)
            if actual and actual != recorded_hash.removeprefix("sha256:"):
                check.fail(f"kept image hash mismatch for {image_path}")
        if article and image_path not in article:
            check.fail(f"kept image is not referenced in the article: {image_path}")
        if not (row.get("visual_question") or "").strip():
            check.fail(f"kept image row {index} is missing visual_question")
        if not (row.get("supported_claim_ids") or "").strip():
            check.fail(f"kept image row {index} is missing supported_claim_ids")
        if not (row.get("why_prose_is_insufficient") or "").strip():
            check.fail(f"kept image row {index} is missing why_prose_is_insufficient")
    return check


def _check_image_authority(root: Path, run_id: str, artifacts: RunArtifacts) -> CheckResult:
    check = CheckResult("final_image_ledger_matches_canonical_audit")
    errors = validate_final_image_ledger(
        root=root,
        run_id=run_id,
        artifacts=artifacts,
    )
    if errors:
        for error in errors:
            check.fail(error)
        return check
    if not _read_csv(root, artifacts.image_ledger) and not _read_json(
        root, artifacts.image_audit_report, None
    ):
        check.skip("run has no canonical image decisions")
    return check


def _check_paragraph_map(paragraph_map: Any, article: str, digest: str | None) -> CheckResult:
    check = CheckResult("paragraph_map_matches_frozen_article")
    if not isinstance(paragraph_map, dict) or not paragraph_map:
        check.fail("paragraph map unavailable")
        return check
    recorded = str(paragraph_map.get("article_sha256", "")).strip().lower()
    if not recorded:
        check.fail("paragraph map does not record article_sha256")
    elif digest and recorded != digest:
        check.fail(f"paragraph map article_sha256 mismatch: map={recorded} actual={digest}")
    total_lines = len(article.splitlines())
    for item in paragraph_map.get("paragraphs") or []:
        if not isinstance(item, dict):
            continue
        paragraph_id = str(item.get("paragraph_id", "unknown"))
        line_start = item.get("line_start")
        line_end = item.get("line_end")
        if not isinstance(line_start, int) or not isinstance(line_end, int):
            check.fail(f"paragraph {paragraph_id} has a non-integer line range")
            continue
        if line_start > line_end:
            check.fail(f"paragraph {paragraph_id} has line_start after line_end")
        if total_lines and line_end > total_lines:
            check.fail(
                f"paragraph {paragraph_id} line_end {line_end} exceeds article length {total_lines}"
            )
    return check


def _artifact_hashes(root: Path, artifacts: RunArtifacts) -> dict[str, str | None]:
    targets = {
        "article_result_json": artifacts.article_result,
        "article_md": artifacts.article,
        "claim_ledger_json": artifacts.claim_ledger,
        "source_ledger_csv": artifacts.source_ledger,
        "image_ledger_csv": artifacts.image_ledger,
        "image_audit_json": artifacts.image_audit_report,
        "paragraph_map_json": artifacts.paragraph_map,
        "research_appendix_md": artifacts.research_appendix,
    }
    hashes: dict[str, str | None] = {}
    for key, rel in targets.items():
        try:
            hashes[key] = file_sha256_hex(resolve_under_root(root, rel))
        except ValueError:
            hashes[key] = None
    return hashes


def final_sync_artifact_hashes(
    root: Path,
    artifacts: RunArtifacts,
) -> dict[str, str | None]:
    """Return the current hash set protected by a final-sync report.

    Read-only status surfaces use this same runner-owned mapping instead of duplicating the
    audit's definition of which frozen outputs must still match the recorded report.
    """

    return _artifact_hashes(root, artifacts)


def _regenerated_after_freeze(root: Path, artifacts: RunArtifacts) -> bool:
    """True when every ledger is at least as new as the frozen article."""

    try:
        article_path = resolve_under_root(root, artifacts.article)
    except ValueError:
        return False
    if not article_path.is_file():
        return False
    article_mtime = article_path.stat().st_mtime
    for rel in (
        artifacts.claim_ledger,
        artifacts.source_ledger,
        artifacts.image_ledger,
        artifacts.paragraph_map,
    ):
        try:
            path = resolve_under_root(root, rel)
        except ValueError:
            return False
        if not path.is_file():
            return False
        # Allow a small tolerance for same-second writes.
        if path.stat().st_mtime + 1 < article_mtime:
            return False
    return True


def write_final_sync_report(root: Path, rel: str, payload: dict[str, Any]) -> None:
    target = resolve_under_root(root, rel)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
