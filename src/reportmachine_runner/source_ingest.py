from __future__ import annotations

import csv
import json
from hashlib import sha256
from pathlib import Path
from typing import Any, Sequence
from urllib.parse import urlparse

from .paths import resolve_under_root
from .pdf_processing import (
    _extract_pdf_page_text,
    _pdf_page_count,
    _render_pdf_page,
    _term_frequency,
    _text_excerpt,
)
from .source_fetch import (
    _copy_to_run_source,
    _download_to_run_source,
    _host_allowed,
    _is_pdf,
    _normalize_domains,
    _read_sources,
    _relative_to_root,
    _safe_id,
)
from .source_ingest_paths import (
    DEFAULT_ALLOWED_SOURCE_DOMAINS,
    DEFAULT_MAX_DOWNLOAD_BYTES,
    DEFAULT_MAX_IMAGE_INPUTS,
    DEFAULT_MAX_PDF_PAGES,
    DEFAULT_PDF_RENDER_DPI,
    SourceIngestConfig,
    SourceIngestPaths,
    SourceIngestResult,
    build_source_ingest_paths,
)
from .source_ledgers import _empty_manifest, _now_iso, write_source_ingest_artifacts
from .source_text import extract_non_pdf_text, write_extracted_text


SOURCE_INGEST_POLICY_SENTINEL = '"openai_file_search": "disabled"'

# Fields the runner writes back into a research candidate after ingest. They are
# excluded from the assignment fingerprint and are never trusted as model input.
RUNNER_OWNED_CANDIDATE_FIELDS = frozenset(
    {
        "source_id",
        "ingest_status",
        "ingest_reason",
        "ingested_local_path",
        "sha256",
        "extracted_text_path",
        "text_status",
    }
)


def _ensure_source_ingest_dirs(root: Path, paths: SourceIngestPaths) -> None:
    for directory in [
        paths.sources_dir,
        paths.pdf_text_dir,
        paths.extracted_text_dir,
        paths.rendered_images_dir,
        str(Path(paths.manifest).parent),
        str(Path(paths.source_candidate_ledger).parent),
    ]:
        resolve_under_root(root, directory).mkdir(parents=True, exist_ok=True)


def _build_ledger_row(
    *,
    row: dict[str, str],
    source_id: str,
    url: str,
    local_path: str,
    digest: str,
    mime_type: str,
    size_bytes: str,
    fetched_at: str,
    status: str,
    matched_domain: str,
    blocked_reason: str,
) -> dict[str, str]:
    return {
        "source_id": source_id,
        "article_type": row.get("article_type", ""),
        "company": row.get("company", ""),
        "period_role": row.get("period_role", ""),
        "period": row.get("period", ""),
        "doc_kind": row.get("doc_kind", ""),
        "issuer": row.get("issuer", ""),
        "date": row.get("date", ""),
        "title": row.get("title", ""),
        "url": url,
        "source_tier": row.get("source_tier", ""),
        "required": row.get("required", ""),
        "local_path": local_path,
        "sha256": digest,
        "mime_type": mime_type,
        "size_bytes": size_bytes,
        "fetched_at": fetched_at,
        "ingest_status": status,
        "allowed_domain": matched_domain,
        "blocked_reason": blocked_reason,
        "extracted_text_path": "",
        "text_status": "",
        "notes": row.get("notes", ""),
    }


