"""Paths, configuration, and artifact writers for the A4 publish output.

Publish outputs are derivative artifacts. They live beside the article under
`outputs/{run_id}/publish/` and never replace `article.md`, `source_ledger.csv`,
or `image_ledger.csv` as evidence.
"""

from __future__ import annotations

import csv
import importlib.util
import json
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
from typing import Sequence

from .config import PublishProfile
from .paths import resolve_under_root


PUBLISH_SCHEMA = "schemas/publish_result.schema.json"
PUBLISH_RENDER_SURFACE = "local_deterministic_renderer"
PUBLISH_TYPE = "a4_pdf"

PUBLISH_RENDER_PACKAGE = "reportlab"
PUBLISH_INSTALL_HINT = (
    f"missing optional publish dependency: {PUBLISH_RENDER_PACKAGE}. "
    "A4 PDF publishing cannot run without it. "
    'Install the publish extra first: python3 -m pip install -e ".[publish]"'
)

PUBLISH_FIGURE_MAP_HEADER = [
    "figure_number",
    "image_path",
    "source_id",
    "page_or_slide_number",
    "caption",
    "caption_source",
    "keep_or_drop",
    "article_reference_order",
    "status",
    "notes",
]


@dataclass(frozen=True)
class PublishPaths:
    publish_dir: str
    article_pdf: str
    publish_result: str
    publish_figure_map: str
    publish_manifest: str


@dataclass(frozen=True)
class PublishSourcePaths:
    article_result: str
    article: str
    source_ledger: str
    image_ledger: str


@dataclass(frozen=True)
class PublishRunConfig:
    root: Path
    run_id: str
    schema: str
    paths: PublishPaths
    sources: PublishSourcePaths
    profile: PublishProfile


def publish_dependency_error() -> str | None:
    """Return an actionable message when the PDF renderer cannot run.

    Probed before publish work so an opt-in `--publish-pdf` run fails with
    installation guidance instead of an uncontrolled ModuleNotFoundError.
    """

    if importlib.util.find_spec(PUBLISH_RENDER_PACKAGE) is None:
        return PUBLISH_INSTALL_HINT
    return None


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def build_publish_paths(run_id: str) -> PublishPaths:
    base = f"outputs/{run_id}/publish"
    return PublishPaths(
        publish_dir=base,
        article_pdf=f"{base}/article.pdf",
        publish_result=f"{base}/publish_result.json",
        publish_figure_map=f"{base}/publish_figure_map.csv",
        publish_manifest=f"{base}/publish_manifest.json",
    )


def build_publish_source_paths(run_id: str) -> PublishSourcePaths:
    return PublishSourcePaths(
        article_result=f"outputs/{run_id}/article_result.json",
        article=f"outputs/{run_id}/article.md",
        source_ledger=f"outputs/{run_id}/source_ledger.csv",
        image_ledger=f"outputs/{run_id}/image_ledger.csv",
    )


