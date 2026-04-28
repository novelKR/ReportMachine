#!/usr/bin/env python3

from __future__ import annotations

import csv
import json
from pathlib import Path


REQUIRED_FILES = [
    "AGENTS.md",
    "README.md",
    ".article-root",
    ".codex/config.toml",
    ".codex/agents/source_researcher.toml",
    ".codex/agents/earnings_delta_analyst.toml",
    ".codex/agents/image_slide_auditor.toml",
    ".codex/agents/narrative_architect.toml",
    ".codex/agents/citation_checker.toml",
    ".codex/agents/qa_reviewer.toml",
    ".codex/agents/article_writer.toml",
    ".agents/skills/industry-article/SKILL.md",
    ".agents/skills/earnings-article/SKILL.md",
    ".agents/skills/article-qa/SKILL.md",
    "prompts/PROMPT.md",
    "prompts/FORMAT.md",
    "prompts/EARNINGS_PROMPT.md",
    "prompts/SOURCE_POLICY.md",
    "prompts/AUTHOR_PROFILE.md",
    "schemas/article_result.schema.json",
    "inputs/topic.md",
    "inputs/sources.csv",
    "scripts/run_article.py",
    "src/reportmachine_runner/cli.py",
    "src/reportmachine_runner/codex_exec.py",
    "src/reportmachine_runner/prompt.py",
]

EXPECTED_SOURCES_HEADER = [
    "source_id",
    "article_type",
    "company",
    "period_role",
    "doc_kind",
    "issuer",
    "date",
    "title",
    "url",
    "required",
    "notes",
]


def require_snippets(errors: list[str], rel: str, text: str, snippets: list[str]) -> None:
    for snippet in snippets:
        if snippet not in text:
            errors.append(f"{rel} missing contract snippet: {snippet}")


def schema_has_earnings_scorecard_rule(
    schema: dict,
    *,
    checklist_statuses: set[str],
    scorecard_type: str,
) -> bool:
    for rule in schema.get("allOf", []):
        condition = rule.get("if", {})
        properties = condition.get("properties", {})
        if properties.get("article_type", {}).get("const") != "earnings":
            continue

        status_condition = properties.get("checklist_status", {})
        if "const" in status_condition:
            statuses = {status_condition["const"]}
        else:
            statuses = set(status_condition.get("enum", []))
        if statuses != checklist_statuses:
            continue

        scorecard = rule.get("then", {}).get("properties", {}).get("scorecard", {})
        if scorecard.get("type") == scorecard_type:
            return True
    return False


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    errors: list[str] = []

    for rel in REQUIRED_FILES:
        if not (root / rel).exists():
            errors.append(f"missing required file: {rel}")

    config_text = (root / ".codex/config.toml").read_text(encoding="utf-8")
    for required in [
        'project_root_markers = [".git", ".article-root"]',
        'web_search = "cached"',
        "network_access = false",
    ]:
        if required not in config_text:
            errors.append(f"missing config setting: {required}")
    if "codex_hooks = true" in config_text:
        errors.append("codex_hooks must stay disabled until real hooks are configured")

    with (root / "inputs/sources.csv").open(newline="", encoding="utf-8") as handle:
        reader = csv.reader(handle)
        header = next(reader, [])
    if header != EXPECTED_SOURCES_HEADER:
        errors.append("inputs/sources.csv header does not match the ReportMachine source contract")

    with (root / "schemas/article_result.schema.json").open(encoding="utf-8") as handle:
        schema = json.load(handle)
    if "allOf" not in schema:
        errors.append("article_result schema must define allOf mode-specific conditions")
    elif not schema_has_earnings_scorecard_rule(
        schema,
        checklist_statuses={"pass", "pass_with_warnings"},
        scorecard_type="object",
    ):
        errors.append("article_result schema must require earnings scorecards for pass and pass_with_warnings")
    elif not schema_has_earnings_scorecard_rule(
        schema,
        checklist_statuses={"fail"},
        scorecard_type="null",
    ):
        errors.append("article_result schema must allow null scorecards for failed earnings runs")

    earnings_prompt_text = (root / "prompts/EARNINGS_PROMPT.md").read_text(encoding="utf-8")
    require_snippets(
        errors,
        "prompts/EARNINGS_PROMPT.md",
        earnings_prompt_text,
        [
            'checklist_status: "fail"',
            "non-publishable failure note",
            "At least two disappeared prior-quarter highlights",
            "fewer than two disappeared highlights",
            "wording/order/unit/risk-language checks",
        ],
    )

    format_text = (root / "prompts/FORMAT.md").read_text(encoding="utf-8")
    require_snippets(
        errors,
        "prompts/FORMAT.md",
        format_text,
        [
            "missing required release/deck materials are a fail/research gate",
            "At least two disappeared prior-quarter highlights",
            "fewer than two were explicitly stated with wording/order/unit/risk-language checks",
        ],
    )

    earnings_delta_text = (root / ".codex/agents/earnings_delta_analyst.toml").read_text(encoding="utf-8")
    require_snippets(
        errors,
        ".codex/agents/earnings_delta_analyst.toml",
        earnings_delta_text,
        [
            "Explore at least two disappeared prior-quarter highlights",
            "If fewer than two disappeared highlights are found",
        ],
    )

    if errors:
        print("Layout verification failed:")
        for error in errors:
            print(f" - {error}")
        return 1

    print("Layout OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
