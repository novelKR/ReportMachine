from __future__ import annotations


def build_prompt(
    *,
    mode: str,
    topic_path: str,
    sources_path: str,
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
            f"Read topic input from `{topic_path}`.",
            f"Read source input from `{sources_path}`.",
            "Use the repo-local AGENTS.md routing contract.",
            "Spawn the required ReportMachine sub agents and wait for their results.",
            "Sub agents must produce intermediate analysis only; the parent agent integrates the final result.",
            f"Write the final article to `{article_output}`.",
            f"Write the source ledger to `{source_ledger_output}`.",
            f"Write the image ledger to `{image_ledger_output}`.",
            f"Return final JSON matching the configured output schema and consistent with `{result_output}`.",
            "If required materials are missing, do not invent facts; return checklist_status `fail` with critical_warnings.",
        ]
    )
