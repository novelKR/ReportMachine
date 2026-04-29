from __future__ import annotations

import argparse
import sys
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
from .audit import run_finalize_audits
from .codex_exec import (
    CodexExecConfig,
    ensure_output_directories,
    make_file_snapshot,
    mirror_latest_outputs,
    run_command,
    validate_file_snapshot,
    validate_path_snapshot,
)
from .intake import write_intake_files
from .manifest import build_run_manifest, write_run_manifest
from .paths import as_posix_arg, resolve_under_root
from .preflight import build_preflight_result, write_preflight_result
from .run_config_builder import RunConfigs, build_run_configs, with_image_inputs
from .run_context import RunContext, build_run_context
from .run_outputs import (
    article_result_status,
    validate_earnings_delta_outputs,
    write_prepare_manifest,
    write_preflight_failure_outputs,
    write_run_summary_log,
)
from .source_ingest import SourceIngestConfig, run_source_ingest
from .summary import build_summary_command, build_summary_prompt, mirror_latest_summary, run_summary_workflow


def try_snapshot_external_artifacts(
    artifact_sync: ArtifactSyncConfig,
    artifact_paths: ArtifactPaths,
) -> None:
    try:
        snapshot_external_artifacts(artifact_sync, artifact_paths)
    except Exception as exc:
        print(f"Artifact snapshot failed: {exc}", file=sys.stderr)


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


def _print_dry_run(args: argparse.Namespace, context: RunContext, configs: RunConfigs) -> None:
    print("cwd:", context.root)
    print("run_id:", context.run_id)
    print("phase:", context.phase)
    print(
        "command:",
        configs.command
        if context.phase in {"finalize", "all"}
        else configs.draft_command
        if context.phase == "draft"
        else [],
    )
    if context.phase == "all":
        print("draft_command:", configs.draft_command)
        print("finalize_command:", configs.command)
    if context.intake_brief:
        print("intake_topic:", context.topic_path)
        print("intake_sources:", context.sources_path)
    print("prompt:")
    print(configs.draft_prompt if context.phase == "draft" else configs.prompt)
    if context.summary_output_enabled:
        summary_prompt = build_summary_prompt(
            configs.summary_config,
            article_status="available_after_article_run",
        )
        print("summary_dir:", configs.summary_config.paths.summary_dir)
        print("summary_result:", configs.summary_config.paths.summary_result)
        print("summary_image:", configs.summary_config.paths.summary_image)
        print("summary_command:", build_summary_command(configs.summary_config, summary_prompt))
        print("summary_prompt:")
        print(summary_prompt)
    if args.smoke_test:
        print("smoke-test: command and prompt built without execution")


def _write_preflight_and_manifests(
    args: argparse.Namespace,
    context: RunContext,
    configs: RunConfigs,
):
    preflight = build_preflight_result(
        root=context.root,
        mode=context.mode,
        run_id=context.run_id,
        sources_path=context.sources_path,
        source_candidate_ledger=context.source_ingest_paths.source_candidate_ledger,
    )
    write_preflight_result(context.root, as_posix_arg(context.preflight_result_output), preflight)
    run_manifest = build_run_manifest(
        root=context.root,
        mode=context.mode,
        run_id=context.run_id,
        phase=context.phase,
        codex_bin=args.codex_bin,
        schema=context.schema,
        web_search=context.web_search,
        allow_shell_network=context.allow_shell_network,
        source_ingest_enabled=context.source_ingest_enabled,
        source_candidate_ledger=context.source_ingest_paths.source_candidate_ledger,
        paths={
            "topic": context.topic_path,
            "sources": context.sources_path,
            "work_run_dir": as_posix_arg(context.work_run_dir),
            "article_result": as_posix_arg(context.output),
            "article": as_posix_arg(context.article_output),
            "source_ledger": as_posix_arg(context.source_ledger_output),
            "image_ledger": as_posix_arg(context.image_ledger_output),
            "claim_ledger": as_posix_arg(context.claim_ledger_output),
            "preflight_result": as_posix_arg(context.preflight_result_output),
            "run_manifest": as_posix_arg(context.run_manifest_output),
            "earnings_delta_matrix": as_posix_arg(context.earnings_delta_matrix_output),
            "earnings_delta_matrix_md": as_posix_arg(context.earnings_delta_matrix_md),
            "events_log": as_posix_arg(context.events_log) if args.json_events else None,
            "stderr_log": as_posix_arg(context.stderr_log) if args.json_events else None,
        },
    )
    write_run_manifest(context.root, as_posix_arg(context.run_manifest_output), run_manifest)
    write_prepare_manifest(
        root=context.root,
        mode=context.mode,
        config=configs.config,
        command=configs.command,
        topic_path=context.topic_path,
        sources_path=context.sources_path,
        prepare_manifest=context.prepare_manifest,
        prompt=configs.app_native_prompt,
    )
    return preflight


