from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Sequence

from .artifacts import ArtifactPaths
from .codex_exec import CodexExecConfig, build_command
from .paths import as_posix_arg
from .prompt import build_prompt
from .run_context import RunContext
from .publish import PublishSourcePaths, build_publish_run_config
from .summary import SUMMARY_SCHEMA, SummarySourcePaths, build_summary_run_config


@dataclass(frozen=True)
class RunConfigs:
    prompt: str
    draft_prompt: str
    app_native_prompt: str
    config: CodexExecConfig
    draft_config: CodexExecConfig
    command: list[str]
    draft_command: list[str]
    artifact_paths: ArtifactPaths
    summary_config: object
    publish_config: object


def build_prompt_kwargs(context: RunContext) -> dict[str, object]:
    return {
        "mode": context.mode,
        "phase": "finalize",
        "run_id": context.run_id,
        "topic_path": context.topic_path,
        "sources_path": context.sources_path,
        "work_run_dir": as_posix_arg(context.work_run_dir),
        "draft_output": as_posix_arg(context.draft_output),
        "image_audit_report": context.run_artifacts.image_audit_report,
        "source_ingest_manifest": as_posix_arg(context.source_ingest_paths.manifest),
        "source_candidate_ledger": as_posix_arg(context.source_ingest_paths.source_candidate_ledger),
        "pdf_index": as_posix_arg(context.source_ingest_paths.pdf_index),
        "rendered_images_ledger": as_posix_arg(context.source_ingest_paths.rendered_images_ledger),
        "source_ingest_enabled": context.source_ingest_enabled,
        "article_output": as_posix_arg(context.article_output),
        "source_ledger_output": as_posix_arg(context.source_ledger_output),
        "image_ledger_output": as_posix_arg(context.image_ledger_output),
        "claim_ledger_output": as_posix_arg(context.claim_ledger_output),
        "preflight_result_output": as_posix_arg(context.preflight_result_output),
        "run_manifest_output": as_posix_arg(context.run_manifest_output),
        "earnings_delta_matrix_output": as_posix_arg(context.earnings_delta_matrix_output),
        "earnings_delta_matrix_md": as_posix_arg(context.earnings_delta_matrix_md),
        "result_output": as_posix_arg(context.output),
    }


def with_image_inputs(configs: RunConfigs, image_inputs: Sequence[str]) -> RunConfigs:
    config = replace(configs.config, image_inputs=tuple(image_inputs))
    draft_config = replace(configs.draft_config, image_inputs=tuple(image_inputs))
    return replace(
        configs,
        config=config,
        draft_config=draft_config,
        command=build_command(config),
        draft_command=build_command(draft_config),
    )


def build_run_configs(
    context: RunContext,
    *,
    codex_bin: str,
    json_events: bool,
) -> RunConfigs:
    prompt_kwargs = build_prompt_kwargs(context)
    prompt = build_prompt(**prompt_kwargs)
    draft_prompt = build_prompt(**{**prompt_kwargs, "phase": "draft"})
    app_native_prompt = build_prompt(**prompt_kwargs, app_native_fallback=True)

    config = CodexExecConfig(
        root=context.root,
        codex_bin=codex_bin,
        run_id=context.run_id,
        phase="finalize",
        schema=context.schema,
        output=as_posix_arg(context.output),
        article_output=as_posix_arg(context.article_output),
        source_ledger_output=as_posix_arg(context.source_ledger_output),
        image_ledger_output=as_posix_arg(context.image_ledger_output),
        claim_ledger_output=as_posix_arg(context.claim_ledger_output),
        preflight_result_output=as_posix_arg(context.preflight_result_output),
        run_manifest_output=as_posix_arg(context.run_manifest_output),
        earnings_delta_matrix_output=as_posix_arg(context.earnings_delta_matrix_output),
        earnings_delta_matrix_md=as_posix_arg(context.earnings_delta_matrix_md),
        work_run_dir=as_posix_arg(context.work_run_dir),
        draft_output=as_posix_arg(context.draft_output),
        source_ingest_manifest=as_posix_arg(context.source_ingest_paths.manifest),
        source_candidate_ledger=as_posix_arg(context.source_ingest_paths.source_candidate_ledger),
        pdf_index=as_posix_arg(context.source_ingest_paths.pdf_index),
        rendered_images_ledger=as_posix_arg(context.source_ingest_paths.rendered_images_ledger),
        latest_dir="outputs/latest",
        web_search=context.web_search,
        allow_shell_network=context.allow_shell_network,
        image_inputs=(),
        structured_output=True,
        json_events=json_events,
        events_log=as_posix_arg(context.events_log),
        stderr_log=as_posix_arg(context.stderr_log),
        artifact_sync=context.artifact_sync,
        prompt=prompt,
    )
    draft_config = replace(
        config,
        phase="draft",
        structured_output=False,
        prompt=draft_prompt,
    )
    artifact_paths = ArtifactPaths(
        root=context.root,
        run_id=context.run_id,
        work_run_dir=as_posix_arg(context.work_run_dir),
        output=as_posix_arg(context.output),
        article_output=as_posix_arg(context.article_output),
        source_ledger_output=as_posix_arg(context.source_ledger_output),
        image_ledger_output=as_posix_arg(context.image_ledger_output),
        claim_ledger_output=as_posix_arg(context.claim_ledger_output),
        preflight_result_output=as_posix_arg(context.preflight_result_output),
        run_manifest_output=as_posix_arg(context.run_manifest_output),
        earnings_delta_matrix_output=as_posix_arg(context.earnings_delta_matrix_output),
        earnings_delta_matrix_md=as_posix_arg(context.earnings_delta_matrix_md),
        summary_dir=f"outputs/{context.run_id}/summary" if context.summary_output_enabled else None,
        publish_dir=f"outputs/{context.run_id}/publish" if context.publish_pdf_enabled else None,
    )
    summary_config = build_summary_run_config(
        root=context.root,
        run_id=context.run_id,
        codex_bin=codex_bin,
        schema=SUMMARY_SCHEMA,
        web_search="disabled",
        profile=context.profile.summary,
        sources=SummarySourcePaths(
            article_result=as_posix_arg(context.output),
            article=as_posix_arg(context.article_output),
            source_ledger=as_posix_arg(context.source_ledger_output),
            image_ledger=as_posix_arg(context.image_ledger_output),
        ),
    )
    publish_config = build_publish_run_config(
        root=context.root,
        run_id=context.run_id,
        profile=context.profile.publish,
        sources=PublishSourcePaths(
            article_result=as_posix_arg(context.output),
            article=as_posix_arg(context.article_output),
            source_ledger=as_posix_arg(context.source_ledger_output),
            image_ledger=as_posix_arg(context.image_ledger_output),
        ),
    )
    return RunConfigs(
        prompt=prompt,
        draft_prompt=draft_prompt,
        app_native_prompt=app_native_prompt,
        config=config,
        draft_config=draft_config,
        command=build_command(config),
        draft_command=build_command(draft_config),
        artifact_paths=artifact_paths,
        summary_config=summary_config,
        publish_config=publish_config,
    )
