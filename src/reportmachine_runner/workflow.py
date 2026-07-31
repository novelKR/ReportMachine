from __future__ import annotations

import argparse
import json
import shutil
import sys
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Sequence

from .agent_attempts import collect_agent_executions, validate_agent_attempt_run_ids
from .artifact_validation import validate_run_identity
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
)
from .final_sync import run_final_sync_audit, write_final_sync_report
from .execution_control import RecoveryFenceRequired
from .intake import write_intake_files
from .intent_contract import (
    apply_scope_approval_cas,
    build_intent_contract,
    migrate_intent_contract,
    read_intent_contract,
    read_intent_contract_unvalidated,
    restore_intent_contract_bytes,
    write_intent_contract,
    write_original_request,
)
from .manifest import (
    build_run_manifest,
    effective_prompt_hashes,
    write_run_manifest,
)
from .paths import as_posix_arg, resolve_under_root
from .phase_contracts import PHASES, SCHEMA_DEPENDENT_PHASES
from .phase_provenance import (
    ProvenanceContext,
    artifact_sha256,
    capture_ingest_receipt_baseline,
    has_any_checkpoint,
    provenance_expected,
    provenance_marker_state,
    provenance_marker_payload,
    rebind_runner_owned_artifact,
    read_checkpoint,
    rendered_images_available,
    RunnerArtifactRebindRecoveryRequired,
    runner_owned_artifact_hash_errors,
    validate_checkpoint,
    write_ingest_receipt,
)
from .phases import PhaseOutcome, PhaseRunner, print_phase_failure
from .pipeline import PipelineContext, phases_to_run, run_pipeline
from .preflight import build_preflight_result, write_preflight_result
from .publish import publish_dependency_error
from .publish_workflow import mirror_latest_publish, run_publish_workflow
from .run_config_builder import RunConfigs, build_run_configs, with_image_inputs
from .run_context import RunContext, build_run_context
from .run_contract import build_run_contract
from .run_outputs import (
    article_result_status,
    validate_earnings_delta_outputs,
    write_prepare_manifest,
    write_preflight_failure_outputs,
    write_run_summary_log,
)
from .schema_validation import schema_validation_dependency_error, validate_json_artifact
from .source_ingest import (
    SourceIngestConfig,
    load_source_ingest_result,
    run_research_candidate_ingest,
    run_source_ingest,
)
from .source_ingest_paths import SourceIngestResult
from .summary import build_summary_command, build_summary_prompt, mirror_latest_summary, run_summary_workflow


@dataclass(frozen=True)
class IntentPreparationResult:
    errors: tuple[str, ...] = ()
    messages: tuple[str, ...] = ()


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


PHASE_ROLE_PLAN = {
    "prepare": ("(no codex call)",),
    "scope": ("scope_guardian",),
    "research-plan": ("research_planner",),
    "research": ("primary_source_scout (per lane)", "counterevidence_scout"),
    "evidence": ("evidence_verifier", "evidence_integrator"),
    "narrative": ("thesis_adjudicator", "narrative_architect"),
    "draft": ("article_writer",),
    "review": (
        "citation_logic_checker",
        "editorial_red_team",
        "qa_reviewer",
        "compression_editor",
        "image_slide_auditor (conditional)",
    ),
    "revise": ("article_writer",),
    "finalize": ("finalizer",),
    "audit": ("(no codex call)",),
}


def _print_phase_plan(context: RunContext) -> None:
    """Show which roles each selected phase will invoke, and the active scope state."""

    selected = PHASES if context.phase == "all" else (context.phase,)
    print("phase_plan:")
    for phase in selected:
        if phase not in PHASE_ROLE_PLAN:
            continue
        if phase == "evidence" and context.mode == "earnings":
            roles = (*PHASE_ROLE_PLAN[phase], "earnings_delta_analyst")
        else:
            roles = PHASE_ROLE_PLAN[phase]
        print(f"  {phase}: {', '.join(roles)}")
    print("depth:", context.depth)
    print("analysis_modes:", ", ".join(context.analysis_modes) or "(default for mode)")
    print("scope_approval:", "approved" if context.approve_scope else "pending")
    if context.waived_blocked_sources:
        print("waived_blocked_sources:", ", ".join(context.waived_blocked_sources))


