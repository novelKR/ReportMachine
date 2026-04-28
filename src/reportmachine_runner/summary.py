from __future__ import annotations

import argparse
import csv
import json
import sys
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
from typing import Sequence

from .codex_exec import make_path_snapshot, run_command, validate_path_snapshot
from .config import RunnerProfile, SummaryProfile, load_profile_config, resolve_config_path
from .paths import as_posix_arg, find_project_root, resolve_under_root, validate_run_id


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


def build_summary_prompt(
    config: SummaryRunConfig,
    *,
    article_status: str,
    hashes: dict[str, str] | None = None,
) -> str:
    hash_lines = []
    for key, value in sorted((hashes or {}).items()):
        hash_lines.append(f"- {key}: `{value}`")
    if not hash_lines:
        hash_lines.append("- Hashes are computed after article outputs exist.")

    return "\n".join(
        [
            "Use $summary-output.",
            f"Run id: `{config.run_id}`.",
            "Task: generate one A4 visual summary image as a derivative summary output.",
            f"Read the completed article from `{config.sources.article}`.",
            f"Read the structured article result from `{config.sources.article_result}`.",
            f"Read source ledger from `{config.sources.source_ledger}`.",
            f"Read image ledger from `{config.sources.image_ledger}`.",
            f"Article checklist_status: `{article_status}`.",
            "Input hashes:",
            *hash_lines,
            "",
            "Output contract:",
            f"- Write the A4 PNG to `{config.paths.summary_image}`.",
            f"- Write the claim map CSV to `{config.paths.summary_claim_map}`.",
            f"- Write prompt/audit notes only under `{config.paths.summary_dir}`.",
            f"- Return schema-valid JSON for `{config.schema}`; Codex CLI `-o` saves it to `{config.paths.summary_result}`.",
            "",
            "Image generation contract:",
            "- The image generation request must use Codex internal image generation tools only.",
            "- Do not call OpenAI API endpoints, File Search, vector stores, external image APIs, shell downloads, or browser automation as fallback.",
            "- If the Codex internal image generation tool is unavailable, cannot save the image, or cannot satisfy the contract, return `checklist_status` `fail`.",
            "- This A4 image is not an article output, not an evidence source, and not a replacement for `image_ledger.csv`.",
            "- AI-generated images remain prohibited in article outputs; this image may exist only under the summary output path.",
            "- Do not introduce new factual claims, quantitative values, source attributions, charts, or captions absent from `article.md` and ledgers.",
            "",
            "A4 style contract from TOML:",
            f"- image_generation_surface: `{config.profile.image_generation_surface}`",
            f"- paper_size: `{config.profile.paper_size}`",
            f"- orientation: `{config.profile.orientation}`",
            f"- language: `{config.profile.language}`",
            f"- style_name: `{config.profile.style_name}`",
            f"- max_visual_claims: `{config.profile.max_visual_claims}`",
            f"- require_source_footer: `{str(config.profile.require_source_footer).lower()}`",
            "style_prompt:",
            config.profile.style_prompt.strip(),
            "negative_prompt:",
            config.profile.negative_prompt.strip(),
            "",
            "Summary claim map requirements:",
            "- Preserve this CSV header exactly:",
            ",".join(SUMMARY_CLAIM_MAP_HEADER),
            "- Each visible statement, number, or source label in the image needs one row.",
            "- Use `article.md`, `source_ledger.csv`, or `image_ledger.csv` in `source_artifact`.",
            "",
            "If the article result is `fail`, do not generate an image; return a failure result.",
        ]
    )


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


def build_summary_command(config: SummaryRunConfig, prompt: str) -> list[str]:
    return [
        config.codex_bin,
        "exec",
        "-C",
        str(config.root),
        "--skip-git-repo-check",
        "--output-schema",
        config.schema,
        "-o",
        config.paths.summary_result,
        "-c",
        'project_root_markers=[".git",".article-root"]',
        "-c",
        f'web_search="{config.web_search}"',
        prompt,
    ]


