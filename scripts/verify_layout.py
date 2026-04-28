#!/usr/bin/env python3

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path


REQUIRED_FILES = [
    "AGENTS.md",
    "README.md",
    ".article-root",
    ".codex/config.toml",
    "reportmachine.example.toml",
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
    ".agents/skills/article-intake/SKILL.md",
    "prompts/PROMPT.md",
    "prompts/FORMAT.md",
    "prompts/EARNINGS_PROMPT.md",
    "prompts/SOURCE_POLICY.md",
    "prompts/AUTHOR_PROFILE.md",
    "schemas/article_result.schema.json",
    "inputs/topic.md",
    "inputs/sources.csv",
    "scripts/run_article.py",
    "src/reportmachine_runner/artifacts.py",
    "src/reportmachine_runner/cli.py",
    "src/reportmachine_runner/config.py",
    "src/reportmachine_runner/codex_exec.py",
    "src/reportmachine_runner/intake.py",
    "src/reportmachine_runner/prompt.py",
    "src/reportmachine_runner/source_ingest.py",
]

EXPECTED_SOURCES_HEADER = [
    "source_id",
    "article_type",
    "company",
    "period_role",
    "period",
    "doc_kind",
    "issuer",
    "date",
    "title",
    "url",
    "source_tier",
    "required",
    "local_path",
    "notes",
]

RELEASE_BLOCKED_DIRS = [
    ".tmp",
    ".venv",
    "__MACOSX",
    "build",
    "dist",
]

RELEASE_BLOCKED_PATTERNS = [
    "*.spec",
    ".DS_Store",
    "__pycache__",
]

RELEASE_ARTIFACT_DIRS = [
    "outputs",
    "work",
]

RELEASE_ALLOWED_ARTIFACT_NAMES = {
    ".gitkeep",
}

API_UPLOAD_GUARDED_FILES = [
    "src/reportmachine_runner/cli.py",
    "src/reportmachine_runner/codex_exec.py",
    "src/reportmachine_runner/config.py",
    "src/reportmachine_runner/prompt.py",
    "src/reportmachine_runner/intake.py",
    "src/reportmachine_runner/source_ingest.py",
]

FORBIDDEN_PAID_API_SNIPPETS = [
    "/v1/responses",
    "/v1/files",
    "vector_store",
    "vector_stores",
    "OpenAI(",
    "AsyncOpenAI(",
    "import openai",
    "from openai import",
]


def is_top_level_release_excluded_path(
    relative_parts: tuple[str, ...],
    blocked_dir_parts: set[str],
) -> bool:
    return bool(relative_parts) and (
        relative_parts[0] == ".git" or relative_parts[0] in blocked_dir_parts
    )


def require_snippets(errors: list[str], rel: str, text: str, snippets: list[str]) -> None:
    for snippet in snippets:
        if snippet not in text:
            errors.append(f"{rel} missing contract snippet: {snippet}")


def check_paid_api_upload_surface(root: Path, errors: list[str]) -> None:
    for rel in API_UPLOAD_GUARDED_FILES:
        text = (root / rel).read_text(encoding="utf-8")
        for snippet in FORBIDDEN_PAID_API_SNIPPETS:
            if snippet in text:
                errors.append(
                    f"{rel} must not use paid OpenAI API/File Search upload surface: {snippet}"
                )


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