def _file_hash(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _read_json(path: Path) -> dict[str, object]:
    with path.open(encoding="utf-8") as handle:
        payload = json.load(handle)
    if not isinstance(payload, dict):
        raise ValueError(f"JSON root must be an object: {path}")
    return payload


def _repo_path(root: Path, value: str) -> Path:
    return resolve_under_root(root, value)


def collect_input_errors(config: PublishRunConfig) -> list[str]:
    errors: list[str] = []
    for label, value in [
        ("article_result", config.sources.article_result),
        ("article", config.sources.article),
        ("source_ledger", config.sources.source_ledger),
        ("image_ledger", config.sources.image_ledger),
    ]:
        if not _repo_path(config.root, value).is_file():
            errors.append(f"missing {label}: {value}")
    if not _repo_path(config.root, config.schema).is_file():
        errors.append(f"missing publish schema: {config.schema}")
    return errors


def source_hashes(config: PublishRunConfig) -> dict[str, str]:
    return {
        "article_result_sha256": _file_hash(
            _repo_path(config.root, config.sources.article_result)
        ),
        "article_sha256": _file_hash(_repo_path(config.root, config.sources.article)),
        "source_ledger_sha256": _file_hash(
            _repo_path(config.root, config.sources.source_ledger)
        ),
        "image_ledger_sha256": _file_hash(
            _repo_path(config.root, config.sources.image_ledger)
        ),
    }


def validate_source_hashes(
    config: PublishRunConfig,
    expected_hashes: dict[str, str],
) -> list[str]:
    errors: list[str] = []
    for key, value in [
        ("article_result_sha256", config.sources.article_result),
        ("article_sha256", config.sources.article),
        ("source_ledger_sha256", config.sources.source_ledger),
        ("image_ledger_sha256", config.sources.image_ledger),
    ]:
        path = _repo_path(config.root, value)
        if not path.is_file():
            errors.append(f"publish input missing after rendering: {value}")
            continue
        if _file_hash(path) != expected_hashes.get(key):
            errors.append(f"publish input changed during rendering: {value}")
    return errors


def ensure_publish_directory(config: PublishRunConfig) -> None:
    _repo_path(config.root, config.paths.publish_dir).mkdir(parents=True, exist_ok=True)


def output_files(config: PublishRunConfig) -> dict[str, str]:
    return {
        "article_pdf": config.paths.article_pdf,
        "publish_figure_map_csv": config.paths.publish_figure_map,
        "publish_manifest_json": config.paths.publish_manifest,
    }


def write_figure_map(config: PublishRunConfig, rows: Sequence[dict[str, object]]) -> None:
    target = _repo_path(config.root, config.paths.publish_figure_map)
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=PUBLISH_FIGURE_MAP_HEADER)
        writer.writeheader()
        for row in rows:
            writer.writerow({key: row.get(key, "") for key in PUBLISH_FIGURE_MAP_HEADER})


def write_publish_manifest(
    config: PublishRunConfig,
    *,
    article_status: str,
    hashes: dict[str, str],
    fonts: dict[str, str | None],
    page_count: int,
    figure_count: int,
    warnings: Sequence[str],
) -> None:
    manifest = {
        "version": 1,
        "run_id": config.run_id,
        "generated_at": _now_iso(),
        "publish_type": PUBLISH_TYPE,
        "render_surface": PUBLISH_RENDER_SURFACE,
        "article_status": article_status,
        "sources": asdict(config.sources),
        "source_hashes": hashes,
        "outputs": asdict(config.paths),
        "layout": asdict(config.profile),
        "fonts": fonts,
        "page_count": page_count,
        "figure_count": figure_count,
        "warnings": list(warnings),
    }
    _repo_path(config.root, config.paths.publish_manifest).write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def write_publish_result(
    config: PublishRunConfig,
    *,
    status: str,
    hashes: dict[str, str] | None,
    fonts: dict[str, str | None],
    page_count: int,
    figure_count: int,
    warnings: Sequence[str],
) -> None:
    payload = {
        "publish_type": PUBLISH_TYPE,
        "source_article": config.sources.article,
        "source_article_sha256": (hashes or {}).get("article_sha256", ""),
        "source_result": config.sources.article_result,
        "source_result_sha256": (hashes or {}).get("article_result_sha256", ""),
        "render_surface": PUBLISH_RENDER_SURFACE,
        "page_count": page_count,
        "figure_count": figure_count,
        "font_regular": fonts.get("regular") or "",
        "font_bold": fonts.get("bold"),
        "output_files": output_files(config),
        "critical_warnings": list(warnings),
        "checklist_status": status,
    }
    _repo_path(config.root, config.paths.publish_result).write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def write_failure_publish_result(
    config: PublishRunConfig,
    *,
    warnings: Sequence[str],
    hashes: dict[str, str] | None = None,
    fonts: dict[str, str | None] | None = None,
) -> None:
    write_publish_result(
        config,
        status="fail",
        hashes=hashes,
        fonts=fonts or {"regular": "", "bold": None},
        page_count=0,
        figure_count=0,
        warnings=warnings,
    )
