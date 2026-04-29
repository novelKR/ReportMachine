from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any


DEFAULT_ALLOWED_SOURCE_DOMAINS = (
    "sec.gov",
    "federalreserve.gov",
    "bls.gov",
    "bea.gov",
    "census.gov",
    "treasury.gov",
    "commerce.gov",
    "energy.gov",
    "nist.gov",
    "ec.europa.eu",
    "europa.eu",
    ".gov",
)
DEFAULT_MAX_DOWNLOAD_BYTES = 50 * 1024 * 1024
DEFAULT_PDF_RENDER_DPI = 144
DEFAULT_MAX_PDF_PAGES = 40
DEFAULT_MAX_IMAGE_INPUTS = 20


@dataclass(frozen=True)
class SourceIngestPaths:
    manifest: str
    source_candidate_ledger: str
    pdf_index: str
    rendered_images_ledger: str
    sources_dir: str
    pdf_text_dir: str
    rendered_images_dir: str


@dataclass(frozen=True)
class SourceIngestConfig:
    root: Path
    mode: str
    run_id: str
    sources_path: str
    work_run_dir: str
    allow_downloads: bool
    enabled: bool = True
    allowed_domains: tuple[str, ...] = ()
    max_download_bytes: int = DEFAULT_MAX_DOWNLOAD_BYTES
    pdf_render_dpi: int = DEFAULT_PDF_RENDER_DPI
    max_pdf_pages: int = DEFAULT_MAX_PDF_PAGES
    max_image_inputs: int = DEFAULT_MAX_IMAGE_INPUTS


@dataclass(frozen=True)
class SourceIngestResult:
    paths: SourceIngestPaths
    image_inputs: tuple[str, ...]
    manifest: dict[str, Any]


def build_source_ingest_paths(work_run_dir: str) -> SourceIngestPaths:
    base = Path(work_run_dir).as_posix().rstrip("/")
    return SourceIngestPaths(
        manifest=f"{base}/research/source_ingest_manifest.json",
        source_candidate_ledger=f"{base}/ledgers/source_ledger.candidate.csv",
        pdf_index=f"{base}/research/pdf_index.jsonl",
        rendered_images_ledger=f"{base}/ledgers/rendered_images.csv",
        sources_dir=f"{base}/research/sources",
        pdf_text_dir=f"{base}/research/pdf_text",
        rendered_images_dir=f"{base}/images/rendered",
    )
