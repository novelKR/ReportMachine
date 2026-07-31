"""Closed, read-only artifact access for the localhost Web surface.

The browser selects a logical artifact name, never a filesystem path.  This module is
framework-neutral: callers can hand :class:`ArtifactPayload` to any HTTP framework without
teaching that framework about the repository layout.
"""

from __future__ import annotations

import os
import stat
from dataclasses import dataclass
from enum import Enum
from hashlib import sha256
from pathlib import Path

from ..paths import validate_run_id


class ArtifactError(ValueError):
    """An artifact request is invalid or cannot be served safely."""


class ArtifactName(str, Enum):
    ARTICLE = "article"
    ARTICLE_RESULT = "article_result"
    SOURCE_LEDGER = "source_ledger"
    IMAGE_LEDGER = "image_ledger"
    CLAIM_LEDGER = "claim_ledger"
    PREFLIGHT = "preflight"
    RUN_MANIFEST = "run_manifest"
    FINAL_SYNC = "final_sync"
    PARAGRAPH_MAP = "paragraph_map"
    RESEARCH_APPENDIX = "research_appendix"
    PUBLISH_PDF = "publish_pdf"
    PUBLISH_RESULT = "publish_result"
    SUMMARY_IMAGE = "summary_image"
    SUMMARY_RESULT = "summary_result"


# ``Artifact`` is kept as a concise public spelling for route code.
Artifact = ArtifactName


@dataclass(frozen=True)
class _ArtifactSpec:
    suffix: tuple[str, ...]
    filename: str
    media_type: str


_SPECS: dict[ArtifactName, _ArtifactSpec] = {
    ArtifactName.ARTICLE: _ArtifactSpec(("article.md",), "article.md", "text/markdown; charset=utf-8"),
    ArtifactName.ARTICLE_RESULT: _ArtifactSpec(("article_result.json",), "article_result.json", "application/json"),
    ArtifactName.SOURCE_LEDGER: _ArtifactSpec(("source_ledger.csv",), "source_ledger.csv", "text/csv; charset=utf-8"),
    ArtifactName.IMAGE_LEDGER: _ArtifactSpec(("image_ledger.csv",), "image_ledger.csv", "text/csv; charset=utf-8"),
    ArtifactName.CLAIM_LEDGER: _ArtifactSpec(("claim_ledger.json",), "claim_ledger.json", "application/json"),
    ArtifactName.PREFLIGHT: _ArtifactSpec(("preflight_result.json",), "preflight_result.json", "application/json"),
    ArtifactName.RUN_MANIFEST: _ArtifactSpec(("run_manifest.json",), "run_manifest.json", "application/json"),
    ArtifactName.FINAL_SYNC: _ArtifactSpec(("final_sync_report.json",), "final_sync_report.json", "application/json"),
    ArtifactName.PARAGRAPH_MAP: _ArtifactSpec(("paragraph_map.json",), "paragraph_map.json", "application/json"),
    ArtifactName.RESEARCH_APPENDIX: _ArtifactSpec(("research_appendix.md",), "research_appendix.md", "text/markdown; charset=utf-8"),
    ArtifactName.PUBLISH_PDF: _ArtifactSpec(("publish", "article.pdf"), "article.pdf", "application/pdf"),
    ArtifactName.PUBLISH_RESULT: _ArtifactSpec(("publish", "publish_result.json"), "publish_result.json", "application/json"),
    ArtifactName.SUMMARY_IMAGE: _ArtifactSpec(("summary", "summary_a4.png"), "summary_a4.png", "image/png"),
    ArtifactName.SUMMARY_RESULT: _ArtifactSpec(("summary", "summary_result.json"), "summary_result.json", "application/json"),
}


@dataclass(frozen=True)
class ArtifactPayload:
    """Safe file bytes and response metadata; no framework response is constructed here."""

    artifact: ArtifactName
    run_id: str
    filename: str
    media_type: str
    content_disposition: str
    size: int
    sha256: str
    data: bytes

    def metadata(self) -> dict[str, str | int]:
        return {
            "artifact": self.artifact.value,
            "run_id": self.run_id,
            "filename": self.filename,
            "media_type": self.media_type,
            "content_disposition": self.content_disposition,
            "size": self.size,
            "sha256": self.sha256,
        }


