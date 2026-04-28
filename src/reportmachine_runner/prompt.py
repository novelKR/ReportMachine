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
    article_output: str,
    source_ledger_output: str,
    image_ledger_output: str,
    result_output: str,
) -> str:
    skill = "$earnings-article" if mode == "earnings" else "$industry-article"
    article_type = "published earnings or IR review" if mode == "earnings" else "general industry analysis"

    return "\n".join(
        [
            f"Use {skill}.",
            f"Task type: {article_type}.",
            f"Run id: `{run_id}`.",
            f"Read topic input from `{topic_path}`.",
            f"Read source input from `{sources_path}`.",
            "Use the repo-local AGENTS.md routing contract.",
            "Spawn the required ReportMachine sub agents and wait for their results.",
            "Read-only sub agents must return intermediate analysis as messages only; they must not write files.",
            f"Keep run-scoped intermediate artifacts under `{work_run_dir}`.",
            f"If article_writer writes a file, it may write only `{draft_output}`.",
            f"If image_slide_auditor writes files, it may write only under `{image_notes_dir}` and `{image_ledger_candidate}`.",
            "The parent agent integrates the final result.",
            f"Write the final article to `{article_output}`.",
            f"Write the source ledger to `{source_ledger_output}`.",
            f"Write the image ledger to `{image_ledger_output}`.",
            f"Return final JSON matching the configured output schema. Do not write `{result_output}` directly; Codex CLI `-o` saves the final assistant message there.",
            "If required materials are missing, do not invent facts; return checklist_status `fail` with critical_warnings.",
        ]
    )
