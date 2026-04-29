from __future__ import annotations

import csv
import json
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .paths import resolve_under_root
from .source_fetch import _normalize_domains
from .source_ingest_paths import DEFAULT_ALLOWED_SOURCE_DOMAINS, SourceIngestConfig, SourceIngestPaths


SOURCE_LEDGER_CANDIDATE_HEADER = [
    "source_id",
    "article_type",
    "company",
    "period_role",
    "period",
    "doc_kind",
    "issuer",
    "date",
    "title",
    "url",
    "source_tier",
    "required",
    "local_path",
    "sha256",
    "mime_type",
    "size_bytes",
    "fetched_at",
    "ingest_status",
    "allowed_domain",
    "blocked_reason",
    "notes",
]

RENDERED_IMAGES_HEADER = [
    "image_path",
    "source_doc",
    "source_id",
    "page",
    "source_hash",
    "image_hash",
    "render_dpi",
    "status",
    "reason",
]


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _empty_manifest(config: SourceIngestConfig, paths: SourceIngestPaths, warnings: list[str]) -> dict[str, Any]:
    return {
        "version": 1,
        "run_id": config.run_id,
        "mode": config.mode,
        "generated_at": _now_iso(),
        "policy": {
            "repo_local_only": True,
            "download_policy": "official_or_allowed_domains",
            "allow_downloads": config.allow_downloads,
            "allowed_domains": list(
                _normalize_domains(DEFAULT_ALLOWED_SOURCE_DOMAINS + config.allowed_domains)
            ),
            "max_download_bytes": config.max_download_bytes,
            "pdf_render_dpi": config.pdf_render_dpi,
            "max_pdf_pages": config.max_pdf_pages,
            "max_image_inputs": config.max_image_inputs,
            "openai_file_search": "disabled",
        },
        "paths": asdict(paths),
        "counts": {
            "input_rows": 0,
            "ingested_sources": 0,
            "blocked_sources": 0,
            "pdf_pages_indexed": 0,
            "rendered_images": 0,
        },
        "records": [],
        "warnings": warnings,
    }


def write_source_ingest_artifacts(
    *,
    root: Path,
    paths: SourceIngestPaths,
    manifest: dict[str, Any],
    source_ledger_rows: list[dict[str, str]],
    pdf_index_rows: list[dict[str, Any]],
    rendered_rows: list[dict[str, str]],
) -> None:
    source_ledger_path = resolve_under_root(root, paths.source_candidate_ledger)
    with source_ledger_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=SOURCE_LEDGER_CANDIDATE_HEADER)
        writer.writeheader()
        writer.writerows(source_ledger_rows)

    rendered_images_path = resolve_under_root(root, paths.rendered_images_ledger)
    with rendered_images_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=RENDERED_IMAGES_HEADER)
        writer.writeheader()
        writer.writerows(rendered_rows)

    pdf_index_path = resolve_under_root(root, paths.pdf_index)
    with pdf_index_path.open("w", encoding="utf-8") as handle:
        for item in pdf_index_rows:
            handle.write(json.dumps(item, ensure_ascii=False, sort_keys=True) + "\n")

    manifest_path = resolve_under_root(root, paths.manifest)
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