def _coerce_artifact(value: ArtifactName | str) -> ArtifactName:
    if isinstance(value, ArtifactName):
        return value
    if not isinstance(value, str):
        raise ArtifactError("artifact must be a closed logical artifact name")
    try:
        return ArtifactName(value)
    except ValueError as exc:
        raise ArtifactError("unknown logical artifact") from exc


def _safe_response_metadata(spec: _ArtifactSpec) -> tuple[str, str]:
    """Return response metadata that never executes an artifact in a browser."""

    filename = spec.filename
    # No current logical artifact is active content, but retain this guard for any future
    # closed-enum addition.  An active payload must be downloaded, never browser-rendered.
    if Path(filename).suffix.casefold() in {".html", ".htm", ".js", ".mjs", ".svg"}:
        return "text/plain; charset=utf-8", f'attachment; filename="{filename}"'
    return spec.media_type, f'attachment; filename="{filename}"'


def _read_regular_under(root: Path, relative: tuple[str, ...]) -> bytes:
    """Read a known relative file while rejecting every symlink in its path."""

    current = root
    try:
        root_info = current.lstat()
    except OSError as exc:
        raise ArtifactError(f"cannot inspect project root: {exc}") from exc
    if stat.S_ISLNK(root_info.st_mode) or not stat.S_ISDIR(root_info.st_mode):
        raise ArtifactError("project root must be a real directory")
    for index, part in enumerate(relative):
        if part in {"", ".", ".."} or Path(part).name != part:
            raise ArtifactError("invalid artifact path component")
        current = current / part
        try:
            info = current.lstat()
        except FileNotFoundError as exc:
            raise ArtifactError("artifact is not available") from exc
        except OSError as exc:
            raise ArtifactError(f"cannot inspect artifact: {exc}") from exc
        if stat.S_ISLNK(info.st_mode):
            raise ArtifactError("artifact path must not contain a symlink")
        if index < len(relative) - 1 and not stat.S_ISDIR(info.st_mode):
            raise ArtifactError("artifact parent must be a real directory")
    if not stat.S_ISREG(info.st_mode):
        raise ArtifactError("artifact must be a regular file")
    flags = os.O_RDONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(current, flags)
        try:
            opened = os.fstat(descriptor)
            if not stat.S_ISREG(opened.st_mode):
                raise ArtifactError("artifact must be a regular file")
            with os.fdopen(descriptor, "rb", closefd=False) as handle:
                return handle.read()
        finally:
            os.close(descriptor)
    except ArtifactError:
        raise
    except OSError as exc:
        raise ArtifactError(f"cannot read artifact safely: {exc}") from exc


class ArtifactBroker:
    """Serve only the fixed output set of one validated run directory."""

    def __init__(self, project_root: Path) -> None:
        self.project_root = Path(project_root).expanduser().resolve()

    @staticmethod
    def validate_run_id(run_id: str) -> str:
        if not isinstance(run_id, str):
            raise ArtifactError("run_id must be a string")
        try:
            return validate_run_id(run_id, "run_id")
        except ValueError as exc:
            raise ArtifactError(str(exc)) from exc

    def read(self, run_id: str, artifact: ArtifactName | str) -> ArtifactPayload:
        checked_run_id = self.validate_run_id(run_id)
        name = _coerce_artifact(artifact)
        spec = _SPECS[name]
        data = _read_regular_under(
            self.project_root,
            ("outputs", checked_run_id, *spec.suffix),
        )
        media_type, disposition = _safe_response_metadata(spec)
        return ArtifactPayload(
            artifact=name,
            run_id=checked_run_id,
            filename=spec.filename,
            media_type=media_type,
            content_disposition=disposition,
            size=len(data),
            sha256=sha256(data).hexdigest(),
            data=data,
        )

    get = read


__all__ = [
    "Artifact",
    "ArtifactBroker",
    "ArtifactError",
    "ArtifactName",
    "ArtifactPayload",
]
