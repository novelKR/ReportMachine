from __future__ import annotations

import argparse
import csv
import json
import shutil
import sys
from dataclasses import replace
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
    validate_path_snapshot,
)
from .config import RunnerProfile, load_profile_config, resolve_config_path
from .audit import run_finalize_audits
from .intake import ArticleBrief, intake_sources_path, intake_topic_path, write_intake_files
from .manifest import build_run_manifest, write_run_manifest
from .paths import as_posix_arg, find_project_root, validate_run_id
from .paths import resolve_external_root, resolve_under_root
from .preflight import build_preflight_result, write_preflight_result
from .prompt import build_prompt
from .schema_validation import validate_json_artifact
from .source_ingest import (
    DEFAULT_MAX_DOWNLOAD_BYTES,
    DEFAULT_MAX_IMAGE_INPUTS,
    DEFAULT_MAX_PDF_PAGES,
    DEFAULT_PDF_RENDER_DPI,
    SourceIngestConfig,
    build_source_ingest_paths,
    run_source_ingest,
)
from .summary import (
    SUMMARY_SCHEMA,
    SummarySourcePaths,
    build_summary_command,
    build_summary_prompt,
    build_summary_run_config,
    mirror_latest_summary,
    run_summary_workflow,
)


WEB_SEARCH_DEFAULTS = {
    "industry": "cached",
    "earnings": "live",
    "preview": "cached",
}

IMAGE_LEDGER_HEADER = [
    "image_path",
    "source_doc",
    "source_id",
    "page_or_slide_number",
    "source_hash",
    "image_hash",
    "inspected_status",
    "caption_basis",
    "actual_slide_title",
    "detected_key_datapoints",
    "caption",
    "caption_candidate",
    "article_anchor_before",
    "article_anchor_after",
    "keep_or_drop",
    "drop_reason",
]