def _print_dry_run(args: argparse.Namespace, context: RunContext, configs: RunConfigs) -> None:
    print("cwd:", context.root)
    print("run_id:", context.run_id)
    print("phase:", context.phase)
    _print_phase_plan(context)
    # Keep a representative command visible so dry runs still show the codex invocation
    # shape, sandbox flags, and effective web-search setting.
    print("command:", configs.command)
    if context.intake_brief:
        print("intake_topic:", context.topic_path)
        print("intake_sources:", context.sources_path)
    # Role prompts are compiled per phase at run time from prompts/core and prompts/roles.
    # The App-native fallback prompt below is what `prepare` writes to prepare_manifest.json.
    print("app_native_prompt:")
    print(configs.app_native_prompt)
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
    if context.publish_pdf_enabled:
        print("publish_dir:", configs.publish_config.paths.publish_dir)
        print("publish_pdf:", configs.publish_config.paths.article_pdf)
        print("publish_result:", configs.publish_config.paths.publish_result)
        print("publish_render_surface: local_deterministic_renderer")
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
        waived_blocked_sources=context.waived_blocked_sources,
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
            "intent_contract": context.run_artifacts.intent_contract,
            "original_request": context.run_artifacts.original_request,
            "evidence_cards": context.run_artifacts.evidence_cards,
            "thesis_decision": context.run_artifacts.thesis_decision,
            "research_appendix": context.run_artifacts.research_appendix,
            "paragraph_map": context.run_artifacts.paragraph_map,
            "final_sync_report": context.run_artifacts.final_sync_report,
        },
        agents_dir=context.run_artifacts.agents_dir,
        contracts_dir=context.run_artifacts.contracts_dir,
        analysis_modes=context.analysis_modes,
        depth=context.depth,
    )
    # A resumed phase rewrites this manifest. Carry the provenance marker across so the
    # rewrite cannot erase the evidence that this run records checkpoints.
    if provenance_expected(_provenance_context(context)):
        run_manifest["provenance_marker"] = provenance_marker_payload()
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
    provenance_status: str | None = None,
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
        provenance_status=provenance_status,
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


