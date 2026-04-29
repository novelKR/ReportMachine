from __future__ import annotations

import csv
import mimetypes
import re
import shutil
import urllib.error
import urllib.request
from hashlib import sha256
from pathlib import Path
from urllib.parse import urlparse

from .paths import resolve_under_root


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