def default_run_id(prefix: str | None = None) -> str:
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"{prefix}_{timestamp}" if prefix else timestamp


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run the ReportMachine Codex article workflow.")
    parser.add_argument("--mode", choices=["industry", "earnings", "preview"], help="Article workflow mode. Required unless the selected config profile provides it.")
    parser.add_argument("--phase", choices=["prepare", "draft", "finalize", "all"], default="all", help="Workflow phase to run. Defaults to all.")
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
    parser.add_argument(
        "--source-ingest",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="Preprocess sources into run-local ledgers, PDF text, and rendered images before Codex runs.",
    )
    parser.add_argument(
        "--source-domain",
        action="append",
        default=None,
        help="Additional official/allowed HTTPS domain for automatic source downloads. May be repeated.",
    )
    parser.add_argument(
        "--max-download-bytes",
        type=int,
        help="Maximum bytes per downloaded source. Defaults to 50 MiB.",
    )
    parser.add_argument(
        "--pdf-render-dpi",
        type=int,
        help="DPI for rendered PDF page images. Defaults to 144.",
    )
    parser.add_argument(
        "--max-pdf-pages",
        type=int,
        help="Maximum pages per PDF to extract/render. Defaults to 40.",
    )
    parser.add_argument(
        "--max-image-inputs",
        type=int,
        help="Maximum rendered images passed to Codex CLI --image. Defaults to 20.",
    )
    parser.add_argument(
        "--intake-topic",
        help="Approved conversational intake topic. Writes run-local intake inputs before execution.",
    )
    parser.add_argument(
        "--intake-core-question",
        default="",
        help="Approved central question for generated run-local intake topic.md.",
    )
    parser.add_argument(
        "--intake-recent-trigger",
        default="",
        help="Approved recent trigger note for generated run-local intake topic.md.",
    )
    parser.add_argument(
        "--intake-source-note",
        action="append",
        default=None,
        help="Approved source requirement note for generated run-local sources.csv. May be repeated.",
    )
    parser.add_argument(
        "--intake-output-notes",
        default="",
        help="Approved output notes for generated run-local intake topic.md.",
    )
    parser.add_argument(
        "--intake-mirror-inputs",
        action="store_true",
        help="Also mirror generated intake files to inputs/topic.md and inputs/sources.csv.",
    )
    parser.add_argument(
        "--prepare-only",
        action="store_true",
        help="Prepare directories, source ingest artifacts, and a prompt manifest without running Codex.",
    )
    parser.add_argument(
        "--json-events",
        action="store_true",
        help="Run codex exec with --json and save newline-delimited events under logs/{run_id}/.",
    )
    parser.add_argument(
        "--summary-output",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="After a successful article run, generate derivative A4 summary output under outputs/{run_id}/summary.",
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


def choose_int(cli_value: int | None, profile_value: int | None, default: int, label: str, parser: argparse.ArgumentParser) -> int:
    value = cli_value if cli_value is not None else profile_value
    if value is None:
        value = default
    if value < 0:
        parser.error(f"{label} must be a non-negative integer")
    return value


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


def _has_intake_args(args: argparse.Namespace) -> bool:
    return any(
        [
            args.intake_topic,
            args.intake_core_question,
            args.intake_recent_trigger,
            args.intake_source_note,
            args.intake_output_notes,
            args.intake_mirror_inputs,
        ]
    )


def build_intake_brief(parser: argparse.ArgumentParser, args: argparse.Namespace, mode: str) -> ArticleBrief | None:
    if not _has_intake_args(args):
        return None
    if args.topic or args.sources:
        parser.error("--intake-topic cannot be combined with --topic or --sources")
    if not args.intake_topic or not args.intake_topic.strip():
        parser.error("--intake-topic is required when using conversational intake options")
    return ArticleBrief(
        mode=mode,
        topic=args.intake_topic,
        core_question=args.intake_core_question,
        recent_trigger=args.intake_recent_trigger,
        required_source_notes=tuple(args.intake_source_note or ()),
        output_notes=args.intake_output_notes,
    )


def write_prepare_manifest(
    *,
    root: Path,
    mode: str,
    config: CodexExecConfig,
    command: Sequence[str],
    topic_path: str,
    sources_path: str,
    prepare_manifest: str,
    prompt: str,
) -> None:
    manifest = {
        "version": 1,
        "mode": mode,
        "run_id": config.run_id,
        "generated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "execution": {
            "prepared_for": "app_native_fallback_or_manual_codex_cli",
            "codex_command": list(command),
            "codex_cli_required_for_command": True,
        },
        "paths": {
            "topic": topic_path,
            "sources": sources_path,
            "work_run_dir": config.work_run_dir,
            "source_ingest_manifest": config.source_ingest_manifest,
            "source_candidate_ledger": config.source_candidate_ledger,
            "pdf_index": config.pdf_index,
            "rendered_images_ledger": config.rendered_images_ledger,
            "article_output": config.article_output,
            "source_ledger_output": config.source_ledger_output,
            "image_ledger_output": config.image_ledger_output,
            "result_output": config.output,
        },
        "prompt": prompt,
    }
    target = resolve_under_root(root, prepare_manifest)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def output_files_payload(
    *,
    article_output: str,
    source_ledger_output: str,
    image_ledger_output: str,
    claim_ledger_output: str,
    preflight_result_output: str,
    run_manifest_output: str,
    earnings_delta_matrix_output: str | None = None,
    earnings_delta_matrix_md: str | None = None,
) -> dict[str, str | None]:
    return {
        "article_md": article_output,
        "source_ledger_csv": source_ledger_output,
        "image_ledger_csv": image_ledger_output,
        "claim_ledger_json": claim_ledger_output,
        "preflight_result_json": preflight_result_output,
        "run_manifest_json": run_manifest_output,
        "earnings_delta_matrix_json": earnings_delta_matrix_output,
        "earnings_delta_matrix_md": earnings_delta_matrix_md,
    }


def write_json_artifact(root: Path, path: str, payload: object) -> None:
    target = resolve_under_root(root, path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def write_empty_image_ledger(root: Path, path: str) -> None:
    target = resolve_under_root(root, path)
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=IMAGE_LEDGER_HEADER)
        writer.writeheader()


def copy_or_write_source_ledger(
    *,
    root: Path,
    source_candidate_ledger: str,
    source_ledger_output: str,
) -> None:
    target = resolve_under_root(root, source_ledger_output)
    target.parent.mkdir(parents=True, exist_ok=True)
    source = resolve_under_root(root, source_candidate_ledger)
    if source.is_file():
        shutil.copy2(source, target)
        return
    with target.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=["source_id", "url", "local_path", "sha256", "ingest_status", "blocked_reason"],
        )
        writer.writeheader()