def validate_summary_result(
    config: SummaryRunConfig,
    *,
    expected_hashes: dict[str, str] | None = None,
) -> list[str]:
    errors: list[str] = []
    result_path = _repo_path(config.root, config.paths.summary_result)
    if not result_path.is_file():
        return [f"missing summary result: {config.paths.summary_result}"]

    try:
        payload = _read_json(result_path)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        return [f"invalid summary result JSON: {exc}"]

    if payload.get("image_generation_surface") != SUMMARY_IMAGE_GENERATION_SURFACE:
        errors.append("summary result must use codex_internal_tool image_generation_surface")
    if payload.get("source_article") != config.sources.article:
        errors.append("summary result source_article does not match configured article path")
    if payload.get("source_result") != config.sources.article_result:
        errors.append("summary result source_result does not match configured article result path")
    if expected_hashes is not None:
        if payload.get("source_article_sha256") != expected_hashes.get("article_sha256"):
            errors.append("summary result source_article_sha256 does not match article hash")
        if payload.get("source_result_sha256") != expected_hashes.get("article_result_sha256"):
            errors.append("summary result source_result_sha256 does not match article result hash")

    output_files = payload.get("output_files")
    if not isinstance(output_files, dict):
        errors.append("summary result output_files must be an object")
    else:
        for key, expected in _output_files(config).items():
            actual = output_files.get(key)
            if actual != expected:
                errors.append(f"summary result output_files.{key} must be {expected}")

    for label, value in [
        ("summary_prompt", config.paths.summary_prompt),
        ("summary_claim_map", config.paths.summary_claim_map),
        ("summary_manifest", config.paths.summary_manifest),
    ]:
        if not _repo_path(config.root, value).is_file():
            errors.append(f"missing {label}: {value}")

    status = payload.get("checklist_status")
    if status in {"pass", "pass_with_warnings"}:
        if not _repo_path(config.root, config.paths.summary_image).is_file():
            errors.append(f"missing generated summary image: {config.paths.summary_image}")
        if _repo_path(config.root, config.paths.summary_claim_map).is_file():
            errors.extend(validate_summary_claim_map(config))
    return errors


def validate_summary_claim_map(config: SummaryRunConfig) -> list[str]:
    claim_map = _repo_path(config.root, config.paths.summary_claim_map)
    if not claim_map.is_file():
        return [f"missing summary_claim_map: {config.paths.summary_claim_map}"]

    errors: list[str] = []
    data_row_found = False
    try:
        with claim_map.open(newline="", encoding="utf-8") as handle:
            reader = csv.reader(handle)
            try:
                header = next(reader)
            except StopIteration:
                return [
                    "summary claim map header must match contract: "
                    f"{config.paths.summary_claim_map}"
                ]
            if header != SUMMARY_CLAIM_MAP_HEADER:
                return [
                    "summary claim map header must match contract: "
                    f"{config.paths.summary_claim_map}"
                ]
            for row_number, row in enumerate(reader, start=2):
                if not any(cell.strip() for cell in row):
                    continue
                data_row_found = True
                row_values = {
                    field: row[index].strip() if index < len(row) else ""
                    for index, field in enumerate(SUMMARY_CLAIM_MAP_HEADER)
                }
                if len(row) != len(SUMMARY_CLAIM_MAP_HEADER):
                    errors.append(
                        f"summary claim map row {row_number} must have "
                        f"{len(SUMMARY_CLAIM_MAP_HEADER)} columns: "
                        f"{config.paths.summary_claim_map}"
                    )
                missing = [
                    field
                    for field in SUMMARY_CLAIM_MAP_REQUIRED_FIELDS
                    if not row_values[field]
                ]
                if missing:
                    errors.append(
                        f"summary claim map row {row_number} missing required fields "
                        f"{', '.join(missing)}: {config.paths.summary_claim_map}"
                    )
                source_artifact = row_values["source_artifact"]
                if (
                    source_artifact
                    and source_artifact not in SUMMARY_CLAIM_MAP_SOURCE_ARTIFACTS
                ):
                    allowed = ", ".join(SUMMARY_CLAIM_MAP_SOURCE_ARTIFACTS)
                    errors.append(
                        f"summary claim map row {row_number} source_artifact must be "
                        f"one of {allowed}: {config.paths.summary_claim_map}"
                    )
    except OSError as exc:
        return [f"invalid summary claim map: {exc}"]

    if not data_row_found:
        return [
            "summary claim map must include at least one data row: "
            f"{config.paths.summary_claim_map}"
        ]
    return errors


def mirror_latest_summary(root: Path, paths: SummaryPaths) -> None:
    source_dir = resolve_under_root(root, paths.summary_dir)
    if not source_dir.is_dir():
        return
    latest_summary = resolve_under_root(root, "outputs/latest/summary")
    temp_dir = latest_summary.with_name(f".{latest_summary.name}.tmp")
    if temp_dir.exists():
        import shutil

        shutil.rmtree(temp_dir)
    import shutil

    shutil.copytree(source_dir, temp_dir)
    if latest_summary.exists():
        shutil.rmtree(latest_summary)
    temp_dir.replace(latest_summary)