def _ingest_source_row(
    *,
    config: SourceIngestConfig,
    paths: SourceIngestPaths,
    row: dict[str, str],
    source_id: str,
    allowed_domains: tuple[str, ...],
) -> dict[str, str]:
    root = config.root
    url = row.get("url", "").strip()
    local_path_value = row.get("local_path", "").strip()
    status = "skipped"
    blocked_reason = ""
    matched_domain = ""
    local_path = ""
    digest = ""
    mime_type = ""
    size_bytes = ""
    fetched_at = ""

    if not config.enabled:
        status = "remote_unverified"
        blocked_reason = "source ingest disabled"
    elif local_path_value:
        try:
            source_file = resolve_under_root(root, local_path_value)
            if not source_file.is_file():
                status = "blocked"
                blocked_reason = f"local_path does not exist: {local_path_value}"
            else:
                local_path, digest, mime_type, size = _copy_to_run_source(
                    root=root,
                    source=source_file,
                    target_dir=resolve_under_root(root, paths.sources_dir),
                    source_id=source_id,
                )
                size_bytes = str(size)
                fetched_at = _now_iso()
                status = "registered_local"
        except (OSError, ValueError) as exc:
            status = "blocked"
            blocked_reason = str(exc)
    elif url:
        parsed = urlparse(url)
        allowed, matched_domain = _host_allowed(url, allowed_domains)
        if parsed.scheme != "https":
            status = "blocked"
            blocked_reason = "only https source downloads are allowed"
        elif not allowed:
            status = "blocked"
            blocked_reason = "domain is not in the official/allowed source list"
        elif not config.allow_downloads:
            status = "remote_unverified"
            blocked_reason = "download requires --allow-shell-network or profile allow_shell_network=true"
        else:
            try:
                local_path, digest, mime_type, size = _download_to_run_source(
                    root=root,
                    url=url,
                    target_dir=resolve_under_root(root, paths.sources_dir),
                    source_id=source_id,
                    max_bytes=config.max_download_bytes,
                )
                size_bytes = str(size)
                fetched_at = _now_iso()
                status = "downloaded"
            except Exception as exc:  # noqa: BLE001 - record failure in the run ledger.
                status = "blocked"
                blocked_reason = str(exc)
    else:
        status = "remote_unverified"
        blocked_reason = "source row has neither url nor local_path"

    return _build_ledger_row(
        row=row,
        source_id=source_id,
        url=url,
        local_path=local_path,
        digest=digest,
        mime_type=mime_type,
        size_bytes=size_bytes,
        fetched_at=fetched_at,
        status=status,
        matched_domain=matched_domain,
        blocked_reason=blocked_reason,
    )


def _index_pdf_source(
    *,
    config: SourceIngestConfig,
    paths: SourceIngestPaths,
    source_id: str,
    local_path: str,
    mime_type: str,
    digest: str,
    warnings: list[str],
    pdf_index_rows: list[dict[str, Any]],
    rendered_rows: list[dict[str, str]],
    image_inputs: list[str],
) -> tuple[str, str]:
    root = config.root
    source_path = resolve_under_root(root, local_path)
    if not _is_pdf(source_path, mime_type):
        return "not_pdf", ""

    page_count = _pdf_page_count(source_path)
    if page_count <= 0:
        warnings.append(f"could not determine PDF page count for {source_id}")
        return "failed", ""
    page_limit = min(page_count, max(0, config.max_pdf_pages))
    if page_limit < page_count:
        warnings.append(
            f"PDF page processing capped for {source_id}: {page_limit} of {page_count} pages"
        )
    extracted_pages: list[str] = []
    page_statuses: list[str] = []
    for page in range(1, page_limit + 1):
        text_path = resolve_under_root(
            root,
            f"{paths.pdf_text_dir}/{source_id}_page_{page:03d}.txt",
        )
        text_status, text = _extract_pdf_page_text(source_path, page, text_path)
        page_statuses.append(text_status)
        if text_status == "ok" and text.strip():
            extracted_pages.append(text)
        text_rel = _relative_to_root(root, text_path) if text_path.exists() else ""
        image_path, image_hash, render_status, render_reason = _render_pdf_page(
            root=root,
            pdf_path=source_path,
            source_id=source_id,
            page=page,
            output_dir=resolve_under_root(root, paths.rendered_images_dir),
            dpi=config.pdf_render_dpi,
        )
        if image_path and len(image_inputs) < config.max_image_inputs:
            image_inputs.append(image_path)
        term_frequency = _term_frequency(text)
        pdf_index_rows.append(
            {
                "source_id": source_id,
                "page": page,
                "page_count": page_count,
                "source_path": local_path,
                "source_hash": digest,
                "text_path": text_rel,
                "text_status": text_status,
                "rendered_image_path": image_path,
                "render_status": render_status,
                "excerpt": _text_excerpt(text),
                "term_frequency": term_frequency,
                "token_count": sum(term_frequency.values()),
            }
        )
        rendered_rows.append(
            {
                "image_path": image_path,
                "source_doc": local_path,
                "source_id": source_id,
                "page": str(page),
                "source_hash": digest,
                "image_hash": image_hash,
                "render_dpi": str(config.pdf_render_dpi),
                "status": render_status,
                "reason": render_reason,
            }
        )
    if extracted_pages:
        extracted_path = write_extracted_text(
            root=root,
            extracted_text_dir=paths.extracted_text_dir,
            source_id=source_id,
            text="\n\n".join(extracted_pages),
        )
        status = "ok" if page_statuses and all(item == "ok" for item in page_statuses) else "partial"
        return status, extracted_path
    if "tool_missing" in page_statuses:
        return "tool_missing", ""
    return "failed", ""


