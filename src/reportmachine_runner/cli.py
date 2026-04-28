from __future__ import annotations

import argparse
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Sequence

from .artifacts import (
    ArtifactPaths,
    ArtifactSyncConfig,
    cleanup_success_cache,
    export_final_artifacts,
    import_existing_artifacts,
    snapshot_external_artifacts,
)
from .codex_exec import (
    CodexExecConfig,
    build_command,
    ensure_output_directories,
    make_file_snapshot,
    mirror_latest_outputs,
    run_command,
    validate_file_snapshot,
)
from .config import RunnerProfile, load_profile_config, resolve_config_path
from .paths import as_posix_arg, find_project_root
from .paths import resolve_external_root, resolve_under_root
from .prompt import build_prompt


RUN_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")
WEB_SEARCH_DEFAULTS = {
    "industry": "cached",
    "earnings": "live",
}


def default_run_id(prefix: str | None = None) -> str:
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"{prefix}_{timestamp}" if prefix else timestamp


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run the ReportMachine Codex article workflow.")
    parser.add_argument("--mode", choices=["industry", "earnings"], help="Article workflow mode. Required unless the selected config profile provides it.")
    parser.add_argument("--config", help="TOML runner preset file. Relative paths are resolved from the project root.")
    parser.add_argument("--profile", default="default", help="Profile name under [profiles.<name>] in the TOML preset.")
    parser.add_argument("--run-id", help="Run id used for work/runs/{run_id} and outputs/{run_id}. Defaults to current UTC timestamp.")
    parser.add_argument("--project-root", help="ReportMachine project root. Defaults to REPORTMACHINE_ROOT or marker search from cwd.")
    parser.add_argument("--topic")
    parser.add_argument("--sources")
    parser.add_argument("--schema")
    parser.add_argument("--output", help="Final assistant JSON path. Defaults to outputs/{run_id}/article_result.json.")
    parser.add_argument("--article-output", help="Final article path. Defaults to outputs/{run_id}/article.md.")
    parser.add_argument("--source-ledger-output", help="Source ledger path. Defaults to outputs/{run_id}/source_ledger.csv.")
    parser.add_argument("--image-ledger-output", help="Image ledger path. Defaults to outputs/{run_id}/image_ledger.csv.")
    parser.add_argument("--codex-bin", default="codex")
    parser.add_argument(
        "--web-search",
        choices=["cached", "live", "disabled"],
        help="Codex web search mode. Defaults to cached for industry and live for earnings.",
    )
    parser.add_argument(
        "--allow-shell-network",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="Allow shell outbound network in the Codex workspace-write sandbox for this run.",
    )
    parser.add_argument("--dry-run", action="store_true", help="Print the command and prompt without running Codex.")
    parser.add_argument("--smoke-test", action="store_true", help="Build the command and prompt without creating directories or running Codex.")
    return parser


def resolve_repo_arg(parser: argparse.ArgumentParser, root: Path, value: str, label: str) -> str:
    try:
        resolved = resolve_under_root(root, value)
        return resolved.relative_to(root.resolve()).as_posix()
    except ValueError as exc:
        parser.error(f"{label}: {exc}")


def load_profile(parser: argparse.ArgumentParser, root: Path, config_value: str | None, profile_name: str) -> RunnerProfile:
    if not config_value:
        return RunnerProfile()
    try:
        return load_profile_config(resolve_config_path(root, config_value), profile_name)
    except ValueError as exc:
        parser.error(str(exc))


def choose_value(cli_value: str | None, profile_value: str | None, default: str) -> str:
    return cli_value or profile_value or default


def choose_bool(cli_value: bool | None, profile_value: bool | None, default: bool = False) -> bool:
    if cli_value is not None:
        return cli_value
    if profile_value is not None:
        return profile_value
    return default