def run_summary_workflow(
    config: SummaryRunConfig,
    *,
    dry_run: bool = False,
    smoke_test: bool = False,
    mirror_latest: bool = True,
) -> int:
    input_errors = collect_input_errors(config)
    if input_errors:
        for error in input_errors:
            print(error, file=sys.stderr)
        return 2

    article_result = _read_json(_repo_path(config.root, config.sources.article_result))
    article_status = str(article_result.get("checklist_status", ""))
    hashes = source_hashes(config)
    prompt = build_summary_prompt(config, article_status=article_status, hashes=hashes)
    command = build_summary_command(config, prompt)

    if dry_run or smoke_test:
        print("cwd:", config.root)
        print("run_id:", config.run_id)
        print("summary_dir:", config.paths.summary_dir)
        print("summary_result:", config.paths.summary_result)
        print("summary_image:", config.paths.summary_image)
        print("command:", command)
        print("summary_prompt:")
        print(prompt)
        if smoke_test:
            print("summary smoke-test: command and prompt built without execution")
        return 0

    ensure_summary_directory(config)
    write_claim_map_header(config)
    write_summary_manifest(config, article_status=article_status, hashes=hashes)
    write_summary_prompt_file(config, prompt)

    if article_status == "fail":
        write_failure_summary_result(
            config,
            warnings=["source article checklist_status is fail; summary image generation skipped"],
            hashes=hashes,
        )
        return 2

    snapshot = make_path_snapshot(
        config.root,
        allowed_prefixes=(f"{config.paths.summary_dir.rstrip('/')}/",),
    )
    status = run_command(command, cwd=config.root)
    guard_errors = validate_path_snapshot(config.root, snapshot)
    guard_errors.extend(validate_source_hashes(config, hashes))
    if guard_errors:
        write_failure_summary_result(config, warnings=guard_errors, hashes=hashes)
        print("Summary guard failed:", file=sys.stderr)
        for error in guard_errors:
            print(f" - {error}", file=sys.stderr)
        return 2
    if status != 0:
        return status

    validation_errors = validate_summary_result(config, expected_hashes=hashes)
    if validation_errors:
        write_failure_summary_result(config, warnings=validation_errors, hashes=hashes)
        print("Summary validation failed:", file=sys.stderr)
        for error in validation_errors:
            print(f" - {error}", file=sys.stderr)
        return 2

    payload = _read_json(_repo_path(config.root, config.paths.summary_result))
    if payload.get("checklist_status") == "fail":
        print("Summary generation returned checklist_status fail", file=sys.stderr)
        return 2

    if mirror_latest:
        mirror_latest_summary(config.root, config.paths)
    return 0


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run the ReportMachine summary output workflow.")
    parser.add_argument("--run-id", required=True, help="Run id under outputs/{run_id}.")
    parser.add_argument("--project-root", help="ReportMachine project root.")
    parser.add_argument("--config", help="TOML runner preset file.")
    parser.add_argument("--profile", default="default")
    parser.add_argument("--codex-bin", default="codex")
    parser.add_argument("--schema", default=SUMMARY_SCHEMA)
    parser.add_argument("--web-search", choices=["cached", "live", "disabled"], default="disabled")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--smoke-test", action="store_true")
    return parser


def _load_profile(root: Path, config_value: str | None, profile_name: str) -> RunnerProfile:
    if not config_value:
        return RunnerProfile()
    return load_profile_config(resolve_config_path(root, config_value), profile_name)


def build_summary_run_config(
    *,
    root: Path,
    run_id: str,
    codex_bin: str = "codex",
    schema: str = SUMMARY_SCHEMA,
    web_search: str = "disabled",
    profile: SummaryProfile | None = None,
    sources: SummarySourcePaths | None = None,
) -> SummaryRunConfig:
    run_id = validate_run_id(run_id, "--run-id")
    schema_rel = as_posix_arg(resolve_under_root(root, schema).relative_to(root.resolve()))
    return SummaryRunConfig(
        root=root,
        run_id=run_id,
        codex_bin=codex_bin,
        schema=schema_rel,
        web_search=web_search,
        paths=build_summary_paths(run_id),
        sources=sources or build_summary_source_paths(run_id),
        profile=profile or SummaryProfile(),
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    root = Path(args.project_root).expanduser().resolve() if args.project_root else find_project_root()
    try:
        loaded_profile = _load_profile(root, args.config, args.profile)
        config = build_summary_run_config(
            root=root,
            run_id=args.run_id,
            codex_bin=args.codex_bin,
            schema=args.schema,
            web_search=args.web_search,
            profile=loaded_profile.summary,
        )
        return run_summary_workflow(
            config,
            dry_run=args.dry_run,
            smoke_test=args.smoke_test,
        )
    except ValueError as exc:
        parser.error(str(exc))
    return 2
