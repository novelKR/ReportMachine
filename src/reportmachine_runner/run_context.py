from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from .artifacts import ArtifactSyncConfig
from .config import RunnerProfile, load_profile_config, resolve_config_path
from .intake import ArticleBrief, intake_sources_path, intake_topic_path
from .paths import find_project_root, resolve_external_root, resolve_under_root, validate_run_id
from .phase_contracts import RunArtifacts, build_run_artifacts
from .source_ingest import (
    DEFAULT_MAX_DOWNLOAD_BYTES,
    DEFAULT_MAX_IMAGE_INPUTS,
    DEFAULT_MAX_PDF_PAGES,
    DEFAULT_PDF_RENDER_DPI,
    SourceIngestPaths,
    build_source_ingest_paths,
)


WEB_SEARCH_DEFAULTS = {
    "industry": "cached",
    "earnings": "live",
    "preview": "cached",
}


@dataclass(frozen=True)
class RunContext:
    root: Path
    profile: RunnerProfile
    mode: str
    phase: str
    run_id: str
    intake_brief: ArticleBrief | None
    topic_path: str
    sources_path: str
    schema: str
    output: str
    article_output: str
    source_ledger_output: str
    image_ledger_output: str
    claim_ledger_output: str
    preflight_result_output: str
    run_manifest_output: str
    earnings_delta_matrix_output: str
    earnings_delta_matrix_md: str
    events_log: str
    stderr_log: str
    work_run_dir: str
    draft_output: str
    prepare_manifest: str
    web_search: str
    allow_shell_network: bool
    source_ingest_enabled: bool
    source_ingest_allowed_domains: tuple[str, ...]
    max_download_bytes: int
    pdf_render_dpi: int
    max_pdf_pages: int
    max_image_inputs: int
    artifact_sync: ArtifactSyncConfig
    summary_output_enabled: bool
    publish_pdf_enabled: bool
    source_ingest_paths: SourceIngestPaths
    run_artifacts: RunArtifacts
    original_request: str | None
    approve_scope: bool
    depth: str
    analysis_modes: tuple[str, ...]
    waived_blocked_sources: tuple[str, ...]
    max_extra_research_rounds: int
    reset_intent_contract: bool
    allow_unprovenanced_audit: bool


DEFAULT_MAX_EXTRA_RESEARCH_ROUNDS = 1


def default_run_id(prefix: str | None = None) -> str:
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"{prefix}_{timestamp}" if prefix else timestamp


def resolve_repo_arg(parser: argparse.ArgumentParser, root: Path, value: str, label: str) -> str:
    try:
        resolved = resolve_under_root(root, value)
        return resolved.relative_to(root.resolve()).as_posix()
    except ValueError as exc:
        parser.error(f"{label}: {exc}")


def load_profile(
    parser: argparse.ArgumentParser,
    root: Path,
    config_value: str | None,
    profile_name: str,
) -> RunnerProfile:
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


def choose_int(
    cli_value: int | None,
    profile_value: int | None,
    default: int,
    label: str,
    parser: argparse.ArgumentParser,
) -> int:
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


def read_original_request(parser: argparse.ArgumentParser, root: Path, args: argparse.Namespace) -> str | None:
    """Resolve the verbatim user request from either the inline flag or a file."""

    inline = getattr(args, "intake_original_request", None)
    request_file = getattr(args, "intake_original_request_file", None)
    if inline and request_file:
        parser.error("--intake-original-request cannot be combined with --intake-original-request-file")
    if inline:
        return inline
    if not request_file:
        return None
    try:
        path = resolve_under_root(root, request_file)
    except ValueError as exc:
        parser.error(f"--intake-original-request-file: {exc}")
    if not path.is_file():
        parser.error(f"--intake-original-request-file does not exist: {request_file}")
    return path.read_text(encoding="utf-8")


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