def _extract_source_text(
    *,
    config: SourceIngestConfig,
    paths: SourceIngestPaths,
    ledger_row: dict[str, str],
    warnings: list[str],
    pdf_index_rows: list[dict[str, Any]],
    rendered_rows: list[dict[str, str]],
    image_inputs: list[str],
) -> None:
    local_path = ledger_row.get("local_path", "")
    if not local_path:
        return
    source_id = ledger_row["source_id"]
    text_status, extracted_path = _index_pdf_source(
        config=config,
        paths=paths,
        source_id=source_id,
        local_path=local_path,
        mime_type=ledger_row.get("mime_type", ""),
        digest=ledger_row.get("sha256", ""),
        warnings=warnings,
        pdf_index_rows=pdf_index_rows,
        rendered_rows=rendered_rows,
        image_inputs=image_inputs,
    )
    if text_status == "not_pdf":
        source_path = resolve_under_root(config.root, local_path)
        text_status, text = extract_non_pdf_text(source_path, ledger_row.get("mime_type", ""))
        if text_status == "ok":
            extracted_path = write_extracted_text(
                root=config.root,
                extracted_text_dir=paths.extracted_text_dir,
                source_id=source_id,
                text=text,
            )
    ledger_row["text_status"] = text_status
    ledger_row["extracted_text_path"] = extracted_path


def run_source_ingest(config: SourceIngestConfig) -> SourceIngestResult:
    paths = build_source_ingest_paths(config.work_run_dir)
    root = config.root
    _ensure_source_ingest_dirs(root, paths)

    warnings: list[str] = []
    source_rows, read_warnings = _read_sources(root, config.sources_path)
    warnings.extend(read_warnings)
    manifest = _empty_manifest(config, paths, warnings)

    source_ledger_rows: list[dict[str, str]] = []
    pdf_index_rows: list[dict[str, Any]] = []
    rendered_rows: list[dict[str, str]] = []
    image_inputs: list[str] = []
    allowed_domains = _normalize_domains(DEFAULT_ALLOWED_SOURCE_DOMAINS + config.allowed_domains)

    if not config.enabled:
        manifest["warnings"].append("source ingest disabled")

    for index, row in enumerate(source_rows, start=1):
        source_id = _safe_id(row.get("source_id", ""), f"source_{index:03d}")
        ledger_row = _ingest_source_row(
            config=config,
            paths=paths,
            row=row,
            source_id=source_id,
            allowed_domains=allowed_domains,
        )
        source_ledger_rows.append(ledger_row)
        manifest["records"].append(ledger_row)

        if ledger_row["local_path"]:
            _extract_source_text(
                config=config,
                paths=paths,
                ledger_row=ledger_row,
                warnings=warnings,
                pdf_index_rows=pdf_index_rows,
                rendered_rows=rendered_rows,
                image_inputs=image_inputs,
            )

    manifest["counts"] = {
        "input_rows": len(source_rows),
        "ingested_sources": sum(
            1 for row in source_ledger_rows if row["ingest_status"] in {"downloaded", "registered_local"}
        ),
        "blocked_sources": sum(
            1
            for row in source_ledger_rows
            if row["ingest_status"] in {"blocked", "remote_unverified", "stale"}
        ),
        "pdf_pages_indexed": len(pdf_index_rows),
        "rendered_images": sum(1 for row in rendered_rows if row["status"] == "ok"),
        "extracted_texts": sum(1 for row in source_ledger_rows if row["extracted_text_path"]),
    }

    write_source_ingest_artifacts(
        root=root,
        paths=paths,
        manifest=manifest,
        source_ledger_rows=source_ledger_rows,
        pdf_index_rows=pdf_index_rows,
        rendered_rows=rendered_rows,
    )

    return SourceIngestResult(
        paths=paths,
        image_inputs=tuple(image_inputs),
        manifest=manifest,
        written_source_ids=tuple(
            row["source_id"]
            for row in source_ledger_rows
            if row.get("source_id")
        ),
        written_image_paths=tuple(
            (row.get("image_path") or "").strip() or f"row_{index:04d}"
            for index, row in enumerate(rendered_rows, start=1)
        ),
    )