def _write_summary_log(
    context: RunContext,
    *,
    status: str,
    preflight_status: str,
) -> None:
    write_run_summary_log(
        root=context.root,
        run_id=context.run_id,
        phase=context.phase,
        status=status,
        preflight_status=preflight_status,
        article_result=as_posix_arg(context.output),
        events_log=as_posix_arg(context.events_log),
        stderr_log=as_posix_arg(context.stderr_log),
    )


def _handle_preflight_failure(context: RunContext, preflight_payload: dict) -> int:
    write_preflight_failure_outputs(
        root=context.root,
        mode=context.mode,
        preflight_payload=preflight_payload,
        output=as_posix_arg(context.output),
        article_output=as_posix_arg(context.article_output),
        source_ledger_output=as_posix_arg(context.source_ledger_output),
        image_ledger_output=as_posix_arg(context.image_ledger_output),
        claim_ledger_output=as_posix_arg(context.claim_ledger_output),
        preflight_result_output=as_posix_arg(context.preflight_result_output),
        run_manifest_output=as_posix_arg(context.run_manifest_output),
        earnings_delta_matrix_output=as_posix_arg(context.earnings_delta_matrix_output),
        earnings_delta_matrix_md=as_posix_arg(context.earnings_delta_matrix_md),
        source_candidate_ledger=context.source_ingest_paths.source_candidate_ledger,
    )
    _write_summary_log(context, status="preflight_fail", preflight_status="fail")
    print("cwd:", context.root)
    print("run_id:", context.run_id)
    print("preflight_result:", context.preflight_result_output)
    print("preflight: fail; wrote schema-valid failure outputs without running Codex")
    return 0


def _handle_prepare(args: argparse.Namespace, context: RunContext) -> int:
    _write_summary_log(context, status="prepared", preflight_status="pass")
    print("cwd:", context.root)
    print("run_id:", context.run_id)
    print("prepare_manifest:", context.prepare_manifest)
    print("preflight_result:", context.preflight_result_output)
    print("run_manifest:", context.run_manifest_output)
    if args.prepare_only:
        print("prepare-only: directories, source ingest artifacts, preflight, and manifests prepared without running Codex")
    else:
        print("prepare: directories, source ingest artifacts, preflight, and manifests prepared without running Codex")
    return 0


def _run_source_ingest(context: RunContext, configs: RunConfigs) -> RunConfigs:
    if not context.source_ingest_enabled:
        return configs
    ingest_result = run_source_ingest(
        SourceIngestConfig(
            root=context.root,
            mode=context.mode,
            run_id=context.run_id,
            sources_path=context.sources_path,
            work_run_dir=context.work_run_dir,
            allow_downloads=context.allow_shell_network,
            enabled=context.source_ingest_enabled,
            allowed_domains=context.source_ingest_allowed_domains,
            max_download_bytes=context.max_download_bytes,
            pdf_render_dpi=context.pdf_render_dpi,
            max_pdf_pages=context.max_pdf_pages,
            max_image_inputs=context.max_image_inputs,
        )
    )
    if not ingest_result.image_inputs:
        return configs
    return with_image_inputs(configs, ingest_result.image_inputs)


def _run_draft_phase(context: RunContext, configs: RunConfigs) -> int | None:
    if context.phase not in {"draft", "all"}:
        return None

    draft_snapshot = make_file_snapshot(configs.draft_config)
    status = run_codex_command(
        config=configs.draft_config,
        command=configs.draft_command,
        artifact_sync=context.artifact_sync,
        artifact_paths=configs.artifact_paths,
        append_logs=False,
    )
    if status != 0:
        if context.artifact_sync.enabled and context.artifact_sync.preserve_cache_on_failure:
            try_snapshot_external_artifacts(context.artifact_sync, configs.artifact_paths)
        _write_summary_log(context, status="draft_codex_fail", preflight_status="pass")
        return status
    draft_validation_errors = validate_path_snapshot(context.root, draft_snapshot)
    if context.phase == "draft" and not resolve_under_root(context.root, configs.draft_config.draft_output).is_file():
        draft_validation_errors.append(f"missing configured draft output: {configs.draft_config.draft_output}")
    if draft_validation_errors:
        print("Draft phase validation failed:", file=sys.stderr)
        for error in draft_validation_errors:
            print(f" - {error}", file=sys.stderr)
        if context.artifact_sync.enabled and context.artifact_sync.preserve_cache_on_failure:
            try_snapshot_external_artifacts(context.artifact_sync, configs.artifact_paths)
        _write_summary_log(context, status="draft_validation_fail", preflight_status="pass")
        return 2
    if context.phase == "draft":
        _write_summary_log(context, status="drafted", preflight_status="pass")
        print("cwd:", context.root)
        print("run_id:", context.run_id)
        print("draft_output:", configs.draft_config.draft_output)
        print("draft: completed without finalizing article outputs")
        return 0
    return None


