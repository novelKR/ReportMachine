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

    if errors:
        print("Layout verification failed:")
        for error in errors:
            print(f" - {error}")
        return 1

    print("Layout OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