def _read_csv_rows(root: Path, rel: str) -> list[dict[str, str]]:
    path = resolve_under_root(root, rel)
    if not path.is_file():
        return []
    with path.open(newline="", encoding="utf-8") as handle:
        return [
            {key: (value or "") for key, value in row.items() if key is not None}
            for row in csv.DictReader(handle)
        ]


def _read_jsonl_rows(root: Path, rel: str) -> tuple[list[dict[str, Any]], list[str]]:
    path = resolve_under_root(root, rel)
    if not path.is_file():
        return [], [f"missing research source candidates: {rel}"]
    rows: list[dict[str, Any]] = []
    errors: list[str] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError as exc:
            errors.append(f"invalid JSONL at {rel}:{line_number}: {exc.msg}")
            continue
        if not isinstance(payload, dict):
            errors.append(f"source candidate row is not an object at {rel}:{line_number}")
            continue
        rows.append(payload)
    return rows, errors


def _write_jsonl_rows(root: Path, rel: str, rows: Sequence[dict[str, Any]]) -> None:
    path = resolve_under_root(root, rel)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in rows),
        encoding="utf-8",
    )


def _read_manifest(config: SourceIngestConfig, paths: SourceIngestPaths) -> dict[str, Any]:
    path = resolve_under_root(config.root, paths.manifest)
    if path.is_file():
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            payload = None
        if isinstance(payload, dict):
            return payload
    return _empty_manifest(config, paths, [])


def _read_pdf_index(root: Path, rel: str) -> list[dict[str, Any]]:
    rows, _errors = _read_jsonl_rows(root, rel)
    return rows


def _existing_image_inputs(
    rendered_rows: Sequence[dict[str, str]],
    *,
    limit: int,
) -> list[str]:
    values: list[str] = []
    for row in rendered_rows:
        image_path = row.get("image_path", "").strip()
        if row.get("status", "").strip() != "ok" or not image_path or image_path in values:
            continue
        if len(values) >= limit:
            break
        values.append(image_path)
    return values


def _changed_row_keys(
    before: Sequence[dict[str, str]],
    after: Sequence[dict[str, str]],
    *,
    key_column: str,
) -> tuple[str, ...]:
    """Return ledger identities whose row content was added or changed."""

    def keyed(rows: Sequence[dict[str, str]]) -> dict[str, str]:
        values: dict[str, str] = {}
        for index, row in enumerate(rows, start=1):
            key = row.get(key_column, "").strip() or f"row_{index:04d}"
            values[key] = json.dumps(row, ensure_ascii=False, sort_keys=True)
        return values

    prior = keyed(before)
    current = keyed(after)
    return tuple(sorted(key for key, value in current.items() if prior.get(key) != value))