def _run_finalize_phase(context: RunContext, configs: RunConfigs) -> int:
    if context.phase == "finalize" and not resolve_under_root(context.root, context.draft_output).is_file():
        print(f"Finalize phase requires draft output: {context.draft_output}", file=sys.stderr)
        _write_summary_log(context, status="missing_draft", preflight_status="pass")
        return 2

    snapshot = make_file_snapshot(configs.config)
    status = run_codex_command(
        config=configs.config,
        command=configs.command,
        artifact_sync=context.artifact_sync,
        artifact_paths=configs.artifact_paths,
        append_logs=context.phase == "all",
    )
    if status != 0:
        if context.artifact_sync.enabled and context.artifact_sync.preserve_cache_on_failure:
            try_snapshot_external_artifacts(context.artifact_sync, configs.artifact_paths)
        _write_summary_log(context, status="codex_fail", preflight_status="pass")
        return status

    validation_errors = validate_file_snapshot(configs.config, snapshot)
    validation_errors.extend(validate_earnings_delta_outputs(configs.config, context.mode))
    validation_errors.extend(
        run_finalize_audits(
            root=context.root,
            mode=context.mode,
            article_result=as_posix_arg(context.output),
            article_output=as_posix_arg(context.article_output),
            claim_ledger_output=as_posix_arg(context.claim_ledger_output),
            image_ledger_output=as_posix_arg(context.image_ledger_output),
            source_ledger_output=as_posix_arg(context.source_ledger_output),
            pdf_index=context.source_ingest_paths.pdf_index,
        )
    )
    if validation_errors:
        print("Post-run validation failed:", file=sys.stderr)
        for error in validation_errors:
            print(f" - {error}", file=sys.stderr)
        if context.artifact_sync.enabled and context.artifact_sync.preserve_cache_on_failure:
            try_snapshot_external_artifacts(context.artifact_sync, configs.artifact_paths)
        _write_summary_log(context, status="post_run_validation_fail", preflight_status="pass")
        return 2

    if context.summary_output_enabled:
        summary_status = run_summary_workflow(configs.summary_config, mirror_latest=False)
        if summary_status != 0:
            if context.artifact_sync.enabled and context.artifact_sync.preserve_cache_on_failure:
                try_snapshot_external_artifacts(context.artifact_sync, configs.artifact_paths)
            return summary_status

    try:
        export_final_artifacts(context.artifact_sync, configs.artifact_paths)
    except OSError as exc:
        print(f"Final artifact export failed: {exc}", file=sys.stderr)
        return 2
    if article_result_status(context.root, as_posix_arg(context.output)) != "fail":
        mirror_latest_outputs(configs.config)
        if context.summary_output_enabled:
            mirror_latest_summary(context.root, configs.summary_config.paths)
    cleanup_success_cache(context.artifact_sync, configs.artifact_paths)
    _write_summary_log(context, status="completed", preflight_status="pass")
    return 0


def run_article_workflow(parser: argparse.ArgumentParser, args: argparse.Namespace) -> int:
    context = build_run_context(parser, args)
    configs = build_run_configs(
        context,
        codex_bin=args.codex_bin,
        json_events=args.json_events,
    )

    if args.dry_run or args.smoke_test:
        _print_dry_run(args, context, configs)
        return 0

    if context.intake_brief:
        write_intake_files(
            root=context.root,
            run_id=context.run_id,
            brief=context.intake_brief,
            mirror_inputs=args.intake_mirror_inputs,
        )
    ensure_output_directories(configs.config)
    import_existing_artifacts(context.artifact_sync, configs.artifact_paths)
    configs = _run_source_ingest(context, configs)

    preflight = _write_preflight_and_manifests(args, context, configs)
    if not preflight.passed:
        return _handle_preflight_failure(context, preflight.payload)

    if context.phase == "prepare":
        return _handle_prepare(args, context)

    draft_status = _run_draft_phase(context, configs)
    if draft_status is not None:
        return draft_status

    return _run_finalize_phase(context, configs)
