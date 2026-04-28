from __future__ import annotations

import json
import shutil
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from .paths import resolve_under_root


FINAL_ARTIFACT_NAMES = (
    "article_result.json",
    "article.md",
    "source_ledger.csv",
    "image_ledger.csv",
)


@dataclass(frozen=True)
class ArtifactSyncConfig:
    external_root: Path | None = None
    import_existing: bool = False
    snapshot_interval_seconds: int = 0
    cleanup_cache_on_success: bool = False
    preserve_cache_on_failure: bool = True

    @property
    def enabled(self) -> bool:
        return self.external_root is not None


@dataclass(frozen=True)
class ArtifactPaths:
    root: Path
    run_id: str
    work_run_dir: str
    output: str
    article_output: str
    source_ledger_output: str
    image_ledger_output: str
    summary_dir: str | None = None


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _atomic_replace_dir(source: Path, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    temp_target = target.with_name(f".{target.name}.tmp")
    if temp_target.exists():
        shutil.rmtree(temp_target)
    if target.exists():
        if target.is_dir():
            shutil.rmtree(target)
        else:
            target.unlink()
    source.replace(temp_target)
    temp_target.replace(target)


def _copy_dir(source: Path, target: Path) -> None:
    if target.exists():
        shutil.rmtree(target)
    shutil.copytree(source, target)


def _copy_existing_file(source: Path, target: Path) -> bool:
    if not source.is_file():
        return False
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, target)
    return True


def _write_manifest(target: Path, *, paths: ArtifactPaths, kind: str) -> None:
    manifest = {
        "kind": kind,
        "run_id": paths.run_id,
        "generated_at": _now_iso(),
        "files": {
            "article_result_json": "article_result.json",
            "article_md": "article.md",
            "source_ledger_csv": "source_ledger.csv",
            "image_ledger_csv": "image_ledger.csv",
        },
        "internal_paths": {
            "work_run_dir": paths.work_run_dir,
            "article_result_json": paths.output,
            "article_md": paths.article_output,
            "source_ledger_csv": paths.source_ledger_output,
            "image_ledger_csv": paths.image_ledger_output,
            "summary_dir": paths.summary_dir,
        },
    }
    (target / "reportmachine_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def import_existing_artifacts(sync: ArtifactSyncConfig, paths: ArtifactPaths) -> None:
    if not sync.enabled or not sync.import_existing:
        return

    assert sync.external_root is not None
    imports = [
        ("run_snapshot", sync.external_root / "runs" / paths.run_id / "snapshot"),
        ("latest", sync.external_root / "latest"),
    ]
    destination_root = resolve_under_root(paths.root, paths.work_run_dir) / "imported"
    for name, source in imports:
        if source.is_dir():
            _copy_dir(source, destination_root / name)


def snapshot_external_artifacts(sync: ArtifactSyncConfig, paths: ArtifactPaths) -> None:
    if not sync.enabled:
        return

    assert sync.external_root is not None
    target = sync.external_root / "runs" / paths.run_id / "snapshot"
    temp = target.with_name(f".{target.name}.stage")
    if temp.exists():
        shutil.rmtree(temp)
    temp.mkdir(parents=True, exist_ok=True)

    work_run_dir = resolve_under_root(paths.root, paths.work_run_dir)
    if work_run_dir.is_dir():
        shutil.copytree(work_run_dir, temp / "work_run")

    outputs_dir = temp / "outputs"
    for source_value, name in [
        (paths.output, "article_result.json"),
        (paths.article_output, "article.md"),
        (paths.source_ledger_output, "source_ledger.csv"),
        (paths.image_ledger_output, "image_ledger.csv"),
    ]:
        _copy_existing_file(resolve_under_root(paths.root, source_value), outputs_dir / name)
    if paths.summary_dir:
        summary_source = resolve_under_root(paths.root, paths.summary_dir)
        if summary_source.is_dir():
            shutil.copytree(summary_source, outputs_dir / "summary")
    _write_manifest(temp, paths=paths, kind="snapshot")
    _atomic_replace_dir(temp, target)


def export_final_artifacts(sync: ArtifactSyncConfig, paths: ArtifactPaths) -> None:
    if not sync.enabled:
        return

    assert sync.external_root is not None
    temp = sync.external_root / "runs" / f".{paths.run_id}.final.stage"
    if temp.exists():
        shutil.rmtree(temp)
    temp.mkdir(parents=True, exist_ok=True)
    for source_value, name in [
        (paths.output, "article_result.json"),
        (paths.article_output, "article.md"),
        (paths.source_ledger_output, "source_ledger.csv"),
        (paths.image_ledger_output, "image_ledger.csv"),
    ]:
        source = resolve_under_root(paths.root, source_value)
        if not source.is_file():
            raise FileNotFoundError(f"missing configured output: {source_value}")
        shutil.copy2(source, temp / name)
    if paths.summary_dir:
        summary_source = resolve_under_root(paths.root, paths.summary_dir)
        if summary_source.is_dir():
            shutil.copytree(summary_source, temp / "summary")
    _write_manifest(temp, paths=paths, kind="final")

    run_target = sync.external_root / "runs" / paths.run_id
    latest_stage = sync.external_root / ".latest.stage"
    if latest_stage.exists():
        shutil.rmtree(latest_stage)
    shutil.copytree(temp, latest_stage)
    _atomic_replace_dir(temp, run_target)
    _atomic_replace_dir(latest_stage, sync.external_root / "latest")


def cleanup_success_cache(sync: ArtifactSyncConfig, paths: ArtifactPaths) -> None:
    if not sync.enabled or not sync.cleanup_cache_on_success:
        return

    for rel in [f"work/runs/{paths.run_id}", f"outputs/{paths.run_id}"]:
        target = resolve_under_root(paths.root, rel)
        if target.is_dir():
            shutil.rmtree(target)
        elif target.exists():
            target.unlink()