def load_source_ingest_result(config: SourceIngestConfig) -> SourceIngestResult | None:
    """Load a prior ingest snapshot so separately invoked phases do not erase research additions."""

    paths = build_source_ingest_paths(config.work_run_dir)
    manifest_path = resolve_under_root(config.root, paths.manifest)
    ledger_path = resolve_under_root(config.root, paths.source_candidate_ledger)
    if not manifest_path.is_file() or not ledger_path.is_file():
        return None
    manifest = _read_manifest(config, paths)
    rendered_rows = _read_csv_rows(config.root, paths.rendered_images_ledger)
    image_inputs = _existing_image_inputs(rendered_rows, limit=config.max_image_inputs)
    return SourceIngestResult(paths=paths, image_inputs=tuple(image_inputs), manifest=manifest)


def _candidate_source_row(config: SourceIngestConfig, candidate: dict[str, Any]) -> dict[str, str]:
    lane_id = str(candidate.get("lane_id") or "").strip()
    search_intent = str(candidate.get("search_intent") or "").strip()
    why_relevant = str(candidate.get("why_relevant") or "").strip()
    notes = "; ".join(
        item
        for item in [
            "research-discovered source",
            f"lane={lane_id}" if lane_id else "",
            f"intent={search_intent}" if search_intent else "",
            why_relevant,
        ]
        if item
    )
    source_role = str(candidate.get("source_role") or "").lower()
    source_tier = "primary" if any(
        token in source_role for token in ("primary", "official", "regulator", "issuer")
    ) else "secondary"
    return {
        "article_type": config.mode,
        "company": "",
        "period_role": "",
        "period": "",
        "doc_kind": str(candidate.get("doc_kind") or ""),
        "issuer": str(candidate.get("issuer") or ""),
        "date": str(candidate.get("publication_date") or ""),
        "title": str(candidate.get("title") or ""),
        "url": str(candidate.get("url") or ""),
        "source_tier": source_tier,
        "required": "false",
        "local_path": str(candidate.get("local_path") or ""),
        "notes": notes,
    }


def _allocate_source_id(candidate_id: str, used: set[str], fallback: str) -> str:
    base = _safe_id(candidate_id, fallback)
    if base not in used:
        used.add(base)
        return base
    suffix = 2
    while f"{base}_{suffix}" in used:
        suffix += 1
    value = f"{base}_{suffix}"
    used.add(value)
    return value


def _candidate_matches_ledger_identity(
    candidate: dict[str, Any],
    ledger_row: dict[str, str],
    *,
    local_path_aliases: dict[str, set[str]],
) -> bool:
    """Allow id-based reuse only when a real external identity matches the ledger row.

    A candidate with neither URL nor registered local path carries no document
    identity, so it must never adopt another candidate's ingested material just
    because both rows happened to use the same lane-local `candidate_id`.
    """

    url = str(candidate.get("url") or "").strip()
    local_path = str(candidate.get("local_path") or "").strip()
    if not url and not local_path:
        return False
    if url and ledger_row.get("url", "").strip() != url:
        return False
    if local_path:
        source_id = ledger_row.get("source_id", "").strip()
        known_local_paths = {
            ledger_row.get("local_path", "").strip(),
            *local_path_aliases.get(source_id, set()),
        }
        if local_path not in known_local_paths:
            return False
    return True


def _candidate_assignment_key(rel: str, candidate: dict[str, Any], ordinal: int) -> str:
    """Fingerprint a raw candidate so repeated ingest reuses its assigned source id.

    Runner-owned enrichment fields are stripped first: they are written back into the
    candidate file by an earlier ingest, so leaving them in would change the key on the
    second pass and allocate a phantom `_2` source for the same discovery.
    """

    payload = {
        key: value
        for key, value in candidate.items()
        if key not in RUNNER_OWNED_CANDIDATE_FIELDS
    }
    canonical = json.dumps(
        {"artifact": rel, "candidate": payload, "ordinal": ordinal},
        ensure_ascii=False,
        sort_keys=True,
    )
    return sha256(canonical.encode("utf-8")).hexdigest()