def build_artifact_sync(
    parser: argparse.ArgumentParser,
    root: Path,
    profile: RunnerProfile,
) -> ArtifactSyncConfig:
    artifacts = profile.artifacts
    if not artifacts.external_root:
        if (
            artifacts.import_existing
            or artifacts.snapshot_interval_seconds
            or artifacts.cleanup_cache_on_success
        ):
            parser.error("Artifact sync options require profiles.<name>.artifacts.external_root")
        return ArtifactSyncConfig()
    try:
        external_root = resolve_external_root(root, artifacts.external_root)
    except ValueError as exc:
        parser.error(str(exc))
    return ArtifactSyncConfig(
        external_root=external_root,
        import_existing=artifacts.import_existing,
        snapshot_interval_seconds=artifacts.snapshot_interval_seconds,
        cleanup_cache_on_success=artifacts.cleanup_cache_on_success,
        preserve_cache_on_failure=artifacts.preserve_cache_on_failure,
    )


def try_snapshot_external_artifacts(
    artifact_sync: ArtifactSyncConfig,
    artifact_paths: ArtifactPaths,
) -> None:
    try:
        snapshot_external_artifacts(artifact_sync, artifact_paths)
    except Exception as exc:
        print(f"Artifact snapshot failed: {exc}", file=sys.stderr)


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    root = Path(args.project_root).expanduser().resolve() if args.project_root else find_project_root()
    profile = load_profile(parser, root, args.config, args.profile)

    mode = args.mode or profile.mode
    if not mode:
        parser.error("--mode is required unless the selected config profile provides mode")
    if mode not in WEB_SEARCH_DEFAULTS:
        parser.error("--mode must be one of: industry, earnings")

    run_id_prefix = profile.run_id_prefix
    if run_id_prefix and not RUN_ID_PATTERN.fullmatch(run_id_prefix):
        parser.error("run_id_prefix must start with an ASCII letter or digit and may also contain underscore, hyphen, and dot")
    run_id = args.run_id or default_run_id(run_id_prefix)
    if not RUN_ID_PATTERN.fullmatch(run_id):
        parser.error("--run-id must start with an ASCII letter or digit and may also contain underscore, hyphen, and dot")

    work_run_dir = f"work/runs/{run_id}"
    draft_output = f"{work_run_dir}/drafts/draft_article.md"
    image_notes_dir = f"{work_run_dir}/images"
    image_ledger_candidate = f"{work_run_dir}/ledgers/image_ledger.candidate.json"

    output = args.output or f"outputs/{run_id}/article_result.json"
    article_output = args.article_output or f"outputs/{run_id}/article.md"
    source_ledger_output = args.source_ledger_output or f"outputs/{run_id}/source_ledger.csv"
    image_ledger_output = args.image_ledger_output or f"outputs/{run_id}/image_ledger.csv"
    web_search = args.web_search or profile.web_search or WEB_SEARCH_DEFAULTS[mode]
    if web_search not in {"cached", "live", "disabled"}:
        parser.error("web_search must be one of: cached, live, disabled")
    allow_shell_network = choose_bool(args.allow_shell_network, profile.allow_shell_network)
    artifact_sync = build_artifact_sync(parser, root, profile)

    topic_path = resolve_repo_arg(parser, root, choose_value(args.topic, profile.topic, "inputs/topic.md"), "--topic")
    sources_path = resolve_repo_arg(parser, root, choose_value(args.sources, profile.sources, "inputs/sources.csv"), "--sources")
    schema = resolve_repo_arg(parser, root, choose_value(args.schema, profile.schema, "schemas/article_result.schema.json"), "--schema")
    output = resolve_repo_arg(parser, root, output, "--output")
    article_output = resolve_repo_arg(parser, root, article_output, "--article-output")
    source_ledger_output = resolve_repo_arg(parser, root, source_ledger_output, "--source-ledger-output")
    image_ledger_output = resolve_repo_arg(parser, root, image_ledger_output, "--image-ledger-output")
    work_run_dir = resolve_repo_arg(parser, root, work_run_dir, "work run directory")
    draft_output = resolve_repo_arg(parser, root, draft_output, "draft output")
    image_notes_dir = resolve_repo_arg(parser, root, image_notes_dir, "image notes directory")
    image_ledger_candidate = resolve_repo_arg(parser, root, image_ledger_candidate, "image ledger candidate")

    prompt = build_prompt(
        mode=mode,
        run_id=run_id,
        topic_path=topic_path,
        sources_path=sources_path,
        work_run_dir=as_posix_arg(work_run_dir),
        draft_output=as_posix_arg(draft_output),
        image_notes_dir=as_posix_arg(image_notes_dir),
        image_ledger_candidate=as_posix_arg(image_ledger_candidate),
        article_output=as_posix_arg(article_output),
        source_ledger_output=as_posix_arg(source_ledger_output),
        image_ledger_output=as_posix_arg(image_ledger_output),
        result_output=as_posix_arg(output),
    )

    config = CodexExecConfig(
        root=root,
        codex_bin=args.codex_bin,
        run_id=run_id,
        schema=schema,
        output=as_posix_arg(output),
        article_output=as_posix_arg(article_output),
        source_ledger_output=as_posix_arg(source_ledger_output),
        image_ledger_output=as_posix_arg(image_ledger_output),
        work_run_dir=as_posix_arg(work_run_dir),
        draft_output=as_posix_arg(draft_output),
        image_notes_dir=as_posix_arg(image_notes_dir),
        image_ledger_candidate=as_posix_arg(image_ledger_candidate),
        latest_dir="outputs/latest",
        web_search=web_search,
        allow_shell_network=allow_shell_network,
        artifact_sync=artifact_sync,
        prompt=prompt,
    )
    command = build_command(config)
    artifact_paths = ArtifactPaths(
        root=root,
        run_id=run_id,
        work_run_dir=as_posix_arg(work_run_dir),
        output=as_posix_arg(output),
        article_output=as_posix_arg(article_output),
        source_ledger_output=as_posix_arg(source_ledger_output),
        image_ledger_output=as_posix_arg(image_ledger_output),
    )

    if args.dry_run or args.smoke_test:
        print("cwd:", root)
        print("run_id:", run_id)
        print("command:", command)
        print("prompt:")
        print(prompt)
        if args.smoke_test:
            print("smoke-test: command and prompt built without execution")
        return 0

    ensure_output_directories(config)
    import_existing_artifacts(artifact_sync, artifact_paths)
    snapshot = make_file_snapshot(config)
    snapshot_callback = None
    if artifact_sync.enabled and artifact_sync.snapshot_interval_seconds > 0:
        snapshot_callback = lambda: try_snapshot_external_artifacts(artifact_sync, artifact_paths)
    if snapshot_callback is None:
        status = run_command(command, cwd=root)
    else:
        status = run_command(
            command,
            cwd=root,
            snapshot_callback=snapshot_callback,
            snapshot_interval_seconds=artifact_sync.snapshot_interval_seconds,
        )
    if status != 0:
        if artifact_sync.enabled and artifact_sync.preserve_cache_on_failure:
            try_snapshot_external_artifacts(artifact_sync, artifact_paths)
        return status

    validation_errors = validate_file_snapshot(config, snapshot)
    if validation_errors:
        print("Post-run validation failed:", file=sys.stderr)
        for error in validation_errors:
            print(f" - {error}", file=sys.stderr)
        if artifact_sync.enabled and artifact_sync.preserve_cache_on_failure:
            try_snapshot_external_artifacts(artifact_sync, artifact_paths)
        return 2

    try:
        export_final_artifacts(artifact_sync, artifact_paths)
    except OSError as exc:
        print(f"Final artifact export failed: {exc}", file=sys.stderr)
        return 2
    mirror_latest_outputs(config)
    cleanup_success_cache(artifact_sync, artifact_paths)
    return 0
