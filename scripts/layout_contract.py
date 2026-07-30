from __future__ import annotations

import csv
import json
from pathlib import Path


AGENT_ROLE_CARDS = {
    ".codex/agents/scope_guardian.toml": "prompts/roles/scope_guardian.md",
    ".codex/agents/research_planner.toml": "prompts/roles/research_planner.md",
    ".codex/agents/primary_source_scout.toml": "prompts/roles/primary_source_scout.md",
    ".codex/agents/counterevidence_scout.toml": "prompts/roles/counterevidence_scout.md",
    ".codex/agents/evidence_verifier.toml": "prompts/roles/evidence_verifier.md",
    ".codex/agents/evidence_integrator.toml": "prompts/roles/evidence_integrator.md",
    ".codex/agents/earnings_delta_analyst.toml": "prompts/roles/earnings_delta_analyst.md",
    ".codex/agents/thesis_adjudicator.toml": "prompts/roles/thesis_adjudicator.md",
    ".codex/agents/narrative_architect.toml": "prompts/roles/narrative_architect.md",
    ".codex/agents/article_writer.toml": "prompts/roles/article_writer.md",
    ".codex/agents/citation_logic_checker.toml": "prompts/roles/citation_logic_checker.md",
    ".codex/agents/editorial_red_team.toml": "prompts/roles/editorial_red_team.md",
    ".codex/agents/qa_reviewer.toml": "prompts/roles/qa_reviewer.md",
    ".codex/agents/compression_editor.toml": "prompts/roles/compression_editor.md",
    ".codex/agents/image_slide_auditor.toml": "prompts/roles/image_slide_auditor.md",
    ".codex/agents/finalizer.toml": "prompts/roles/finalizer.md",
    # Backward-compatible App-native aliases. The runner uses canonical names above.
    ".codex/agents/source_researcher.toml": "prompts/roles/primary_source_scout.md",
    ".codex/agents/citation_checker.toml": "prompts/roles/citation_logic_checker.md",
}