def _identityless_assignment_errors(
    assignments: dict[str, Any],
    *,
    by_id: dict[str, dict[str, str]],
) -> list[str]:
    """Reject an assignment table that no longer matches the run's source ledger."""

    errors: list[str] = []
    for key, source_id in sorted(assignments.items()):
        row = by_id.get(str(source_id).strip())
        if row is None:
            errors.append(
                "research candidate assignment points at a missing source id: "
                f"{source_id}"
            )
            continue
        if (
            row.get("url", "").strip()
            or row.get("local_path", "").strip()
            or row.get("sha256", "").strip()
            or row.get("extracted_text_path", "").strip()
        ):
            errors.append(
                "research candidate assignment points at a materialized source id: "
                f"{source_id}"
            )
    return errors


def _registered_local_path_aliases(
    config: SourceIngestConfig,
    *,
    by_id: dict[str, dict[str, str]],
) -> dict[str, set[str]]:
    """Map source CSV local paths to the copied run-ledger identity."""

    aliases: dict[str, set[str]] = {
        source_id: {row.get("local_path", "").strip()}
        for source_id, row in by_id.items()
        if row.get("local_path", "").strip()
    }
    input_rows, _warnings = _read_sources(config.root, config.sources_path)
    source_ids = [
        _safe_id(row.get("source_id", ""), f"source_{index:03d}")
        for index, row in enumerate(input_rows, start=1)
    ]
    seen_ids: set[str] = set()
    duplicate_ids: set[str] = set()
    for source_id in source_ids:
        if source_id in seen_ids:
            duplicate_ids.add(source_id)
        seen_ids.add(source_id)
    for source_id, row in zip(source_ids, input_rows):
        local_path = row.get("local_path", "").strip()
        if (
            source_id in duplicate_ids
            or source_id not in by_id
            or not local_path
        ):
            continue
        aliases.setdefault(source_id, set()).add(local_path)
    return aliases


def _candidate_identity_errors(
    parsed_by_path: dict[str, list[dict[str, Any]]],
    *,
    by_url: dict[str, dict[str, str]],
    by_local_path: dict[str, dict[str, str]],
) -> list[str]:
    """Reject candidates whose URL and local path resolve to different source identities."""

    errors: list[str] = []
    for rel, candidates in parsed_by_path.items():
        for index, candidate in enumerate(candidates, start=1):
            url = str(candidate.get("url") or "").strip()
            local_path = str(candidate.get("local_path") or "").strip()
            if not url or not local_path:
                continue
            url_row = by_url.get(url)
            local_row = by_local_path.get(local_path)
            if url_row is None or local_row is None:
                continue
            url_source_id = str((url_row or {}).get("source_id") or "").strip()
            local_source_id = str((local_row or {}).get("source_id") or "").strip()
            if url_source_id and url_source_id == local_source_id:
                continue
            candidate_id = str(candidate.get("candidate_id") or f"row_{index}").strip()
            errors.append(
                f"research candidate {candidate_id} has conflicting URL/local path identity "
                f"in {rel} row {index}"
            )
    return errors


def _refresh_manifest_counts(
    manifest: dict[str, Any],
    *,
    source_rows: Sequence[dict[str, str]],
    pdf_index_rows: Sequence[dict[str, Any]],
    rendered_rows: Sequence[dict[str, str]],
    research_candidates: int,
    research_sources_added: int,
) -> None:
    counts = manifest.setdefault("counts", {})
    previous_candidates = int(counts.get("research_candidates") or 0)
    previous_sources_added = int(counts.get("research_sources_added") or 0)
    counts.update(
        {
            "ingested_sources": sum(
                1
                for row in source_rows
                if row.get("ingest_status") in {"downloaded", "downloaded_hashed", "registered_local"}
            ),
            "blocked_sources": sum(
                1
                for row in source_rows
                if row.get("ingest_status") in {"blocked", "remote_unverified", "stale"}
            ),
            "pdf_pages_indexed": len(pdf_index_rows),
            "rendered_images": sum(1 for row in rendered_rows if row.get("status") == "ok"),
            "extracted_texts": sum(1 for row in source_rows if row.get("extracted_text_path")),
            "research_candidates": max(previous_candidates, research_candidates),
            "research_sources_added": previous_sources_added + research_sources_added,
        }
    )