def build_run_context(parser: argparse.ArgumentParser, args: argparse.Namespace) -> RunContext:
    root = Path(args.project_root).expanduser().resolve() if args.project_root else find_project_root()
    profile = load_profile(parser, root, args.config, args.profile)

    mode = args.mode or profile.mode
    if not mode:
        parser.error("--mode is required unless the selected config profile provides mode")
    if mode not in WEB_SEARCH_DEFAULTS:
        parser.error("--mode must be one of: industry, earnings, preview")
    phase = "prepare" if args.prepare_only else args.phase
    allow_unprovenanced_audit = bool(
        getattr(args, "allow_unprovenanced_audit", False)
    )
    if allow_unprovenanced_audit and phase != "audit":
        parser.error("--allow-unprovenanced-audit requires --phase audit")
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
    publish_pdf_enabled = choose_bool(args.publish_pdf, profile.publish.enabled)
    original_request = read_original_request(parser, root, args)
    approve_scope = bool(getattr(args, "intake_approve_scope", False))
    reset_intent_contract = bool(
        getattr(args, "intake_reset_intent_contract", False)
    )
    depth = getattr(args, "intake_depth", None) or "standard"
    analysis_modes = tuple(dict.fromkeys(getattr(args, "intake_analysis_mode", None) or ()))
    waived_blocked_sources = tuple(
        dict.fromkeys(
            value.strip()
            for value in (getattr(args, "waive_blocked_source", None) or ())
            if value and value.strip()
        )
    )
    max_extra_research_rounds = choose_int(
        getattr(args, "max_extra_research_rounds", None),
        None,
        DEFAULT_MAX_EXTRA_RESEARCH_ROUNDS,
        "max_extra_research_rounds",
        parser,
    )

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
    prepare_manifest = resolve_repo_arg(parser, root, f"{work_run_dir}/prepare_manifest.json", "prepare manifest")
    source_ingest_paths = build_source_ingest_paths(work_run_dir)
    run_artifacts = build_run_artifacts(
        work_run_dir=work_run_dir,
        outputs_dir=f"outputs/{run_id}",
        article=article_output,
        source_ledger=source_ledger_output,
        image_ledger=image_ledger_output,
        claim_ledger=claim_ledger_output,
        article_result=output,
    )

    return RunContext(
        root=root,
        profile=profile,
        mode=mode,
        phase=phase,
        run_id=run_id,
        intake_brief=intake_brief,
        topic_path=topic_path,
        sources_path=sources_path,
        schema=schema,
        output=output,
        article_output=article_output,
        source_ledger_output=source_ledger_output,
        image_ledger_output=image_ledger_output,
        claim_ledger_output=claim_ledger_output,
        preflight_result_output=preflight_result_output,
        run_manifest_output=run_manifest_output,
        earnings_delta_matrix_output=earnings_delta_matrix_output,
        earnings_delta_matrix_md=earnings_delta_matrix_md,
        events_log=events_log,
        stderr_log=stderr_log,
        work_run_dir=work_run_dir,
        draft_output=draft_output,
        prepare_manifest=prepare_manifest,
        web_search=web_search,
        allow_shell_network=allow_shell_network,
        source_ingest_enabled=source_ingest_enabled,
        source_ingest_allowed_domains=source_ingest_allowed_domains,
        max_download_bytes=max_download_bytes,
        pdf_render_dpi=pdf_render_dpi,
        max_pdf_pages=max_pdf_pages,
        max_image_inputs=max_image_inputs,
        artifact_sync=artifact_sync,
        summary_output_enabled=summary_output_enabled,
        publish_pdf_enabled=publish_pdf_enabled,
        source_ingest_paths=source_ingest_paths,
        run_artifacts=run_artifacts,
        original_request=original_request,
        approve_scope=approve_scope,
        depth=depth,
        analysis_modes=analysis_modes,
        waived_blocked_sources=waived_blocked_sources,
        max_extra_research_rounds=max_extra_research_rounds,
        reset_intent_contract=reset_intent_contract,
        allow_unprovenanced_audit=allow_unprovenanced_audit,
    )
