from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path
from typing import Sequence

from .codex_exec import make_path_snapshot, run_command, validate_path_snapshot
from .config import RunnerProfile, SummaryProfile, load_profile_config, resolve_config_path
from .paths import as_posix_arg, find_project_root, resolve_under_root, validate_run_id
from .summary_contracts import (
    SUMMARY_SCHEMA,
    SummaryPaths,
    SummaryRunConfig,
    SummarySourcePaths,
    _read_json,
    _repo_path,
    build_summary_paths,
    build_summary_source_paths,
    collect_input_errors,
    ensure_summary_directory,
    source_hashes,
    validate_source_hashes,
    write_claim_map_header,
    write_failure_summary_result,
    write_summary_manifest,
    write_summary_prompt_file,
)
from .summary_prompt import build_summary_command, build_summary_prompt
from .summary_validation import validate_summary_result


def mirror_latest_summary(root: Path, paths: SummaryPaths) -> None:
    source_dir = resolve_under_root(root, paths.summary_dir)
    if not source_dir.is_dir():
        return
    latest_summary = resolve_under_root(root, "outputs/latest/summary")
    temp_dir = latest_summary.with_name(f".{latest_summary.name}.tmp")
    if temp_dir.exists():
        shutil.rmtree(temp_dir)
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
