from __future__ import annotations

from pathlib import Path
from typing import Any
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


SOURCE_INGEST_POLICY_SENTINEL = '"openai_file_search": "disabled"'


def _ensure_source_ingest_dirs(root: Path, paths: SourceIngestPaths) -> None:
    for directory in [
        paths.sources_dir,
        paths.pdf_text_dir,
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
        status = "disabled"
        blocked_reason = "source ingest disabled"
    elif local_path_value:
        try:
            source_file = resolve_under_root(root, local_path_value)
            if not source_file.is_file():
                status = "missing_local_path"
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
            status = "failed"
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
            status = "network_disabled"
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
                status = "failed"
                blocked_reason = str(exc)
    else:
        status = "missing_location"
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
) -> None:
    root = config.root
    source_path = resolve_under_root(root, local_path)
    if not _is_pdf(source_path, mime_type):
        return

    page_count = _pdf_page_count(source_path)
    if page_count <= 0:
        warnings.append(f"could not determine PDF page count for {source_id}")
        return
    page_limit = min(page_count, max(0, config.max_pdf_pages))
    if page_limit < page_count:
        warnings.append(
            f"PDF page processing capped for {source_id}: {page_limit} of {page_count} pages"
        )
    for page in range(1, page_limit + 1):
        text_path = resolve_under_root(
            root,
            f"{paths.pdf_text_dir}/{source_id}_page_{page:03d}.txt",
        )
        text_status, text = _extract_pdf_page_text(source_path, page, text_path)
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
            _index_pdf_source(
                config=config,
                paths=paths,
                source_id=source_id,
                local_path=ledger_row["local_path"],
                mime_type=ledger_row["mime_type"],
                digest=ledger_row["sha256"],
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
            1 for row in source_ledger_rows if row["ingest_status"] in {"blocked", "network_disabled"}
        ),
        "pdf_pages_indexed": len(pdf_index_rows),
        "rendered_images": sum(1 for row in rendered_rows if row["status"] == "ok"),
    }

    write_source_ingest_artifacts(
        root=root,
        paths=paths,
        manifest=manifest,
        source_ledger_rows=source_ledger_rows,
        pdf_index_rows=pdf_index_rows,
        rendered_rows=rendered_rows,
    )

    return SourceIngestResult(paths=paths, image_inputs=tuple(image_inputs), manifest=manifest)