REQUIRED_FILES = [
    "AGENTS.md",
    "README.md",
    ".article-root",
    ".codex/config.toml",
    "reportmachine.example.toml",
    *AGENT_ROLE_CARDS,
    ".agents/skills/industry-article/SKILL.md",
    ".agents/skills/earnings-article/SKILL.md",
    ".agents/skills/article-qa/SKILL.md",
    ".agents/skills/article-intake/SKILL.md",
    ".agents/skills/reportmachine-usage/SKILL.md",
    ".agents/skills/summary-output/SKILL.md",
    "docs/USAGE.ko.md",
    "prompts/PROMPT.md",
    "prompts/FORMAT.md",
    "prompts/EARNINGS_PROMPT.md",
    "prompts/SOURCE_POLICY.md",
    "prompts/AUTHOR_PROFILE.md",
    "prompts/core/CORE_POLICY.md",
    "prompts/core/MODE_CONTRACT.md",
    "prompts/roles/scope_guardian.md",
    "prompts/roles/research_planner.md",
    "prompts/roles/primary_source_scout.md",
    "prompts/roles/counterevidence_scout.md",
    "prompts/roles/evidence_verifier.md",
    "prompts/roles/evidence_integrator.md",
    "prompts/roles/thesis_adjudicator.md",
    "prompts/roles/narrative_architect.md",
    "prompts/roles/article_writer.md",
    "prompts/roles/citation_logic_checker.md",
    "prompts/roles/editorial_red_team.md",
    "prompts/roles/qa_reviewer.md",
    "prompts/roles/compression_editor.md",
    "prompts/roles/image_slide_auditor.md",
    "prompts/roles/earnings_delta_analyst.md",
    "prompts/roles/finalizer.md",
    "schemas/article_result.schema.json",
    "schemas/preflight_result.schema.json",
    "schemas/claim_ledger.schema.json",
    "schemas/summary_result.schema.json",
    "schemas/earnings_delta.schema.json",
    "schemas/publish_result.schema.json",
    "schemas/intent_contract.schema.json",
    "schemas/research_question_tree.schema.json",
    "schemas/source_candidate.schema.json",
    "schemas/evidence_card.schema.json",
    "schemas/contradiction_ledger.schema.json",
    "schemas/evidence_gap_map.schema.json",
    "schemas/thesis_decision.schema.json",
    "schemas/narrative_plan.schema.json",
    "schemas/citation_logic_report.schema.json",
    "schemas/red_team_report.schema.json",
    "schemas/qa_report.schema.json",
    "schemas/compression_report.schema.json",
    "schemas/image_audit_report.schema.json",
    "schemas/revision_notes.schema.json",
    "schemas/paragraph_map.schema.json",
    "schemas/final_sync_report.schema.json",
    "schemas/agent_attempt.schema.json",
    "schemas/phase_provenance.schema.json",
    "inputs/topic.md",
    "inputs/sources.csv",
    "scripts/run_article.py",
    "scripts/run_summary.py",
    "scripts/run_publish.py",
    "scripts/package_source.py",
    "scripts/layout_contract.py",
    "src/reportmachine_runner/artifacts.py",
    "src/reportmachine_runner/audit.py",
    "src/reportmachine_runner/cli.py",
    "src/reportmachine_runner/config.py",
    "src/reportmachine_runner/codex_exec.py",
    "src/reportmachine_runner/intake.py",
    "src/reportmachine_runner/manifest.py",
    "src/reportmachine_runner/prompt.py",
    "src/reportmachine_runner/preflight.py",
    "src/reportmachine_runner/run_config_builder.py",
    "src/reportmachine_runner/run_context.py",
    "src/reportmachine_runner/run_outputs.py",
    "src/reportmachine_runner/source_fetch.py",
    "src/reportmachine_runner/source_ingest.py",
    "src/reportmachine_runner/source_text.py",
    "src/reportmachine_runner/source_ingest_paths.py",
    "src/reportmachine_runner/source_ledgers.py",
    "src/reportmachine_runner/pdf_processing.py",
    "src/reportmachine_runner/publish.py",
    "src/reportmachine_runner/publish_contracts.py",
    "src/reportmachine_runner/publish_markdown.py",
    "src/reportmachine_runner/publish_pdf.py",
    "src/reportmachine_runner/publish_workflow.py",
    "src/reportmachine_runner/summary.py",
    "src/reportmachine_runner/summary_contracts.py",
    "src/reportmachine_runner/summary_prompt.py",
    "src/reportmachine_runner/summary_validation.py",
    "src/reportmachine_runner/summary_workflow.py",
    "src/reportmachine_runner/workflow.py",
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

RELEASE_BLOCKED_TOP_LEVEL_DIR_PREFIXES = [
    "tmp",
]

RELEASE_BLOCKED_PATTERNS = [
    "*.spec",
    ".DS_Store",
    "__pycache__",
    "*.egg-info",
]

RELEASE_ARTIFACT_DIRS = [
    "outputs",
    "work",
    "logs",
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
    "src/reportmachine_runner/run_config_builder.py",
    "src/reportmachine_runner/run_context.py",
    "src/reportmachine_runner/run_outputs.py",
    "src/reportmachine_runner/workflow.py",
    "src/reportmachine_runner/source_fetch.py",
    "src/reportmachine_runner/source_ingest.py",
    "src/reportmachine_runner/source_ingest_paths.py",
    "src/reportmachine_runner/source_ledgers.py",
    "src/reportmachine_runner/pdf_processing.py",
    "src/reportmachine_runner/summary.py",
    "src/reportmachine_runner/summary_contracts.py",
    "src/reportmachine_runner/summary_prompt.py",
    "src/reportmachine_runner/summary_validation.py",
    "src/reportmachine_runner/summary_workflow.py",
    "src/reportmachine_runner/publish.py",
    "src/reportmachine_runner/publish_contracts.py",
    "src/reportmachine_runner/publish_markdown.py",
    "src/reportmachine_runner/publish_pdf.py",
    "src/reportmachine_runner/publish_workflow.py",
]

FORBIDDEN_PAID_API_SNIPPETS = [
    "/v1/responses",
    "/v1/files",
    "/v1/images",
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
    for path in root.iterdir():
        if not path.is_dir():
            continue
        if any(
            path.name.startswith(prefix)
            for prefix in RELEASE_BLOCKED_TOP_LEVEL_DIR_PREFIXES
        ):
            blocked_dir_parts.add(path.name)
            errors.append(f"release-blocked generated directory exists: {path.name}")

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


def verify_layout_contract(root: Path, *, release: bool = False) -> list[str]:
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
            "$reportmachine-usage",
            "docs/USAGE.ko.md",
            "--config reportmachine.example.toml --profile default --smoke-test",
            "$article-intake",
            "--prepare-only",
            "--summary-output",
            "--publish-pdf",
            "python3 scripts/run_publish.py",
            "App-native fallback",
            "External artifact roots are runner-only",
            "OpenAI API key is not required",
            "OpenAI File Search/vector-store upload is not used by the runner",
            "Codex internal image generation tools only",
            "python3 scripts/package_source.py",
            "--phase",
            "--json-events",
            "Git HEAD",
            "--allow-dirty",
        ],
    )

    usage_text = (root / "docs/USAGE.ko.md").read_text(encoding="utf-8")
    require_snippets(
        errors,
        "docs/USAGE.ko.md",
        usage_text,
        [
            "Git 없이 zip",
            "python3 scripts/package_source.py",
            "unzip ReportMachine-source-",
            "--allow-dirty",
            "preflight_result.json",
            "claim_ledger.json",
            "run_manifest.json",
            "--publish-pdf",
            "outputs/{run_id}/publish/article.pdf",
        ],
    )

    agents_text = (root / "AGENTS.md").read_text(encoding="utf-8")
    require_snippets(
        errors,
        "AGENTS.md",
        agents_text,
        [
            "$reportmachine-usage",
            "docs/USAGE.ko.md",
            "$article-intake",
            "$summary-output",
            "App-native fallback exception",
            "schemas/article_result.schema.json",
            "schemas/preflight_result.schema.json",
            "schemas/claim_ledger.schema.json",
            "schemas/summary_result.schema.json",
            "schemas/publish_result.schema.json",
        ],
    )

    codex_exec_text = (root / "src/reportmachine_runner/codex_exec.py").read_text(encoding="utf-8")
    if "--full-auto" in codex_exec_text:
        errors.append("runner command must not use --full-auto")
    check_paid_api_upload_surface(root, errors)

    source_ledgers_text = (root / "src/reportmachine_runner/source_ledgers.py").read_text(encoding="utf-8")
    if '"openai_file_search": "disabled"' not in source_ledgers_text:
        errors.append("source ingest manifest must keep openai_file_search disabled")

    summary_prompt_text = (root / "src/reportmachine_runner/summary_prompt.py").read_text(encoding="utf-8")
    summary_contract_text = (root / "src/reportmachine_runner/summary_contracts.py").read_text(encoding="utf-8")
    require_snippets(
        errors,
        "summary output modules",
        summary_prompt_text + "\n" + summary_contract_text,
        [
            "Use $summary-output.",
            "Codex internal image generation tools only",
            "Do not call OpenAI API endpoints, File Search, vector stores",
            "summary_a4.png",
            "summary_claim_map.csv",
        ],
    )

    publish_pdf_text = (root / "src/reportmachine_runner/publish_pdf.py").read_text(encoding="utf-8")
    publish_contract_text = (
        root / "src/reportmachine_runner/publish_contracts.py"
    ).read_text(encoding="utf-8")
    require_snippets(
        errors,
        "publish output modules",
        publish_pdf_text + "\n" + publish_contract_text,
        [
            "local_deterministic_renderer",
            "article.pdf",
            "publish_figure_map.csv",
        ],
    )
    # CID fonts render Hangul as a blank page in poppler based viewers, so the
    # renderer must only ever embed TrueType faces.
    if "UnicodeCIDFont(" in publish_pdf_text:
        errors.append(
            "publish renderer must not register CID fonts; embed a TrueType Hangul face instead"
        )
    if "TTFont(" not in publish_pdf_text:
        errors.append("publish renderer must embed a TrueType font for Hangul output")

    prompt_text = (root / "src/reportmachine_runner/prompt.py").read_text(encoding="utf-8")
    require_snippets(
        errors,
        "src/reportmachine_runner/prompt.py",
        prompt_text,
        [
            "OpenAI API/File Search/vector store uploads are disabled",
            "openai_file_search = disabled",
            "Final claim_ledger.json",
        ],
    )

    source_policy_text = (root / "prompts/SOURCE_POLICY.md").read_text(encoding="utf-8")
    require_snippets(
        errors,
        "prompts/SOURCE_POLICY.md",
        source_policy_text,
        [
            "AI-generated images are prohibited in article outputs",
            "AI-generated A4 summary images are allowed only as derivative summary outputs",
            "Codex internal image generation tools only",
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

    for agent_path, role_card in AGENT_ROLE_CARDS.items():
        agent_text = (root / agent_path).read_text(encoding="utf-8")
        require_snippets(
            errors,
            agent_path,
            agent_text,
            ["src/reportmachine_runner/roles.py", role_card],
        )

    earnings_delta_text = (root / "prompts/roles/earnings_delta_analyst.md").read_text(
        encoding="utf-8"
    )
    require_snippets(
        errors,
        "prompts/roles/earnings_delta_analyst.md",
        earnings_delta_text,
        [
            "Explore at least two disappeared prior-quarter highlights",
            "If fewer than two exist",
        ],
    )

    if release:
        check_release_cleanliness(root, errors)

    return errors
