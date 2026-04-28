from __future__ import annotations

import csv
import json
import mimetypes
import re
import shutil
import subprocess
import urllib.error
import urllib.request
from collections import Counter
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from .paths import resolve_under_root


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


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _safe_id(value: str, fallback: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_.-]+", "_", value.strip()).strip("._-")
    if not cleaned:
        return fallback
    if not re.match(r"^[A-Za-z0-9]", cleaned):
        cleaned = f"s_{cleaned}"
    return cleaned[:80]


def _hash_file(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _is_pdf(path: Path, mime_type: str) -> bool:
    if mime_type.lower().split(";")[0].strip() == "application/pdf":
        return True
    if path.suffix.lower() == ".pdf":
        return True
    try:
        with path.open("rb") as handle:
            return handle.read(5) == b"%PDF-"
    except OSError:
        return False


def _guess_mime(path: Path, supplied: str = "") -> str:
    if supplied:
        return supplied.split(";")[0].strip().lower()
    try:
        with path.open("rb") as handle:
            if handle.read(5) == b"%PDF-":
                return "application/pdf"
    except OSError:
        pass
    guessed, _encoding = mimetypes.guess_type(path.name)
    return guessed or "application/octet-stream"


def _extension_for(url: str, mime_type: str, fallback_path: Path | None = None) -> str:
    mime = mime_type.lower().split(";")[0].strip()
    if mime == "application/pdf":
        return ".pdf"
    if mime.startswith("image/"):
        ext = mimetypes.guess_extension(mime)
        if ext:
            return ".jpg" if ext == ".jpe" else ext
    parsed_suffix = Path(urlparse(url).path).suffix.lower()
    if parsed_suffix in {".pdf", ".png", ".jpg", ".jpeg", ".webp", ".gif", ".csv", ".txt", ".html"}:
        return parsed_suffix
    if fallback_path and fallback_path.suffix:
        return fallback_path.suffix.lower()
    return ".bin"


def _normalize_domains(domains: tuple[str, ...]) -> tuple[str, ...]:
    normalized: list[str] = []
    for domain in domains:
        value = domain.strip().lower().rstrip(".")
        if value and value not in normalized:
            normalized.append(value)
    return tuple(normalized)


def _host_allowed(url: str, allowed_domains: tuple[str, ...]) -> tuple[bool, str]:
    host = (urlparse(url).hostname or "").lower().rstrip(".")
    if not host:
        return False, ""
    for domain in allowed_domains:
        if domain.startswith("."):
            suffix = domain[1:]
            if host == suffix or host.endswith(domain):
                return True, domain
        elif host == domain or host.endswith(f".{domain}"):
            return True, domain
    return False, ""


def _read_sources(root: Path, sources_path: str) -> tuple[list[dict[str, str]], list[str]]:
    source_file = resolve_under_root(root, sources_path)
    warnings: list[str] = []
    if not source_file.is_file():
        return [], [f"missing sources file: {sources_path}"]
    with source_file.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        rows = [
            {key: (value or "") for key, value in row.items() if key is not None}
            for row in reader
        ]
    if not rows:
        warnings.append(f"sources file has no source rows: {sources_path}")
    return rows, warnings


def _relative_to_root(root: Path, path: Path) -> str:
    return path.resolve().relative_to(root.resolve()).as_posix()


def _copy_to_run_source(
    *,
    root: Path,
    source: Path,
    target_dir: Path,
    source_id: str,
    mime_type: str = "",
) -> tuple[str, str, str, int]:
    target_dir.mkdir(parents=True, exist_ok=True)
    supplied_mime = _guess_mime(source, mime_type)
    temp_target = target_dir / f".{source_id}.stage"
    shutil.copy2(source, temp_target)
    digest = _hash_file(temp_target)
    ext = _extension_for("", supplied_mime, source)
    final_target = target_dir / f"{source_id}-{digest[:12]}{ext}"
    if final_target.exists():
        final_target.unlink()
    temp_target.replace(final_target)
    return _relative_to_root(root, final_target), digest, _guess_mime(final_target, supplied_mime), final_target.stat().st_size


def _download_to_run_source(
    *,
    root: Path,
    url: str,
    target_dir: Path,
    source_id: str,
    max_bytes: int,
) -> tuple[str, str, str, int]:
    target_dir.mkdir(parents=True, exist_ok=True)
    request = urllib.request.Request(
        url,
        headers={"User-Agent": "ReportMachine source ingest/0.1"},
    )
    temp_target = target_dir / f".{source_id}.download"
    digest = sha256()
    size = 0
    mime_type = ""
    with urllib.request.urlopen(request, timeout=30) as response:
        status = getattr(response, "status", 200)
        if status >= 400:
            raise urllib.error.HTTPError(url, status, "download failed", response.headers, None)
        mime_type = response.headers.get("Content-Type", "")
        with temp_target.open("wb") as handle:
            while True:
                chunk = response.read(1024 * 1024)
                if not chunk:
                    break
                size += len(chunk)
                if size > max_bytes:
                    raise ValueError(f"download exceeds max_download_bytes ({max_bytes})")
                digest.update(chunk)
                handle.write(chunk)
    ext = _extension_for(url, mime_type)
    final_target = target_dir / f"{source_id}-{digest.hexdigest()[:12]}{ext}"
    if final_target.exists():
        final_target.unlink()
    temp_target.replace(final_target)
    return _relative_to_root(root, final_target), digest.hexdigest(), _guess_mime(final_target, mime_type), size


def _pdf_page_count(pdf_path: Path) -> int:
    pdfinfo = shutil.which("pdfinfo")
    if pdfinfo:
        result = subprocess.run(
            [pdfinfo, str(pdf_path)],
            check=False,
            capture_output=True,
            text=True,
        )
        if result.returncode == 0:
            for line in result.stdout.splitlines():
                if line.lower().startswith("pages:"):
                    _, value = line.split(":", 1)
                    try:
                        return max(0, int(value.strip()))
                    except ValueError:
                        return 0
    try:
        data = pdf_path.read_bytes()
    except OSError:
        return 0
    return len(re.findall(rb"/Type\s*/Page\b", data))


def _text_excerpt(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()[:500]


def _term_frequency(text: str) -> dict[str, int]:
    tokens = re.findall(r"[A-Za-z0-9][A-Za-z0-9_.%-]*", text.lower())
    counts = Counter(token for token in tokens if len(token) > 1)
    return dict(counts.most_common(200))


def _extract_pdf_page_text(pdf_path: Path, page: int, text_path: Path) -> tuple[str, str]:
    pdftotext = shutil.which("pdftotext")
    if not pdftotext:
        return "tool_missing", ""
    text_path.parent.mkdir(parents=True, exist_ok=True)
    result = subprocess.run(
        [
            pdftotext,
            "-layout",
            "-enc",
            "UTF-8",
            "-f",
            str(page),
            "-l",
            str(page),
            str(pdf_path),
            str(text_path),
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        return "failed", result.stderr.strip()[:500]
    return "ok", text_path.read_text(encoding="utf-8", errors="replace")


def _render_pdf_page(
    *,
    root: Path,
    pdf_path: Path,
    source_id: str,
    page: int,
    output_dir: Path,
    dpi: int,
) -> tuple[str, str, str, str]:
    pdftoppm = shutil.which("pdftoppm")
    if not pdftoppm:
        return "", "", "tool_missing", "pdftoppm not found"
    output_dir.mkdir(parents=True, exist_ok=True)
    target = output_dir / f"{source_id}_page_{page:03d}.png"
    prefix = output_dir / f".{source_id}_page_{page:03d}"
    for stale in output_dir.glob(f"{prefix.name}*.png"):
        stale.unlink()
    result = subprocess.run(
        [
            pdftoppm,
            "-png",
            "-r",
            str(dpi),
            "-f",
            str(page),
            "-l",
            str(page),
            str(pdf_path),
            str(prefix),
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        return "", "", "failed", result.stderr.strip()[:500]
    generated = sorted(output_dir.glob(f"{prefix.name}*.png"))
    if not generated:
        return "", "", "failed", "pdftoppm produced no image"
    if target.exists():
        target.unlink()
    generated[0].replace(target)
    for extra in generated[1:]:
        extra.unlink()
    return _relative_to_root(root, target), _hash_file(target), "ok", ""


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


def run_source_ingest(config: SourceIngestConfig) -> SourceIngestResult:
    paths = build_source_ingest_paths(config.work_run_dir)
    root = config.root
    for directory in [
        paths.sources_dir,
        paths.pdf_text_dir,
        paths.rendered_images_dir,
        str(Path(paths.manifest).parent),
        str(Path(paths.source_candidate_ledger).parent),
    ]:
        resolve_under_root(root, directory).mkdir(parents=True, exist_ok=True)

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
        fallback_id = f"source_{index:03d}"
        source_id = _safe_id(row.get("source_id", ""), fallback_id)
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

        ledger_row = {
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
        source_ledger_rows.append(ledger_row)
        manifest["records"].append(ledger_row)

        if not local_path:
            continue

        source_path = resolve_under_root(root, local_path)
        if not _is_pdf(source_path, mime_type):
            continue

        page_count = _pdf_page_count(source_path)
        if page_count <= 0:
            warnings.append(f"could not determine PDF page count for {source_id}")
            continue
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

    return SourceIngestResult(paths=paths, image_inputs=tuple(image_inputs), manifest=manifest)