def check_release_cleanliness(root: Path, errors: list[str]) -> None:
    blocked_dir_parts = set(RELEASE_BLOCKED_DIRS)
    reported_artifacts: set[str] = set()
    for rel in RELEASE_BLOCKED_DIRS:
        if (root / rel).exists():
            errors.append(f"release-blocked generated directory exists: {rel}")

    for pattern in RELEASE_BLOCKED_PATTERNS:
        for path in root.rglob(pattern):
            relative_parts = path.relative_to(root).parts
            if is_top_level_release_excluded_path(relative_parts, blocked_dir_parts):
                continue
            rel = path.relative_to(root).as_posix()
            reported_artifacts.add(rel)
            errors.append(f"release-blocked generated artifact exists: {rel}")

    for artifact_dir in RELEASE_ARTIFACT_DIRS:
        base = root / artifact_dir
        if not base.exists():
            continue
        for path in base.rglob("*"):
            if not path.is_file() or path.name in RELEASE_ALLOWED_ARTIFACT_NAMES:
                continue
            relative_parts = path.relative_to(root).parts
            if is_top_level_release_excluded_path(relative_parts, blocked_dir_parts):
                continue
            rel = path.relative_to(root).as_posix()
            if rel in reported_artifacts:
                continue
            reported_artifacts.add(rel)
            if artifact_dir == "outputs" and path.parent == base and path.suffix == ".json":
                errors.append(f"release-blocked root output JSON exists: {rel}")
            else:
                errors.append(f"release-blocked generated artifact exists: {rel}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify the ReportMachine repository layout.")
    parser.add_argument(
        "--release",
        action="store_true",
        help="Also fail on generated artifacts that must not be included in release archives.",
    )
    args = parser.parse_args()

    root = Path(__file__).resolve().parents[1]
    errors: list[str] = []

    for rel in REQUIRED_FILES:
        if not (root / rel).exists():
            errors.append(f"missing required file: {rel}")

    config_text = (root / ".codex/config.toml").read_text(encoding="utf-8")
    for required in [
        'project_root_markers = [".git", ".article-root"]',
        'web_search = "cached"',
        "allow_login_shell = false",
        "view_image = true",
        "network_access = false",
        'sandbox = "elevated"',
    ]:
        if required not in config_text:
            errors.append(f"missing config setting: {required}")
    if "codex_hooks = true" in config_text:
        errors.append("codex_hooks must stay disabled until real hooks are configured")

    gitignore_text = (root / ".gitignore").read_text(encoding="utf-8")
    if "reportmachine.local.toml" not in gitignore_text:
        errors.append(".gitignore must ignore reportmachine.local.toml")

    pyproject_text = (root / "pyproject.toml").read_text(encoding="utf-8")
    if 'tomli>=2; python_version < "3.11"' not in pyproject_text:
        errors.append("pyproject.toml must include the Python 3.10 TOML parser dependency")

    readme_text = (root / "README.md").read_text(encoding="utf-8")
    require_snippets(
        errors,
        "README.md",
        readme_text,
        [
            "--config reportmachine.example.toml --profile default --smoke-test",
            "$article-intake",
            "--prepare-only",
            "App-native fallback",
            "External artifact roots are runner-only",
            "OpenAI API key is not required",
            "OpenAI File Search/vector-store upload is not used by the runner",
        ],
    )

    agents_text = (root / "AGENTS.md").read_text(encoding="utf-8")
    require_snippets(
        errors,
        "AGENTS.md",
        agents_text,
        [
            "$article-intake",
            "App-native fallback exception",
            "schemas/article_result.schema.json",
        ],
    )

    codex_exec_text = (root / "src/reportmachine_runner/codex_exec.py").read_text(encoding="utf-8")
    if "--full-auto" in codex_exec_text:
        errors.append("runner command must not use --full-auto")
    check_paid_api_upload_surface(root, errors)

    source_ingest_text = (root / "src/reportmachine_runner/source_ingest.py").read_text(encoding="utf-8")
    if '"openai_file_search": "disabled"' not in source_ingest_text:
        errors.append("source ingest manifest must keep openai_file_search disabled")

    prompt_text = (root / "src/reportmachine_runner/prompt.py").read_text(encoding="utf-8")
    require_snippets(
        errors,
        "src/reportmachine_runner/prompt.py",
        prompt_text,
        [
            "OpenAI API/File Search/vector store uploads are disabled",
            "openai_file_search = disabled",
        ],
    )

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

    if args.release:
        check_release_cleanliness(root, errors)

    if errors:
        print("Layout verification failed:")
        for error in errors:
            print(f" - {error}")
        return 1

    print("Layout OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
