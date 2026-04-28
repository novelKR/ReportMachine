from __future__ import annotations


def build_prompt(
    *,
    mode: str,
    run_id: str,
    topic_path: str,
    sources_path: str,
    work_run_dir: str,
    draft_output: str,
    image_notes_dir: str,
    image_ledger_candidate: str,
    source_ingest_manifest: str,
    source_candidate_ledger: str,
    pdf_index: str,
    rendered_images_ledger: str,
    source_ingest_enabled: bool,
    article_output: str,
    source_ledger_output: str,
    image_ledger_output: str,
    result_output: str,
) -> str:
    skill = "$earnings-article" if mode == "earnings" else "$industry-article"
    article_type = "published earnings or IR review" if mode == "earnings" else "general industry analysis"
    source_preprocessing_lines = [
        f"Source preprocessing is `{'enabled' if source_ingest_enabled else 'disabled'}`.",
    ]
    if source_ingest_enabled:
        source_preprocessing_lines.extend(
            [
                f"When source preprocessing is enabled, first read `{source_ingest_manifest}`.",
                f"Use preprocessed source candidates from `{source_candidate_ledger}`.",
                f"Use PDF search chunks from `{pdf_index}` and rendered PDF image inventory from `{rendered_images_ledger}`.",
            ]
        )
    else:
        source_preprocessing_lines.extend(
            [
                f"Use `{sources_path}` directly as the source inventory for this run.",
                "No preprocessed source ledger, PDF search chunks, or rendered PDF image inventory is expected for this run.",
            ]
        )

    return "\n".join(
        [
            f"Use {skill}.",
            f"Task type: {article_type}.",
            f"Run id: `{run_id}`.",
            f"Read topic input from `{topic_path}`.",
            f"Read source input from `{sources_path}`.",
            *source_preprocessing_lines,
            "OpenAI API/File Search/vector store uploads are disabled for this runner; keep `openai_file_search = disabled`.",
            "Treat Codex web search as candidate discovery only; final claims must trace to repo-local sources and ledgers.",
            "Use the repo-local AGENTS.md routing contract.",
            "Spawn the required ReportMachine sub agents and wait for their results.",
            "Read-only sub agents must return intermediate analysis as messages only; they must not write files.",
            f"Keep run-scoped intermediate artifacts under `{work_run_dir}`.",
            f"If article_writer writes a file, it may write only `{draft_output}`.",
            f"If image_slide_auditor writes files, it may write only under `{image_notes_dir}` and `{image_ledger_candidate}`.",
            "If rendered PDF images are available, image_slide_auditor must inspect the actual image before proposing captions.",
            "Do not use AI-generated images in article outputs.",
            "The parent agent integrates the final result.",
            f"Write the final article to `{article_output}`.",
            f"Write the source ledger to `{source_ledger_output}`.",
            f"Write the image ledger to `{image_ledger_output}`.",
            "Final source_ledger.csv must preserve source_id, url, local_path, sha256, ingest_status, allowed_domain, and blocked_reason where available.",
            "Final image_ledger.csv must preserve rendered image path, source PDF, page or slide, source hash, inspected status, caption basis, keep/drop reason, and caption candidate where available.",
            f"Return final JSON matching the configured output schema. Do not write `{result_output}` directly; Codex CLI `-o` saves the final assistant message there.",
            "If required materials are missing, do not invent facts; return checklist_status `fail` with critical_warnings.",
            f"When checklist_status is `fail` because required materials are missing, write a non-publishable failure note to `{article_output}` instead of a publishable article.",
        ]
    )
