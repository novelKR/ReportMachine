from __future__ import annotations

import csv
import json
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
from typing import Sequence

from .config import SummaryProfile
from .paths import resolve_under_root


SUMMARY_SCHEMA = "schemas/summary_result.schema.json"
SUMMARY_IMAGE_GENERATION_SURFACE = "codex_internal_tool"
SUMMARY_CLAIM_MAP_HEADER = [
    "summary_item_id",
    "summary_text",
    "claim_type",
    "source_artifact",
    "source_path",
    "source_anchor",
    "source_url",
    "notes",
]
SUMMARY_CLAIM_MAP_REQUIRED_FIELDS = (
    "summary_item_id",
    "summary_text",
    "claim_type",
    "source_artifact",
    "source_path",
    "source_anchor",
)
SUMMARY_CLAIM_MAP_SOURCE_ARTIFACTS = (
    "article.md",
    "source_ledger.csv",
    "image_ledger.csv",
)


@dataclass(frozen=True)
class SummaryPaths:
    summary_dir: str
    summary_result: str
    summary_image: str
    summary_prompt: str
    summary_claim_map: str
    summary_manifest: str


@dataclass(frozen=True)
class SummarySourcePaths:
    article_result: str
    article: str
    source_ledger: str
    image_ledger: str


@dataclass(frozen=True)
class SummaryRunConfig:
    root: Path
    run_id: str
    codex_bin: str
    schema: str
    web_search: str
    paths: SummaryPaths
    sources: SummarySourcePaths
    profile: SummaryProfile


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def build_summary_paths(run_id: str) -> SummaryPaths:
    base = f"outputs/{run_id}/summary"
    return SummaryPaths(
        summary_dir=base,
        summary_result=f"{base}/summary_result.json",
        summary_image=f"{base}/summary_a4.png",
        summary_prompt=f"{base}/summary_prompt.md",
        summary_claim_map=f"{base}/summary_claim_map.csv",
        summary_manifest=f"{base}/summary_manifest.json",
    )


def build_summary_source_paths(run_id: str) -> SummarySourcePaths:
    return SummarySourcePaths(
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


def collect_input_errors(config: SummaryRunConfig) -> list[str]:
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
        errors.append(f"missing summary schema: {config.schema}")
    return errors


def source_hashes(config: SummaryRunConfig) -> dict[str, str]:
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
    config: SummaryRunConfig,
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
            errors.append(f"summary input missing after generation: {value}")
            continue
        actual = _file_hash(path)
        if actual != expected_hashes.get(key):
            errors.append(f"summary input changed during generation: {value}")
    return errors


def ensure_summary_directory(config: SummaryRunConfig) -> None:
    _repo_path(config.root, config.paths.summary_dir).mkdir(parents=True, exist_ok=True)


def write_claim_map_header(config: SummaryRunConfig) -> None:
    claim_map = _repo_path(config.root, config.paths.summary_claim_map)
    claim_map.parent.mkdir(parents=True, exist_ok=True)
    with claim_map.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=SUMMARY_CLAIM_MAP_HEADER)
        writer.writeheader()


def write_summary_manifest(
    config: SummaryRunConfig,
    *,
    article_status: str,
    hashes: dict[str, str],
) -> None:
    manifest = {
        "version": 1,
        "run_id": config.run_id,
        "generated_at": _now_iso(),
        "summary_type": "a4_visual",
        "image_generation_surface": SUMMARY_IMAGE_GENERATION_SURFACE,
        "article_status": article_status,
        "sources": asdict(config.sources),
        "source_hashes": hashes,
        "outputs": asdict(config.paths),
        "style": asdict(config.profile),
    }
    _repo_path(config.root, config.paths.summary_manifest).write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def write_summary_prompt_file(config: SummaryRunConfig, prompt: str) -> None:
    _repo_path(config.root, config.paths.summary_prompt).write_text(
        prompt + "\n",
        encoding="utf-8",
    )


def _output_files(config: SummaryRunConfig) -> dict[str, str]:
    return {
        "summary_a4_png": config.paths.summary_image,
        "summary_prompt_md": config.paths.summary_prompt,
        "summary_claim_map_csv": config.paths.summary_claim_map,
        "summary_manifest_json": config.paths.summary_manifest,
    }


def write_failure_summary_result(
    config: SummaryRunConfig,
    *,
    warnings: Sequence[str],
    hashes: dict[str, str] | None = None,
) -> None:
    payload = {
        "summary_type": "a4_visual",
        "source_article": config.sources.article,
        "source_article_sha256": (hashes or {}).get("article_sha256", ""),
        "source_result": config.sources.article_result,
        "source_result_sha256": (hashes or {}).get("article_result_sha256", ""),
        "image_generation_surface": SUMMARY_IMAGE_GENERATION_SURFACE,
        "output_files": _output_files(config),
        "critical_warnings": list(warnings),
        "checklist_status": "fail",
    }
    _repo_path(config.root, config.paths.summary_result).write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