def run_research_candidate_ingest(
    config: SourceIngestConfig,
    *,
    candidate_paths: Sequence[str],
) -> SourceIngestResult:
    """Promote research-discovered candidates into the hashed run-local source inventory."""

    paths = build_source_ingest_paths(config.work_run_dir)
    _ensure_source_ingest_dirs(config.root, paths)
    parsed_by_path: dict[str, list[dict[str, Any]]] = {}
    errors: list[str] = []
    for rel in candidate_paths:
        rows, row_errors = _read_jsonl_rows(config.root, rel)
        parsed_by_path[rel] = rows
        errors.extend(row_errors)
    manifest = _read_manifest(config, paths)
    rendered_rows = _read_csv_rows(config.root, paths.rendered_images_ledger)
    rendered_rows_before = [dict(row) for row in rendered_rows]
    image_inputs = _existing_image_inputs(rendered_rows, limit=config.max_image_inputs)
    if errors:
        return SourceIngestResult(
            paths=paths,
            image_inputs=tuple(image_inputs),
            manifest=manifest,
            errors=tuple(errors),
        )

    source_rows = _read_csv_rows(config.root, paths.source_candidate_ledger)
    pdf_index_rows = _read_pdf_index(config.root, paths.pdf_index)
    warnings = manifest.setdefault("warnings", [])
    if not isinstance(warnings, list):
        warnings = []
        manifest["warnings"] = warnings
    allowed_domains = _normalize_domains(DEFAULT_ALLOWED_SOURCE_DOMAINS + config.allowed_domains)
    used_ids = {row.get("source_id", "").strip() for row in source_rows if row.get("source_id")}
    by_id = {
        row.get("source_id", "").strip(): row
        for row in source_rows
        if row.get("source_id", "").strip()
    }
    by_url = {
        row.get("url", "").strip(): row
        for row in source_rows
        if row.get("url", "").strip()
    }
    by_local_path = {
        row.get("local_path", "").strip(): row
        for row in source_rows
        if row.get("local_path", "").strip()
    }
    local_path_aliases = _registered_local_path_aliases(config, by_id=by_id)
    for source_id, aliases in local_path_aliases.items():
        ledger_row = by_id.get(source_id)
        if ledger_row is None:
            continue
        for local_path in aliases:
            by_local_path.setdefault(local_path, ledger_row)
    identity_errors = _candidate_identity_errors(
        parsed_by_path,
        by_url=by_url,
        by_local_path=by_local_path,
    )
    if identity_errors:
        return SourceIngestResult(
            paths=paths,
            image_inputs=tuple(image_inputs),
            manifest=manifest,
            errors=tuple(identity_errors),
        )

    raw_assignments = manifest.get("candidate_assignments")
    assignments: dict[str, str] = (
        {
            str(key): str(value)
            for key, value in raw_assignments.items()
            if str(value).strip()
        }
        if isinstance(raw_assignments, dict)
        else {}
    )
    assignment_errors = _identityless_assignment_errors(assignments, by_id=by_id)
    if assignment_errors:
        return SourceIngestResult(
            paths=paths,
            image_inputs=tuple(image_inputs),
            manifest=manifest,
            errors=tuple(assignment_errors),
        )
    research_sources_added = 0
    research_candidates = 0
    written_source_ids: set[str] = set()

    for rel, candidates in parsed_by_path.items():
        updated: list[dict[str, Any]] = []
        assignment_ordinals: dict[str, int] = {}
        for index, candidate in enumerate(candidates, start=1):
            research_candidates += 1
            url = str(candidate.get("url") or "").strip()
            candidate_local_path = str(candidate.get("local_path") or "").strip()
            ledger_row = by_url.get(url) if url else None
            if ledger_row is None and candidate_local_path:
                ledger_row = by_local_path.get(candidate_local_path)
            assignment_key = ""
            if ledger_row is None:
                prior_source_id = str(candidate.get("source_id") or "").strip()
                prior_row = by_id.get(prior_source_id)
                # A candidate-supplied source id is untrusted: honor it only when the
                # candidate still carries a real, matching external identity.
                if prior_row is not None and _candidate_matches_ledger_identity(
                    candidate,
                    prior_row,
                    local_path_aliases=local_path_aliases,
                ):
                    ledger_row = prior_row
            if ledger_row is None and not url and not candidate_local_path:
                # No document identity at all, including `not_found` search outcomes.
                # Reuse is only possible through the runner's own assignment table, so a
                # repeated ingest stays idempotent without ever inheriting real material.
                base_key = _candidate_assignment_key(rel, candidate, 0)
                ordinal = assignment_ordinals.get(base_key, 0)
                assignment_ordinals[base_key] = ordinal + 1
                assignment_key = _candidate_assignment_key(rel, candidate, ordinal)
                assigned_id = assignments.get(assignment_key, "")
                if assigned_id:
                    ledger_row = by_id.get(assigned_id)
            if ledger_row is None:
                source_id = _allocate_source_id(
                    str(candidate.get("candidate_id") or ""),
                    used_ids,
                    f"research_{research_candidates:03d}",
                )
                source_row = _candidate_source_row(config, candidate)
                if candidate_local_path:
                    ledger_row = _build_ledger_row(
                        row=source_row,
                        source_id=source_id,
                        url="",
                        local_path="",
                        digest="",
                        mime_type="",
                        size_bytes="",
                        fetched_at="",
                        status="blocked",
                        matched_domain="",
                        blocked_reason=(
                            "research-discovered local_path is not already registered in the "
                            "run source ledger; add it to inputs/sources.csv and rerun prepare"
                        ),
                    )
                else:
                    ledger_row = _ingest_source_row(
                        config=config,
                        paths=paths,
                        row=source_row,
                        source_id=source_id,
                        allowed_domains=allowed_domains,
                    )
                if ledger_row.get("local_path"):
                    _extract_source_text(
                        config=config,
                        paths=paths,
                        ledger_row=ledger_row,
                        warnings=warnings,
                        pdf_index_rows=pdf_index_rows,
                        rendered_rows=rendered_rows,
                        image_inputs=image_inputs,
                    )
                source_rows.append(ledger_row)
                by_id[source_id] = ledger_row
                if url:
                    by_url[url] = ledger_row
                if ledger_row.get("local_path"):
                    by_local_path[ledger_row["local_path"]] = ledger_row
                if assignment_key:
                    assignments[assignment_key] = source_id
                research_sources_added += 1
                written_source_ids.add(source_id)
            source_id = ledger_row["source_id"]
            enriched = dict(candidate)
            enriched.update(
                {
                    "source_id": source_id,
                    "ingest_status": ledger_row.get("ingest_status", "remote_unverified"),
                    "ingest_reason": ledger_row.get("blocked_reason", ""),
                    "ingested_local_path": ledger_row.get("local_path") or None,
                    "sha256": ledger_row.get("sha256") or None,
                    "extracted_text_path": ledger_row.get("extracted_text_path") or None,
                    "text_status": ledger_row.get("text_status") or None,
                }
            )
            updated.append(enriched)
        _write_jsonl_rows(config.root, rel, updated)

    manifest["records"] = source_rows
    manifest["research_ingested_at"] = _now_iso()
    manifest["candidate_assignments"] = dict(sorted(assignments.items()))
    _refresh_manifest_counts(
        manifest,
        source_rows=source_rows,
        pdf_index_rows=pdf_index_rows,
        rendered_rows=rendered_rows,
        research_candidates=research_candidates,
        research_sources_added=research_sources_added,
    )
    write_source_ingest_artifacts(
        root=config.root,
        paths=paths,
        manifest=manifest,
        source_ledger_rows=source_rows,
        pdf_index_rows=pdf_index_rows,
        rendered_rows=rendered_rows,
    )
    return SourceIngestResult(
        paths=paths,
        image_inputs=tuple(image_inputs[: config.max_image_inputs]),
        manifest=manifest,
        written_source_ids=tuple(sorted(written_source_ids)),
        written_image_paths=_changed_row_keys(
            rendered_rows_before,
            rendered_rows,
            key_column="image_path",
        ),
    )