def write_empty_earnings_delta(root: Path, json_path: str, md_path: str, reason: str) -> None:
    write_json_artifact(
        root,
        json_path,
        {
            "delta_table": [],
            "disappeared_highlights": [],
            "wording_shifts": [],
            "guidance_assumption_changes": [],
            "ir_frame": "",
            "counter_reading": "",
            "scorecard_candidate": {
                "total": 0,
                "headline": 0,
                "quality": 0,
                "leading_indicators": 0,
                "transparency": 0,
                "execution": 0,
                "summary": reason,
            },
            "next_quarter_verification_points": [],
        },
    )
    target = resolve_under_root(root, md_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(f"# Earnings delta matrix unavailable\n\n{reason}\n", encoding="utf-8")


def write_preflight_failure_outputs(
    *,
    root: Path,
    mode: str,
    preflight_payload: dict,
    output: str,
    article_output: str,
    source_ledger_output: str,
    image_ledger_output: str,
    claim_ledger_output: str,
    preflight_result_output: str,
    run_manifest_output: str,
    earnings_delta_matrix_output: str,
    earnings_delta_matrix_md: str,
    source_candidate_ledger: str,
) -> None:
    warnings = list(preflight_payload.get("blocking_errors") or [])
    article = [
        "# Preflight failure",
        "",
        "This is a non-publishable failure note. ReportMachine stopped before drafting because required materials were missing or unusable.",
        "",
        *[f"- {item}" for item in warnings],
        "",
    ]
    resolve_under_root(root, article_output).write_text("\n".join(article), encoding="utf-8")
    copy_or_write_source_ledger(
        root=root,
        source_candidate_ledger=source_candidate_ledger,
        source_ledger_output=source_ledger_output,
    )
    write_empty_image_ledger(root, image_ledger_output)
    write_json_artifact(root, claim_ledger_output, [])
    if mode == "earnings":
        write_empty_earnings_delta(
            root,
            earnings_delta_matrix_output,
            earnings_delta_matrix_md,
            "Preflight failed before earnings delta analysis.",
        )
    write_json_artifact(
        root,
        output,
        {
            "article_type": mode,
            "title": "Preflight failed",
            "subtitle": "Required source materials were missing or unusable.",
            "one_sentence_insight": "",
            "scorecard": None,
            "output_files": output_files_payload(
                article_output=article_output,
                source_ledger_output=source_ledger_output,
                image_ledger_output=image_ledger_output,
                claim_ledger_output=claim_ledger_output,
                preflight_result_output=preflight_result_output,
                run_manifest_output=run_manifest_output,
                earnings_delta_matrix_output=earnings_delta_matrix_output if mode == "earnings" else None,
                earnings_delta_matrix_md=earnings_delta_matrix_md if mode == "earnings" else None,
            ),
            "critical_warnings": warnings,
            "checklist_status": "fail",
        },
    )


def article_result_status(root: Path, output: str) -> str:
    path = resolve_under_root(root, output)
    if not path.is_file():
        return ""
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return ""
    return str(payload.get("checklist_status", ""))


def write_run_summary_log(
    *,
    root: Path,
    run_id: str,
    phase: str,
    status: str,
    preflight_status: str,
    article_result: str,
    events_log: str,
    stderr_log: str,
) -> None:
    write_json_artifact(
        root,
        f"logs/{run_id}/run_summary.json",
        {
            "run_id": run_id,
            "phase": phase,
            "status": status,
            "preflight_status": preflight_status,
            "article_result": article_result,
            "events_log": events_log,
            "stderr_log": stderr_log,
        },
    )


def validate_earnings_delta_outputs(config: CodexExecConfig, mode: str) -> list[str]:
    if mode != "earnings":
        return []
    if article_result_status(config.root, config.output) == "fail":
        return []
    errors: list[str] = []
    for value in [config.earnings_delta_matrix_output, config.earnings_delta_matrix_md]:
        if not resolve_under_root(config.root, value).is_file():
            errors.append(f"missing configured earnings delta output: {value}")
    if errors:
        return errors
    _, schema_errors = validate_json_artifact(
        root=config.root,
        artifact=config.earnings_delta_matrix_output,
        schema="schemas/earnings_delta.schema.json",
        label="earnings delta",
    )
    errors.extend(schema_errors)
    return errors


def run_codex_command(
    *,
    config: CodexExecConfig,
    command: Sequence[str],
    artifact_sync: ArtifactSyncConfig,
    artifact_paths: ArtifactPaths,
    append_logs: bool = False,
) -> int:
    snapshot_callback = None
    if artifact_sync.enabled and artifact_sync.snapshot_interval_seconds > 0:
        snapshot_callback = lambda: try_snapshot_external_artifacts(artifact_sync, artifact_paths)
    stdout_path = resolve_under_root(config.root, config.events_log) if config.json_events else None
    stderr_path = resolve_under_root(config.root, config.stderr_log) if config.json_events else None
    if snapshot_callback is None:
        if stdout_path is None and stderr_path is None:
            return run_command(command, cwd=config.root)
        return run_command(
            command,
            cwd=config.root,
            stdout_path=stdout_path,
            stderr_path=stderr_path,
            append_logs=append_logs,
        )
    if stdout_path is None and stderr_path is None:
        return run_command(
            command,
            cwd=config.root,
            snapshot_callback=snapshot_callback,
            snapshot_interval_seconds=artifact_sync.snapshot_interval_seconds,
        )
    return run_command(
        command,
        cwd=config.root,
        snapshot_callback=snapshot_callback,
        snapshot_interval_seconds=artifact_sync.snapshot_interval_seconds,
        stdout_path=stdout_path,
        stderr_path=stderr_path,
        append_logs=append_logs,
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    root = Path(args.project_root).expanduser().resolve() if args.project_root else find_project_root()
    profile = load_profile(parser, root, args.config, args.profile)

    mode = args.mode or profile.mode
    if not mode:
        parser.error("--mode is required unless the selected config profile provides mode")
    if mode not in WEB_SEARCH_DEFAULTS:
        parser.error("--mode must be one of: industry, earnings, preview")
    phase = "prepare" if args.prepare_only else args.phase
    intake_brief = build_intake_brief(parser, args, mode)

    run_id_prefix = profile.run_id_prefix
    if run_id_prefix:
        try:
            validate_run_id(run_id_prefix, "run_id_prefix")
        except ValueError as exc:
            parser.error(str(exc))
    run_id = args.run_id or default_run_id(run_id_prefix)
    try:
        validate_run_id(run_id, "--run-id")
    except ValueError as exc:
        parser.error(str(exc))

    work_run_dir = f"work/runs/{run_id}"
    draft_output = f"{work_run_dir}/drafts/draft_article.md"
    image_notes_dir = f"{work_run_dir}/images"
    image_ledger_candidate = f"{work_run_dir}/ledgers/image_ledger.candidate.json"

    output = args.output or f"outputs/{run_id}/article_result.json"
    article_output = args.article_output or f"outputs/{run_id}/article.md"
    source_ledger_output = args.source_ledger_output or f"outputs/{run_id}/source_ledger.csv"
    image_ledger_output = args.image_ledger_output or f"outputs/{run_id}/image_ledger.csv"
    claim_ledger_output = f"outputs/{run_id}/claim_ledger.json"
    preflight_result_output = f"outputs/{run_id}/preflight_result.json"
    run_manifest_output = f"outputs/{run_id}/run_manifest.json"
    earnings_delta_matrix_output = f"outputs/{run_id}/earnings_delta_matrix.json"
    earnings_delta_matrix_md = f"outputs/{run_id}/earnings_delta_matrix.md"
    events_log = f"logs/{run_id}/codex_events.jsonl"
    stderr_log = f"logs/{run_id}/stderr.log"
    web_search = args.web_search or profile.web_search or WEB_SEARCH_DEFAULTS[mode]
    if web_search not in {"cached", "live", "disabled"}:
        parser.error("web_search must be one of: cached, live, disabled")
    allow_shell_network = choose_bool(args.allow_shell_network, profile.allow_shell_network)
    source_ingest_enabled = choose_bool(args.source_ingest, profile.source_ingest.enabled, True)
    source_ingest_allowed_domains = tuple(profile.source_ingest.allowed_domains) + tuple(args.source_domain or ())
    max_download_bytes = choose_int(
        args.max_download_bytes,
        profile.source_ingest.max_download_bytes,
        DEFAULT_MAX_DOWNLOAD_BYTES,
        "max_download_bytes",
        parser,
    )
    pdf_render_dpi = choose_int(
        args.pdf_render_dpi,
        profile.source_ingest.pdf_render_dpi,
        DEFAULT_PDF_RENDER_DPI,
        "pdf_render_dpi",
        parser,
    )
    max_pdf_pages = choose_int(
        args.max_pdf_pages,
        profile.source_ingest.max_pdf_pages,
        DEFAULT_MAX_PDF_PAGES,
        "max_pdf_pages",
        parser,
    )
    max_image_inputs = choose_int(
        args.max_image_inputs,
        profile.source_ingest.max_image_inputs,
        DEFAULT_MAX_IMAGE_INPUTS,
        "max_image_inputs",
        parser,
    )
    artifact_sync = build_artifact_sync(parser, root, profile)
    summary_output_enabled = choose_bool(args.summary_output, profile.summary.enabled)

    topic_default = intake_topic_path(run_id) if intake_brief else choose_value(args.topic, profile.topic, "inputs/topic.md")
    sources_default = intake_sources_path(run_id) if intake_brief else choose_value(args.sources, profile.sources, "inputs/sources.csv")
    topic_path = resolve_repo_arg(parser, root, topic_default, "--topic")
    sources_path = resolve_repo_arg(parser, root, sources_default, "--sources")
    schema = resolve_repo_arg(parser, root, choose_value(args.schema, profile.schema, "schemas/article_result.schema.json"), "--schema")
    output = resolve_repo_arg(parser, root, output, "--output")
    article_output = resolve_repo_arg(parser, root, article_output, "--article-output")
    source_ledger_output = resolve_repo_arg(parser, root, source_ledger_output, "--source-ledger-output")
    image_ledger_output = resolve_repo_arg(parser, root, image_ledger_output, "--image-ledger-output")
    claim_ledger_output = resolve_repo_arg(parser, root, claim_ledger_output, "claim ledger output")
    preflight_result_output = resolve_repo_arg(parser, root, preflight_result_output, "preflight result output")
    run_manifest_output = resolve_repo_arg(parser, root, run_manifest_output, "run manifest output")
    earnings_delta_matrix_output = resolve_repo_arg(parser, root, earnings_delta_matrix_output, "earnings delta matrix output")
    earnings_delta_matrix_md = resolve_repo_arg(parser, root, earnings_delta_matrix_md, "earnings delta matrix md")
    events_log = resolve_repo_arg(parser, root, events_log, "codex events log")
    stderr_log = resolve_repo_arg(parser, root, stderr_log, "codex stderr log")
    work_run_dir = resolve_repo_arg(parser, root, work_run_dir, "work run directory")
    draft_output = resolve_repo_arg(parser, root, draft_output, "draft output")
    image_notes_dir = resolve_repo_arg(parser, root, image_notes_dir, "image notes directory")
    image_ledger_candidate = resolve_repo_arg(parser, root, image_ledger_candidate, "image ledger candidate")
    prepare_manifest = resolve_repo_arg(parser, root, f"{work_run_dir}/prepare_manifest.json", "prepare manifest")
    source_ingest_paths = build_source_ingest_paths(work_run_dir)

    prompt_kwargs = {
        "mode": mode,
        "phase": "finalize",
        "run_id": run_id,
        "topic_path": topic_path,
        "sources_path": sources_path,
        "work_run_dir": as_posix_arg(work_run_dir),
        "draft_output": as_posix_arg(draft_output),
        "image_notes_dir": as_posix_arg(image_notes_dir),
        "image_ledger_candidate": as_posix_arg(image_ledger_candidate),
        "source_ingest_manifest": as_posix_arg(source_ingest_paths.manifest),
        "source_candidate_ledger": as_posix_arg(source_ingest_paths.source_candidate_ledger),
        "pdf_index": as_posix_arg(source_ingest_paths.pdf_index),
        "rendered_images_ledger": as_posix_arg(source_ingest_paths.rendered_images_ledger),
        "source_ingest_enabled": source_ingest_enabled,
        "article_output": as_posix_arg(article_output),
        "source_ledger_output": as_posix_arg(source_ledger_output),
        "image_ledger_output": as_posix_arg(image_ledger_output),
        "claim_ledger_output": as_posix_arg(claim_ledger_output),
        "preflight_result_output": as_posix_arg(preflight_result_output),
        "run_manifest_output": as_posix_arg(run_manifest_output),
        "earnings_delta_matrix_output": as_posix_arg(earnings_delta_matrix_output),
        "earnings_delta_matrix_md": as_posix_arg(earnings_delta_matrix_md),
        "result_output": as_posix_arg(output),
    }
    prompt = build_prompt(**prompt_kwargs)
    draft_prompt = build_prompt(**{**prompt_kwargs, "phase": "draft"})
    app_native_prompt = build_prompt(**prompt_kwargs, app_native_fallback=True)

    config = CodexExecConfig(
        root=root,
        codex_bin=args.codex_bin,
        run_id=run_id,
        phase="finalize",
        schema=schema,
        output=as_posix_arg(output),
        article_output=as_posix_arg(article_output),
        source_ledger_output=as_posix_arg(source_ledger_output),
        image_ledger_output=as_posix_arg(image_ledger_output),
        claim_ledger_output=as_posix_arg(claim_ledger_output),
        preflight_result_output=as_posix_arg(preflight_result_output),
        run_manifest_output=as_posix_arg(run_manifest_output),
        earnings_delta_matrix_output=as_posix_arg(earnings_delta_matrix_output),
        earnings_delta_matrix_md=as_posix_arg(earnings_delta_matrix_md),
        work_run_dir=as_posix_arg(work_run_dir),
        draft_output=as_posix_arg(draft_output),
        image_notes_dir=as_posix_arg(image_notes_dir),
        image_ledger_candidate=as_posix_arg(image_ledger_candidate),
        source_ingest_manifest=as_posix_arg(source_ingest_paths.manifest),
        source_candidate_ledger=as_posix_arg(source_ingest_paths.source_candidate_ledger),
        pdf_index=as_posix_arg(source_ingest_paths.pdf_index),
        rendered_images_ledger=as_posix_arg(source_ingest_paths.rendered_images_ledger),
        latest_dir="outputs/latest",
        web_search=web_search,
        allow_shell_network=allow_shell_network,
        image_inputs=(),
        structured_output=True,
        json_events=args.json_events,
        events_log=as_posix_arg(events_log),
        stderr_log=as_posix_arg(stderr_log),
        artifact_sync=artifact_sync,
        prompt=prompt,
    )
    draft_config = replace(
        config,
        phase="draft",
        structured_output=False,
        prompt=draft_prompt,
    )
    draft_command = build_command(draft_config)
    command = build_command(config)
    artifact_paths = ArtifactPaths(
        root=root,
        run_id=run_id,
        work_run_dir=as_posix_arg(work_run_dir),
        output=as_posix_arg(output),
        article_output=as_posix_arg(article_output),
        source_ledger_output=as_posix_arg(source_ledger_output),
        image_ledger_output=as_posix_arg(image_ledger_output),
        claim_ledger_output=as_posix_arg(claim_ledger_output),
        preflight_result_output=as_posix_arg(preflight_result_output),
        run_manifest_output=as_posix_arg(run_manifest_output),
        earnings_delta_matrix_output=as_posix_arg(earnings_delta_matrix_output),
        earnings_delta_matrix_md=as_posix_arg(earnings_delta_matrix_md),
        summary_dir=f"outputs/{run_id}/summary" if summary_output_enabled else None,
    )
    summary_config = build_summary_run_config(
        root=root,
        run_id=run_id,
        codex_bin=args.codex_bin,
        schema=SUMMARY_SCHEMA,
        web_search="disabled",
        profile=profile.summary,
        sources=SummarySourcePaths(
            article_result=as_posix_arg(output),
            article=as_posix_arg(article_output),
            source_ledger=as_posix_arg(source_ledger_output),
            image_ledger=as_posix_arg(image_ledger_output),
        ),
    )

    if args.dry_run or args.smoke_test:
        print("cwd:", root)
        print("run_id:", run_id)
        print("phase:", phase)
        print("command:", command if phase in {"finalize", "all"} else draft_command if phase == "draft" else [])
        if phase == "all":
            print("draft_command:", draft_command)
            print("finalize_command:", command)
        if intake_brief:
            print("intake_topic:", topic_path)
            print("intake_sources:", sources_path)
        print("prompt:")
        print(draft_prompt if phase == "draft" else prompt)
        if summary_output_enabled:
            summary_prompt = build_summary_prompt(
                summary_config,
                article_status="available_after_article_run",
            )
            print("summary_dir:", summary_config.paths.summary_dir)
            print("summary_result:", summary_config.paths.summary_result)
            print("summary_image:", summary_config.paths.summary_image)
            print("summary_command:", build_summary_command(summary_config, summary_prompt))
            print("summary_prompt:")
            print(summary_prompt)
        if args.smoke_test:
            print("smoke-test: command and prompt built without execution")
        return 0

    if intake_brief:
        write_intake_files(
            root=root,
            run_id=run_id,
            brief=intake_brief,
            mirror_inputs=args.intake_mirror_inputs,
        )
    ensure_output_directories(config)
    import_existing_artifacts(artifact_sync, artifact_paths)
    if source_ingest_enabled:
        ingest_result = run_source_ingest(
            SourceIngestConfig(
                root=root,
                mode=mode,
                run_id=run_id,
                sources_path=sources_path,
                work_run_dir=work_run_dir,
                allow_downloads=allow_shell_network,
                enabled=source_ingest_enabled,
                allowed_domains=source_ingest_allowed_domains,
                max_download_bytes=max_download_bytes,
                pdf_render_dpi=pdf_render_dpi,
                max_pdf_pages=max_pdf_pages,
                max_image_inputs=max_image_inputs,
            )
        )
        if ingest_result.image_inputs:
            config = replace(config, image_inputs=ingest_result.image_inputs)
            draft_config = replace(draft_config, image_inputs=ingest_result.image_inputs)
            command = build_command(config)
            draft_command = build_command(draft_config)

    preflight = build_preflight_result(
        root=root,
        mode=mode,
        run_id=run_id,
        sources_path=sources_path,
        source_candidate_ledger=source_ingest_paths.source_candidate_ledger,
    )
    write_preflight_result(root, as_posix_arg(preflight_result_output), preflight)
    run_manifest = build_run_manifest(
        root=root,
        mode=mode,
        run_id=run_id,
        phase=phase,
        codex_bin=args.codex_bin,
        schema=schema,
        web_search=web_search,
        allow_shell_network=allow_shell_network,
        source_ingest_enabled=source_ingest_enabled,
        source_candidate_ledger=source_ingest_paths.source_candidate_ledger,
        paths={
            "topic": topic_path,
            "sources": sources_path,
            "work_run_dir": as_posix_arg(work_run_dir),
            "article_result": as_posix_arg(output),
            "article": as_posix_arg(article_output),
            "source_ledger": as_posix_arg(source_ledger_output),
            "image_ledger": as_posix_arg(image_ledger_output),
            "claim_ledger": as_posix_arg(claim_ledger_output),
            "preflight_result": as_posix_arg(preflight_result_output),
            "run_manifest": as_posix_arg(run_manifest_output),
            "earnings_delta_matrix": as_posix_arg(earnings_delta_matrix_output),
            "earnings_delta_matrix_md": as_posix_arg(earnings_delta_matrix_md),
            "events_log": as_posix_arg(events_log) if args.json_events else None,
            "stderr_log": as_posix_arg(stderr_log) if args.json_events else None,
        },
    )
    write_run_manifest(root, as_posix_arg(run_manifest_output), run_manifest)
    write_prepare_manifest(
        root=root,
        mode=mode,
        config=config,
        command=command,
        topic_path=topic_path,
        sources_path=sources_path,
        prepare_manifest=prepare_manifest,
        prompt=app_native_prompt,
    )

    if not preflight.passed:
        write_preflight_failure_outputs(
            root=root,
            mode=mode,
            preflight_payload=preflight.payload,
            output=as_posix_arg(output),
            article_output=as_posix_arg(article_output),
            source_ledger_output=as_posix_arg(source_ledger_output),
            image_ledger_output=as_posix_arg(image_ledger_output),
            claim_ledger_output=as_posix_arg(claim_ledger_output),
            preflight_result_output=as_posix_arg(preflight_result_output),
            run_manifest_output=as_posix_arg(run_manifest_output),
            earnings_delta_matrix_output=as_posix_arg(earnings_delta_matrix_output),
            earnings_delta_matrix_md=as_posix_arg(earnings_delta_matrix_md),
            source_candidate_ledger=source_ingest_paths.source_candidate_ledger,
        )
        write_run_summary_log(
            root=root,
            run_id=run_id,
            phase=phase,
            status="preflight_fail",
            preflight_status="fail",
            article_result=as_posix_arg(output),
            events_log=as_posix_arg(events_log),
            stderr_log=as_posix_arg(stderr_log),
        )
        print("cwd:", root)
        print("run_id:", run_id)
        print("preflight_result:", preflight_result_output)
        print("preflight: fail; wrote schema-valid failure outputs without running Codex")
        return 0

    if phase == "prepare":
        write_run_summary_log(
            root=root,
            run_id=run_id,
            phase=phase,
            status="prepared",
            preflight_status="pass",
            article_result=as_posix_arg(output),
            events_log=as_posix_arg(events_log),
            stderr_log=as_posix_arg(stderr_log),
        )
        print("cwd:", root)
        print("run_id:", run_id)
        print("prepare_manifest:", prepare_manifest)
        print("preflight_result:", preflight_result_output)
        print("run_manifest:", run_manifest_output)
        if args.prepare_only:
            print("prepare-only: directories, source ingest artifacts, preflight, and manifests prepared without running Codex")
        else:
            print("prepare: directories, source ingest artifacts, preflight, and manifests prepared without running Codex")
        return 0

    if phase in {"draft", "all"}:
        draft_snapshot = make_file_snapshot(draft_config)
        status = run_codex_command(
            config=draft_config,
            command=draft_command,
            artifact_sync=artifact_sync,
            artifact_paths=artifact_paths,
            append_logs=False,
        )
        if status != 0:
            if artifact_sync.enabled and artifact_sync.preserve_cache_on_failure:
                try_snapshot_external_artifacts(artifact_sync, artifact_paths)
            write_run_summary_log(
                root=root,
                run_id=run_id,
                phase=phase,
                status="draft_codex_fail",
                preflight_status="pass",
                article_result=as_posix_arg(output),
                events_log=as_posix_arg(events_log),
                stderr_log=as_posix_arg(stderr_log),
            )
            return status
        draft_validation_errors = validate_path_snapshot(root, draft_snapshot)
        if phase == "draft" and not resolve_under_root(root, draft_config.draft_output).is_file():
            draft_validation_errors.append(f"missing configured draft output: {draft_config.draft_output}")
        if draft_validation_errors:
            print("Draft phase validation failed:", file=sys.stderr)
            for error in draft_validation_errors:
                print(f" - {error}", file=sys.stderr)
            if artifact_sync.enabled and artifact_sync.preserve_cache_on_failure:
                try_snapshot_external_artifacts(artifact_sync, artifact_paths)
            write_run_summary_log(
                root=root,
                run_id=run_id,
                phase=phase,
                status="draft_validation_fail",
                preflight_status="pass",
                article_result=as_posix_arg(output),
                events_log=as_posix_arg(events_log),
                stderr_log=as_posix_arg(stderr_log),
            )
            return 2
        if phase == "draft":
            write_run_summary_log(
                root=root,
                run_id=run_id,
                phase=phase,
                status="drafted",
                preflight_status="pass",
                article_result=as_posix_arg(output),
                events_log=as_posix_arg(events_log),
                stderr_log=as_posix_arg(stderr_log),
            )
            print("cwd:", root)
            print("run_id:", run_id)
            print("draft_output:", draft_config.draft_output)
            print("draft: completed without finalizing article outputs")
            return 0

    if phase == "finalize" and not resolve_under_root(root, draft_output).is_file():
        print(f"Finalize phase requires draft output: {draft_output}", file=sys.stderr)
        write_run_summary_log(
            root=root,
            run_id=run_id,
            phase=phase,
            status="missing_draft",
            preflight_status="pass",
            article_result=as_posix_arg(output),
            events_log=as_posix_arg(events_log),
            stderr_log=as_posix_arg(stderr_log),
        )
        return 2

    snapshot = make_file_snapshot(config)
    status = run_codex_command(
        config=config,
        command=command,
        artifact_sync=artifact_sync,
        artifact_paths=artifact_paths,
        append_logs=phase == "all",
    )
    if status != 0:
        if artifact_sync.enabled and artifact_sync.preserve_cache_on_failure:
            try_snapshot_external_artifacts(artifact_sync, artifact_paths)
        write_run_summary_log(
            root=root,
            run_id=run_id,
            phase=phase,
            status="codex_fail",
            preflight_status="pass",
            article_result=as_posix_arg(output),
            events_log=as_posix_arg(events_log),
            stderr_log=as_posix_arg(stderr_log),
        )
        return status

    validation_errors = validate_file_snapshot(config, snapshot)
    validation_errors.extend(validate_earnings_delta_outputs(config, mode))
    validation_errors.extend(
        run_finalize_audits(
            root=root,
            mode=mode,
            article_result=as_posix_arg(output),
            article_output=as_posix_arg(article_output),
            claim_ledger_output=as_posix_arg(claim_ledger_output),
            image_ledger_output=as_posix_arg(image_ledger_output),
            source_ledger_output=as_posix_arg(source_ledger_output),
            pdf_index=source_ingest_paths.pdf_index,
        )
    )
    if validation_errors:
        print("Post-run validation failed:", file=sys.stderr)
        for error in validation_errors:
            print(f" - {error}", file=sys.stderr)
        if artifact_sync.enabled and artifact_sync.preserve_cache_on_failure:
            try_snapshot_external_artifacts(artifact_sync, artifact_paths)
        write_run_summary_log(
            root=root,
            run_id=run_id,
            phase=phase,
            status="post_run_validation_fail",
            preflight_status="pass",
            article_result=as_posix_arg(output),
            events_log=as_posix_arg(events_log),
            stderr_log=as_posix_arg(stderr_log),
        )
        return 2

    if summary_output_enabled:
        summary_status = run_summary_workflow(summary_config, mirror_latest=False)
        if summary_status != 0:
            if artifact_sync.enabled and artifact_sync.preserve_cache_on_failure:
                try_snapshot_external_artifacts(artifact_sync, artifact_paths)
            return summary_status

    try:
        export_final_artifacts(artifact_sync, artifact_paths)
    except OSError as exc:
        print(f"Final artifact export failed: {exc}", file=sys.stderr)
        return 2
    if article_result_status(root, as_posix_arg(output)) != "fail":
        mirror_latest_outputs(config)
        if summary_output_enabled:
            mirror_latest_summary(root, summary_config.paths)
    cleanup_success_cache(artifact_sync, artifact_paths)
    write_run_summary_log(
        root=root,
        run_id=run_id,
        phase=phase,
        status="completed",
        preflight_status="pass",
        article_result=as_posix_arg(output),
        events_log=as_posix_arg(events_log),
        stderr_log=as_posix_arg(stderr_log),
    )
    return 0
