"""Build the RUN CONTRACT block injected into every compiled role prompt.

Roles receive the run's identity, approved scope, base date, and the exact artifact paths
they may read and write. Keeping this in one place means a path change cannot silently
desynchronize the prompts from the phase table.
"""

from __future__ import annotations

from typing import Any

from .paths import as_posix_arg
from .phase_provenance import provenance_dir
from .run_context import RunContext


def build_run_contract(context: RunContext, intent: dict[str, Any] | None = None) -> dict[str, Any]:
    artifacts = context.run_artifacts
    intent = intent or {}
    analysis_modes = intent.get("analysis_modes") or list(context.analysis_modes)
    depth = intent.get("depth") or context.depth
    contract: dict[str, Any] = {
        "run_id": context.run_id,
        "article_type": context.mode,
        "analysis_modes": analysis_modes,
        "depth": depth,
        "base_date": intent.get("base_date"),
        "primary_question": intent.get("primary_question"),
        "must_include": intent.get("must_include") or [],
        "must_not_include": intent.get("must_not_include") or [],
        "topic_path": as_posix_arg(context.topic_path),
        "sources_path": as_posix_arg(context.sources_path),
        "work_run_dir": as_posix_arg(context.work_run_dir),
        "outputs_dir": artifacts.outputs_dir,
        "original_request": artifacts.original_request,
        "intent_contract": artifacts.intent_contract,
        "question_tree": artifacts.question_tree,
        "lanes_dir": artifacts.lanes_dir,
        "primary_candidates": artifacts.primary_candidates,
        "counter_candidates": artifacts.counter_candidates,
        "extracted_text_dir": artifacts.extracted_text_dir,
        "evidence_cards": artifacts.evidence_cards,
        "contradiction_ledger": artifacts.contradiction_ledger,
        "evidence_gap_map": artifacts.evidence_gap_map,
        "thesis_decision": artifacts.thesis_decision,
        "narrative_plan": artifacts.narrative_plan,
        "draft_article": artifacts.draft_article,
        "reviews_dir": f"{artifacts.work_run_dir}/reviews",
        "image_audit_report": artifacts.image_audit_report,
        "article": artifacts.article,
        "research_appendix": artifacts.research_appendix,
        "paragraph_map": artifacts.paragraph_map,
        "claim_ledger": artifacts.claim_ledger,
        "source_ledger": artifacts.source_ledger,
        "image_ledger": artifacts.image_ledger,
        "article_result": artifacts.article_result,
        "final_sync_report": artifacts.final_sync_report,
        "run_manifest": as_posix_arg(context.run_manifest_output),
        "phase_provenance_dir": provenance_dir(artifacts),
        "earnings_delta_matrix": as_posix_arg(context.earnings_delta_matrix_output),
        "earnings_delta_matrix_md": as_posix_arg(context.earnings_delta_matrix_md),
        "source_ingest_enabled": context.source_ingest_enabled,
        # These run-local canonical paths remain meaningful on --no-source-ingest resume.
        "rendered_images_ledger": as_posix_arg(
            context.source_ingest_paths.rendered_images_ledger
        ),
        "max_image_inputs": context.max_image_inputs,
        "openai_file_search": "disabled",
        "web_search_policy": (
            "Codex web search is candidate discovery only; final claims must trace to repo-local sources."
        ),
    }
    if context.source_ingest_enabled:
        # Only advertise preprocessing artifacts when preprocessing actually ran, so a
        # disabled run cannot point a role at an inventory that does not exist.
        contract.update(
            {
                "source_ingest_manifest": as_posix_arg(context.source_ingest_paths.manifest),
                "source_candidate_ledger": as_posix_arg(
                    context.source_ingest_paths.source_candidate_ledger
                ),
                "pdf_index": as_posix_arg(context.source_ingest_paths.pdf_index),
            }
        )
    return {key: value for key, value in contract.items() if value is not None}