def _source_ingest_config(context: RunContext) -> SourceIngestConfig:
    return SourceIngestConfig(
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


def _provenance_context(context: RunContext) -> ProvenanceContext:
    """Resolve this run's provenance scope for workflow-level checks."""

    earnings: tuple[str, ...] = ()
    if context.mode == "earnings":
        earnings = (
            as_posix_arg(context.earnings_delta_matrix_output),
            as_posix_arg(context.earnings_delta_matrix_md),
        )
    provenance = ProvenanceContext(
        root=context.root,
        run_id=context.run_id,
        artifacts=context.run_artifacts,
        earnings_delta=earnings,
        topic_path=as_posix_arg(context.topic_path),
        sources_path=as_posix_arg(context.sources_path),
        run_manifest=as_posix_arg(context.run_manifest_output),
    )
    return replace(
        provenance,
        include_images=rendered_images_available(
            context.root,
            provenance.rendered_images_ledger,
            fallback=context.source_ingest_enabled,
        ),
    )


def _audit_provenance(context: RunContext) -> tuple[str, list[str]]:
    """Report the audited run's provenance state without weakening the code audit.

    Checkpoint-free App-native or historical runs fail by default. They may run the remaining
    code-only checks under an explicit waiver, but that result is never equivalent to verified
    provenance. Once any checkpoint artifact or marker exists, malformed, missing, and
    mismatched finalize state cannot be waived.
    """

    provenance = _provenance_context(context)
    marker_state = provenance_marker_state(provenance)
    checkpoint_exists = has_any_checkpoint(provenance)
    if marker_state == "invalid":
        return (
            "fail",
            [
                "finalize provenance is invalid: run manifest provenance_marker does not "
                "match the current marker contract"
            ],
        )
    # The manifest marker survives deletion of the provenance directory, so a run that ever
    # produced checkpoints cannot pass itself off as legacy by removing them.
    if marker_state == "absent" and not checkpoint_exists:
        if context.allow_unprovenanced_audit:
            return "waived_no_checkpoint", []
        return (
            "fail",
            [
                "finalize provenance is unavailable: no provenance marker or checkpoint "
                "exists; rerun finalize or pass --allow-unprovenanced-audit for a "
                "non-promotable code-only audit"
            ],
        )
    if not checkpoint_exists and marker_state in {"missing", "invalid"}:
        return (
            "fail",
            [
                "finalize provenance cannot be waived because the run manifest does not "
                f"prove marker absence (marker_state={marker_state})"
            ],
        )
    errors = validate_checkpoint(provenance, "finalize")
    return ("pass" if not errors else "fail"), errors


def _run_source_ingest(context: RunContext, configs: RunConfigs) -> RunConfigs:
    if not context.source_ingest_enabled:
        return configs
    ingest_config = _source_ingest_config(context)
    ingest_result = None
    if context.phase not in {"prepare", "all"}:
        ingest_result = load_source_ingest_result(ingest_config)
    if ingest_result is None:
        provenance = _provenance_context(context)
        baseline = capture_ingest_receipt_baseline(provenance)
        ingest_result = run_source_ingest(ingest_config)
        write_ingest_receipt(
            provenance,
            "prepare",
            baseline=baseline,
            input_paths=(as_posix_arg(context.sources_path),),
            written_source_ids=ingest_result.written_source_ids,
            written_image_paths=ingest_result.written_image_paths,
        )
    if not ingest_result.image_inputs:
        return configs
    return with_image_inputs(configs, ingest_result.image_inputs)


def _run_research_source_ingest(
    context: RunContext,
    candidate_paths: Sequence[str],
) -> SourceIngestResult:
    return run_research_candidate_ingest(
        _source_ingest_config(context),
        candidate_paths=candidate_paths,
    )


def _build_phase_runner(
    context: RunContext,
    configs: RunConfigs,
    *,
    validate_schemas: bool,
) -> PhaseRunner:
    """Wire the pipeline runner to this run's exact role boundary."""

    if validate_schemas:
        intent, _ = read_intent_contract(context.root, context.run_artifacts.intent_contract)
    else:
        intent = read_intent_contract_unvalidated(
            context.root,
            context.run_artifacts.intent_contract,
        )
    run_contract = build_run_contract(context, intent)

    def invoke(config: CodexExecConfig, command: Sequence[str]) -> int:
        return run_codex_command(
            config=config,
            command=command,
            artifact_sync=context.artifact_sync,
            artifact_paths=configs.artifact_paths,
            append_logs=True,
        )

    depth = str((intent or {}).get("depth") or context.depth)
    return PhaseRunner(
        root=context.root,
        run_id=context.run_id,
        mode=context.mode,
        artifacts=context.run_artifacts,
        exec_config=configs.config,
        run_contract=run_contract,
        run_codex=invoke,
        depth=depth,
        include_images=context.source_ingest_enabled,
        max_image_inputs=context.max_image_inputs,
    )


def _run_pipeline_phases(
    context: RunContext,
    configs: RunConfigs,
    *,
    validate_schemas: bool,
) -> int:
    """Execute the ordered role phases for the requested --phase value."""

    phases = phases_to_run(context.phase)
    if not phases:
        return 0
    runner = _build_phase_runner(context, configs, validate_schemas=validate_schemas)
    pipeline = PipelineContext(
        runner=runner,
        artifacts=context.run_artifacts,
        mode=context.mode,
        approve_scope=context.approve_scope,
        max_extra_research_rounds=context.max_extra_research_rounds,
        research_ingest=(
            (
                lambda paths: _run_research_source_ingest(
                    context,
                    paths,
                )
            )
            if context.source_ingest_enabled
            else None
        ),
        validate_schemas=validate_schemas,
    )
    outcome = run_pipeline(pipeline, phases)
    if not outcome.ok:
        print_phase_failure(f"Phase failed ({outcome.label})", outcome.messages)
        if context.artifact_sync.enabled and context.artifact_sync.preserve_cache_on_failure:
            try_snapshot_external_artifacts(context.artifact_sync, configs.artifact_paths)
        _write_summary_log(context, status=outcome.label, preflight_status="pass")
        if outcome.recovery_required:
            raise RecoveryFenceRequired(
                "role process-group cleanup could not prove that all descendant writers stopped"
            )
        return outcome.status
    for message in outcome.messages:
        print(message)
    if context.phase in set(PHASES) - {"finalize", "audit", "prepare"}:
        _write_summary_log(context, status=f"{context.phase}_completed", preflight_status="pass")
        print("cwd:", context.root)
        print("run_id:", context.run_id)
        if context.phase == "draft" and not validate_schemas:
            print(
                "phase `draft` completed; artifact syntax and semantics validated "
                "(JSON Schema validation skipped because jsonschema is unavailable)"
            )
        else:
            print(f"phase `{context.phase}` completed; artifacts validated")
        return 0
    return 0


def _refresh_run_manifest(context: RunContext, configs: RunConfigs) -> list[str]:
    """Rewrite the manifest after agents ran so it records real executions."""

    manifest_rel = as_posix_arg(context.run_manifest_output)
    try:
        existing = json.loads(
            resolve_under_root(context.root, manifest_rel).read_text(encoding="utf-8")
        )
    except (OSError, json.JSONDecodeError):
        return []
    if not isinstance(existing, dict):
        return []
    identity_errors = validate_run_identity(
        existing,
        expected_run_id=context.run_id,
        label="run manifest",
        artifact=manifest_rel,
    )
    identity_errors.extend(
        validate_agent_attempt_run_ids(
            context.root,
            context.run_artifacts.agents_dir,
            expected_run_id=context.run_id,
        )
    )
    if identity_errors:
        return identity_errors
    existing["phase"] = context.phase
    existing["agent_executions"] = collect_agent_executions(
        context.root,
        context.run_artifacts.agents_dir,
    )
    existing["effective_prompt_hashes"] = effective_prompt_hashes(
        context.root,
        context.run_artifacts.contracts_dir,
    )
    # This WIP field was a mutable observation overwritten before every audit, not an
    # integrity baseline. Finalize checkpoints are the canonical trust record and the fresh
    # final-sync report records current hashes, so remove any previously generated copy.
    existing.pop("final_artifact_hashes", None)
    write_run_manifest(context.root, manifest_rel, existing)
    return []


def _run_final_audit(context: RunContext, configs: RunConfigs) -> int:
    """Validate final outputs, then run the code-level cross audit."""

    validation_errors = _refresh_run_manifest(context, configs)
    validation_errors.extend(
        validate_file_snapshot(configs.config, make_file_snapshot(configs.config))
    )
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
    sync_result = run_final_sync_audit(
        root=context.root,
        run_id=context.run_id,
        artifacts=context.run_artifacts,
        require_evidence_cards=True,
    )
    write_final_sync_report(
        context.root,
        context.run_artifacts.final_sync_report,
        sync_result.payload,
    )
    validation_errors.extend(sync_result.errors)
    _sync_payload, sync_schema_errors = validate_json_artifact(
        root=context.root,
        artifact=context.run_artifacts.final_sync_report,
        schema="schemas/final_sync_report.schema.json",
        label="final sync report",
    )
    validation_errors.extend(sync_schema_errors)
    for warning in sync_result.warnings:
        print(f"final sync warning: {warning}", file=sys.stderr)
    if validation_errors:
        print("Post-run validation failed:", file=sys.stderr)
        for error in validation_errors:
            print(f" - {error}", file=sys.stderr)
        if context.artifact_sync.enabled and context.artifact_sync.preserve_cache_on_failure:
            try_snapshot_external_artifacts(context.artifact_sync, configs.artifact_paths)
        _write_summary_log(context, status="post_run_validation_fail", preflight_status="pass")
        return 2

    if context.publish_pdf_enabled:
        publish_status = run_publish_workflow(configs.publish_config, mirror_latest=False)
        if publish_status != 0:
            if context.artifact_sync.enabled and context.artifact_sync.preserve_cache_on_failure:
                try_snapshot_external_artifacts(context.artifact_sync, configs.artifact_paths)
            return publish_status

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
        if context.publish_pdf_enabled:
            mirror_latest_publish(context.root, configs.publish_config.paths)
        if context.summary_output_enabled:
            mirror_latest_summary(context.root, configs.summary_config.paths)
    cleanup_success_cache(context.artifact_sync, configs.artifact_paths)
    _write_summary_log(context, status="completed", preflight_status="pass")
    return 0


def run_article_workflow(
    parser: argparse.ArgumentParser,
    args: argparse.Namespace,
    *,
    context: RunContext | None = None,
) -> int:
    context = context or build_run_context(parser, args)
    if context.approve_scope_only:
        return _approve_scope_only(context)

    configs = build_run_configs(
        context,
        codex_bin=args.codex_bin,
        json_events=args.json_events,
    )

    if args.dry_run or args.smoke_test:
        _print_dry_run(args, context, configs)
        return 0

    dependency_error = schema_validation_dependency_error()
    validate_schemas = dependency_error is None
    if context.phase in SCHEMA_DEPENDENT_PHASES:
        # Draft intentionally has a parse-only fallback. Every other non-prepare
        # phase fails before Codex when schema validation cannot run.
        if dependency_error is not None:
            print(dependency_error, file=sys.stderr)
            return 2
        # Publishing runs after finalize, but the optional renderer dependency is
        # probed here for the same reason: fail before Codex spends budget.
        if context.publish_pdf_enabled:
            publish_error = publish_dependency_error()
            if publish_error:
                print(publish_error, file=sys.stderr)
                return 2

    if context.phase != "audit":
        intent_result = _prepare_intent_artifacts(context)
        if intent_result.errors:
            print("Intent contract preparation failed:", file=sys.stderr)
            for error in intent_result.errors:
                print(f" - {error}", file=sys.stderr)
            return 2
        for message in intent_result.messages:
            print(message)

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

    if context.phase == "audit":
        return _run_audit_phase(context, configs)

    preflight = _write_preflight_and_manifests(args, context, configs)
    if not preflight.passed:
        return _handle_preflight_failure(context, preflight.payload)

    if context.phase == "prepare":
        return _handle_prepare(args, context)

    pipeline_status = _run_pipeline_phases(
        context,
        configs,
        validate_schemas=validate_schemas,
    )
    if pipeline_status != 0:
        return pipeline_status
    if context.phase not in {"finalize", "all"}:
        return 0

    return _run_final_audit(context, configs)


def _approve_scope_only(context: RunContext) -> int:
    """CAS-approve one existing intent contract without running the workflow."""

    artifacts = context.run_artifacts
    payload, validation_errors = read_intent_contract(
        context.root,
        artifacts.intent_contract,
    )
    if payload is None or validation_errors:
        print("Scope approval failed:", file=sys.stderr)
        for error in validation_errors or (
            f"missing intent contract: {artifacts.intent_contract}",
        ):
            print(f" - {error}", file=sys.stderr)
        return 2

    assert context.approve_scope_sha256 is not None
    result = apply_scope_approval_cas(
        payload,
        expected_run_id=context.run_id,
        expected_mode=context.mode,
        expected_scope_sha256=context.approve_scope_sha256,
    )
    if result.errors:
        print("Scope approval failed:", file=sys.stderr)
        for error in result.errors:
            print(f" - {error}", file=sys.stderr)
        return 2
    if not result.changed:
        provenance = _provenance_context(context)
        current_sha256 = artifact_sha256(provenance, artifacts.intent_contract)
        consistency_errors = runner_owned_artifact_hash_errors(
            provenance,
            artifacts.intent_contract,
            expected_sha256=current_sha256,
        )
        if consistency_errors:
            print("Scope approval failed:", file=sys.stderr)
            for error in consistency_errors:
                print(f" - {error}", file=sys.stderr)
            return 2
        print("scope_approval: already approved for the current material scope")
        print("run_id:", context.run_id)
        return 0

    try:
        _write_and_rebind_intent_contract(
            context,
            result.payload,
            require_all_matching=True,
        )
    except RecoveryFenceRequired:
        raise
    except BaseException as exc:
        print(
            f"Scope approval failed: intent/provenance update rolled back: {type(exc).__name__}",
            file=sys.stderr,
        )
        return 2
    print("scope_approval: approved current material scope")
    print("run_id:", context.run_id)
    print("intent_contract:", artifacts.intent_contract)
    return 0


def _write_and_rebind_intent_contract(
    context: RunContext,
    payload: dict[str, object],
    *,
    require_all_matching: bool = False,
) -> None:
    artifacts = context.run_artifacts
    provenance = _provenance_context(context)
    before_sha256 = artifact_sha256(provenance, artifacts.intent_contract)
    target = resolve_under_root(context.root, artifacts.intent_contract)
    original = target.read_bytes()
    try:
        write_intent_contract(context.root, artifacts.intent_contract, payload)
        rebind_runner_owned_artifact(
            provenance,
            artifacts.intent_contract,
            previous_sha256=before_sha256,
            require_all_matching=require_all_matching,
        )
    except BaseException as exc:
        try:
            restore_intent_contract_bytes(
                context.root,
                artifacts.intent_contract,
                original,
            )
        except BaseException as restore_exc:
            raise RecoveryFenceRequired(
                "intent/provenance update failed and the intent contract rollback failed: "
                f"{type(restore_exc).__name__}"
            ) from exc
        if isinstance(exc, RunnerArtifactRebindRecoveryRequired):
            raise RecoveryFenceRequired(str(exc)) from exc
        raise


def _new_intent_contract(context: RunContext, *, approved: bool) -> dict[str, object]:
    primary_question = ""
    if context.intake_brief and context.intake_brief.core_question:
        primary_question = context.intake_brief.core_question.strip()
    return build_intent_contract(
        run_id=context.run_id,
        mode=context.mode,
        original_request=context.original_request,
        primary_question=primary_question,
        analysis_modes=list(context.analysis_modes) or None,
        depth=context.depth,
        approved=approved,
    )


def _next_intent_backup_paths(context: RunContext) -> tuple[Path, Path]:
    artifacts = context.run_artifacts
    contract_path = resolve_under_root(context.root, artifacts.intent_contract)
    request_path = resolve_under_root(context.root, artifacts.original_request)
    for index in range(1, 1000):
        suffix = f"mismatched_{index:03d}"
        contract_backup = contract_path.with_name(
            f"{contract_path.stem}.{suffix}{contract_path.suffix}"
        )
        request_backup = request_path.with_name(
            f"{request_path.stem}.{suffix}{request_path.suffix}"
        )
        if not contract_backup.exists() and not request_backup.exists():
            return contract_backup, request_backup
    raise OSError("no available intent contract backup slot from 001 through 999")


def _reset_invalid_intent_contract(context: RunContext) -> IntentPreparationResult:
    artifacts = context.run_artifacts
    contract_path = resolve_under_root(context.root, artifacts.intent_contract)
    request_path = resolve_under_root(context.root, artifacts.original_request)
    try:
        contract_backup, request_backup = _next_intent_backup_paths(context)
        contract_backup.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(contract_path, contract_backup)
        messages = [
            "Backed up invalid intent contract to "
            f"{contract_backup.relative_to(context.root.resolve()).as_posix()}"
        ]
        if request_path.is_file():
            shutil.copy2(request_path, request_backup)
            messages.append(
                "Backed up original request to "
                f"{request_backup.relative_to(context.root.resolve()).as_posix()}"
            )
        write_original_request(
            context.root,
            artifacts.original_request,
            context.original_request,
        )
        # Reset never carries approval forward. The normal scope gate applies an
        # explicitly supplied --intake-approve-scope after the guardian runs.
        write_intent_contract(
            context.root,
            artifacts.intent_contract,
            _new_intent_contract(context, approved=False),
        )
    except OSError as exc:
        return IntentPreparationResult(
            errors=(f"could not reset intent contract: {exc}",)
        )
    return IntentPreparationResult(messages=tuple(messages))


def _prepare_intent_artifacts(context: RunContext) -> IntentPreparationResult:
    """Validate identity before seeding or updating the run's intent artifacts."""

    artifacts = context.run_artifacts
    contract_path = resolve_under_root(context.root, artifacts.intent_contract)
    request_path = resolve_under_root(context.root, artifacts.original_request)

    # Parse without schema validation: `prepare` must stay runnable without the
    # schema-validation dependency, and the scope gate still validates the contract.
    existing = read_intent_contract_unvalidated(context.root, artifacts.intent_contract)
    if contract_path.is_file() and existing is None:
        if context.reset_intent_contract:
            return _reset_invalid_intent_contract(context)
        return IntentPreparationResult(
            errors=(
                "intent contract is malformed or is not a JSON object: "
                f"{artifacts.intent_contract}; use --intake-reset-intent-contract "
                "to back it up and create a pending current-run contract",
            )
        )
    if existing is not None:
        identity_errors = validate_run_identity(
            existing,
            expected_run_id=context.run_id,
            label="intent contract",
            artifact=artifacts.intent_contract,
        )
        if identity_errors:
            if context.reset_intent_contract:
                return _reset_invalid_intent_contract(context)
            return IntentPreparationResult(
                errors=tuple(
                    identity_errors
                    + [
                        "use --intake-reset-intent-contract to back up the mismatched "
                        "artifacts and create a pending current-run contract"
                    ]
                )
            )
        if not request_path.is_file():
            write_original_request(
                context.root,
                artifacts.original_request,
                context.original_request,
            )
        updated = migrate_intent_contract(existing)
        if context.approve_scope:
            from .intent_contract import apply_scope_approval

            updated = apply_scope_approval(updated, approved=True)
        if updated is not existing:
            _write_and_rebind_intent_contract(context, updated)
        return IntentPreparationResult()

    write_original_request(context.root, artifacts.original_request, context.original_request)
    write_intent_contract(
        context.root,
        artifacts.intent_contract,
        _new_intent_contract(context, approved=context.approve_scope),
    )
    return IntentPreparationResult()


def _run_audit_phase(context: RunContext, configs: RunConfigs) -> int:
    """Run the code-only cross audit against existing final outputs."""

    validation_errors = _refresh_run_manifest(context, configs)
    provenance_status, provenance_errors = _audit_provenance(context)
    validation_errors.extend(provenance_errors)
    validation_errors.extend(
        validate_file_snapshot(configs.config, make_file_snapshot(configs.config))
    )
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
    sync_result = run_final_sync_audit(
        root=context.root,
        run_id=context.run_id,
        artifacts=context.run_artifacts,
        require_evidence_cards=True,
    )
    validation_errors.extend(sync_result.errors)
    # Write before validating, matching the finalize path: an audit of an App-native or
    # historical run has no prior report, and any existing one is from an earlier audit.
    write_final_sync_report(
        context.root,
        context.run_artifacts.final_sync_report,
        sync_result.payload,
    )
    _sync_payload, sync_schema_errors = validate_json_artifact(
        root=context.root,
        artifact=context.run_artifacts.final_sync_report,
        schema="schemas/final_sync_report.schema.json",
        label="final sync report",
    )
    validation_errors.extend(sync_schema_errors)
    print("cwd:", context.root)
    print("run_id:", context.run_id)
    print("final_sync_report:", context.run_artifacts.final_sync_report)
    print("audit_status:", sync_result.payload.get("status"))
    print("provenance_status:", provenance_status)
    if provenance_status == "waived_no_checkpoint":
        print(
            "warning: provenance is unavailable; explicit audit waiver used and "
            "outputs/latest will not be updated"
        )
    for warning in sync_result.warnings:
        print(f"warning: {warning}")
    if validation_errors:
        print("Audit failed:", file=sys.stderr)
        for error in validation_errors:
            print(f" - {error}", file=sys.stderr)
        _write_summary_log(
            context,
            status="audit_failed",
            preflight_status="pass",
            provenance_status=provenance_status,
        )
        return 2
    summary_status = (
        "audit_passed_with_provenance_waiver"
        if provenance_status == "waived_no_checkpoint"
        else "audit_passed"
    )
    _write_summary_log(
        context,
        status=summary_status,
        preflight_status="pass",
        provenance_status=provenance_status,
    )
    if (
        provenance_status == "pass"
        and article_result_status(context.root, as_posix_arg(context.output)) != "fail"
    ):
        mirror_latest_outputs(configs.config)
    return 0
