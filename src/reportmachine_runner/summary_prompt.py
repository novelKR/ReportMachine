from __future__ import annotations

from .summary_contracts import SUMMARY_CLAIM_MAP_HEADER, SummaryRunConfig


def build_summary_prompt(
    config: SummaryRunConfig,
    *,
    article_status: str,
    hashes: dict[str, str] | None = None,
) -> str:
    hash_lines = []
    for key, value in sorted((hashes or {}).items()):
        hash_lines.append(f"- {key}: `{value}`")
    if not hash_lines:
        hash_lines.append("- Hashes are computed after article outputs exist.")

    return "\n".join(
        [
            "Use $summary-output.",
            f"Run id: `{config.run_id}`.",
            "Task: generate one A4 visual summary image as a derivative summary output.",
            f"Read the completed article from `{config.sources.article}`.",
            f"Read the structured article result from `{config.sources.article_result}`.",
            f"Read source ledger from `{config.sources.source_ledger}`.",
            f"Read image ledger from `{config.sources.image_ledger}`.",
            f"Article checklist_status: `{article_status}`.",
            "Input hashes:",
            *hash_lines,
            "",
            "Output contract:",
            f"- Write the A4 PNG to `{config.paths.summary_image}`.",
            f"- Write the claim map CSV to `{config.paths.summary_claim_map}`.",
            f"- Write prompt/audit notes only under `{config.paths.summary_dir}`.",
            f"- Return schema-valid JSON for `{config.schema}`; Codex CLI `-o` saves it to `{config.paths.summary_result}`.",
            "",
            "Image generation contract:",
            "- The image generation request must use Codex internal image generation tools only.",
            "- Do not call OpenAI API endpoints, File Search, vector stores, external image APIs, shell downloads, or browser automation as fallback.",
            "- If the Codex internal image generation tool is unavailable, cannot save the image, or cannot satisfy the contract, return `checklist_status` `fail`.",
            "- This A4 image is not an article output, not an evidence source, and not a replacement for `image_ledger.csv`.",
            "- AI-generated images remain prohibited in article outputs; this image may exist only under the summary output path.",
            "- Do not introduce new factual claims, quantitative values, source attributions, charts, or captions absent from `article.md` and ledgers.",
            "",
            "A4 style contract from TOML:",
            f"- image_generation_surface: `{config.profile.image_generation_surface}`",
            f"- paper_size: `{config.profile.paper_size}`",
            f"- orientation: `{config.profile.orientation}`",
            f"- language: `{config.profile.language}`",
            f"- style_name: `{config.profile.style_name}`",
            f"- max_visual_claims: `{config.profile.max_visual_claims}`",
            f"- require_source_footer: `{str(config.profile.require_source_footer).lower()}`",
            "style_prompt:",
            config.profile.style_prompt.strip(),
            "negative_prompt:",
            config.profile.negative_prompt.strip(),
            "",
            "Summary claim map requirements:",
            "- Preserve this CSV header exactly:",
            ",".join(SUMMARY_CLAIM_MAP_HEADER),
            "- Each visible statement, number, or source label in the image needs one row.",
            "- Use `article.md`, `source_ledger.csv`, or `image_ledger.csv` in `source_artifact`.",
            "",
            "If the article result is `fail`, do not generate an image; return a failure result.",
        ]
    )


def build_summary_command(config: SummaryRunConfig, prompt: str) -> list[str]:
    return [
        config.codex_bin,
        "exec",
        "-C",
        str(config.root),
        "--skip-git-repo-check",
        "--output-schema",
        config.schema,
        "-o",
        config.paths.summary_result,
        "-c",
        'project_root_markers=[".git",".article-root"]',
        "-c",
        f'web_search="{config.web_search}"',
        prompt,
    ]
