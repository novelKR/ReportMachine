"""Role registry for the runner-driven phase pipeline.

Each role maps to a role card under `prompts/roles/`, the contract files compiled into its
effective prompt, its model placement, and its structured-output schema.

Compile matrix: scouts and verifiers get core policy, mode contract, source policy, and their
role card only. Writer, narrative, and finalizer roles additionally get the article prompt,
format floor, and author profile. The QA reviewer gets the full set.
"""

from __future__ import annotations

from dataclasses import dataclass, field


CORE_POLICY = "prompts/core/CORE_POLICY.md"
MODE_CONTRACT = "prompts/core/MODE_CONTRACT.md"
ARTICLE_PROMPT = "prompts/PROMPT.md"
FORMAT_FLOOR = "prompts/FORMAT.md"
SOURCE_POLICY = "prompts/SOURCE_POLICY.md"
AUTHOR_PROFILE = "prompts/AUTHOR_PROFILE.md"
EARNINGS_PROMPT = "prompts/EARNINGS_PROMPT.md"
PREVIEW_PROMPT = "prompts/PREVIEW_PROMPT.md"

RESEARCH_CONTRACTS = (CORE_POLICY, MODE_CONTRACT, SOURCE_POLICY)
WRITER_CONTRACTS = (
    CORE_POLICY,
    MODE_CONTRACT,
    ARTICLE_PROMPT,
    FORMAT_FLOOR,
    SOURCE_POLICY,
    AUTHOR_PROFILE,
)

FAST_MODEL = "gpt-5.4-mini"
FAST_EFFORT = "xhigh"
DEEP_MODEL = "gpt-5.5"
DEEP_EFFORT = "high"


@dataclass(frozen=True)
class RoleSpec:
    """Static contract for one runner-invoked agent role."""

    name: str
    role_card: str
    contracts: tuple[str, ...]
    model: str
    reasoning_effort: str
    output_schema: str | None = None
    structured_output: bool = False
    earnings_only: bool = False
    extra_contracts_for_earnings: tuple[str, ...] = field(default_factory=tuple)


ROLES: dict[str, RoleSpec] = {
    "scope_guardian": RoleSpec(
        name="scope_guardian",
        role_card="prompts/roles/scope_guardian.md",
        contracts=(CORE_POLICY, MODE_CONTRACT),
        model=DEEP_MODEL,
        reasoning_effort=DEEP_EFFORT,
        output_schema="schemas/intent_contract.schema.json",
    ),
    "research_planner": RoleSpec(
        name="research_planner",
        role_card="prompts/roles/research_planner.md",
        contracts=RESEARCH_CONTRACTS,
        model=DEEP_MODEL,
        reasoning_effort=DEEP_EFFORT,
        output_schema="schemas/research_question_tree.schema.json",
    ),
    "primary_source_scout": RoleSpec(
        name="primary_source_scout",
        role_card="prompts/roles/primary_source_scout.md",
        contracts=RESEARCH_CONTRACTS,
        model=FAST_MODEL,
        reasoning_effort=FAST_EFFORT,
    ),
    "counterevidence_scout": RoleSpec(
        name="counterevidence_scout",
        role_card="prompts/roles/counterevidence_scout.md",
        contracts=RESEARCH_CONTRACTS,
        model=FAST_MODEL,
        reasoning_effort=FAST_EFFORT,
    ),
    "evidence_verifier": RoleSpec(
        name="evidence_verifier",
        role_card="prompts/roles/evidence_verifier.md",
        contracts=RESEARCH_CONTRACTS,
        model=FAST_MODEL,
        reasoning_effort=FAST_EFFORT,
    ),
    "evidence_integrator": RoleSpec(
        name="evidence_integrator",
        role_card="prompts/roles/evidence_integrator.md",
        contracts=RESEARCH_CONTRACTS,
        model=DEEP_MODEL,
        reasoning_effort=DEEP_EFFORT,
    ),
    "earnings_delta_analyst": RoleSpec(
        name="earnings_delta_analyst",
        role_card="prompts/roles/earnings_delta_analyst.md",
        contracts=(CORE_POLICY, MODE_CONTRACT, SOURCE_POLICY, EARNINGS_PROMPT),
        model=DEEP_MODEL,
        reasoning_effort=DEEP_EFFORT,
        output_schema="schemas/earnings_delta.schema.json",
        earnings_only=True,
    ),
    "thesis_adjudicator": RoleSpec(
        name="thesis_adjudicator",
        role_card="prompts/roles/thesis_adjudicator.md",
        contracts=RESEARCH_CONTRACTS,
        model=DEEP_MODEL,
        reasoning_effort=DEEP_EFFORT,
        output_schema="schemas/thesis_decision.schema.json",
    ),
    "narrative_architect": RoleSpec(
        name="narrative_architect",
        role_card="prompts/roles/narrative_architect.md",
        contracts=WRITER_CONTRACTS,
        model=DEEP_MODEL,
        reasoning_effort=DEEP_EFFORT,
        output_schema="schemas/narrative_plan.schema.json",
    ),
    "article_writer": RoleSpec(
        name="article_writer",
        role_card="prompts/roles/article_writer.md",
        contracts=WRITER_CONTRACTS,
        model=DEEP_MODEL,
        reasoning_effort=DEEP_EFFORT,
        extra_contracts_for_earnings=(EARNINGS_PROMPT,),
    ),
    "citation_logic_checker": RoleSpec(
        name="citation_logic_checker",
        role_card="prompts/roles/citation_logic_checker.md",
        contracts=RESEARCH_CONTRACTS,
        model=DEEP_MODEL,
        reasoning_effort=DEEP_EFFORT,
        output_schema="schemas/citation_logic_report.schema.json",
    ),
    "editorial_red_team": RoleSpec(
        name="editorial_red_team",
        role_card="prompts/roles/editorial_red_team.md",
        contracts=RESEARCH_CONTRACTS,
        model=DEEP_MODEL,
        reasoning_effort=DEEP_EFFORT,
        output_schema="schemas/red_team_report.schema.json",
    ),
    "qa_reviewer": RoleSpec(
        name="qa_reviewer",
        role_card="prompts/roles/qa_reviewer.md",
        contracts=WRITER_CONTRACTS,
        model=DEEP_MODEL,
        reasoning_effort=DEEP_EFFORT,
        output_schema="schemas/qa_report.schema.json",
        extra_contracts_for_earnings=(EARNINGS_PROMPT,),
    ),
    "compression_editor": RoleSpec(
        name="compression_editor",
        role_card="prompts/roles/compression_editor.md",
        contracts=(CORE_POLICY, MODE_CONTRACT, FORMAT_FLOOR),
        model=FAST_MODEL,
        reasoning_effort=FAST_EFFORT,
        output_schema="schemas/compression_report.schema.json",
    ),
    "image_slide_auditor": RoleSpec(
        name="image_slide_auditor",
        role_card="prompts/roles/image_slide_auditor.md",
        contracts=(CORE_POLICY, MODE_CONTRACT, SOURCE_POLICY),
        model=FAST_MODEL,
        reasoning_effort=FAST_EFFORT,
        output_schema="schemas/image_audit_report.schema.json",
    ),
    "finalizer": RoleSpec(
        name="finalizer",
        role_card="prompts/roles/finalizer.md",
        contracts=WRITER_CONTRACTS,
        model=DEEP_MODEL,
        reasoning_effort=DEEP_EFFORT,
        output_schema="schemas/article_result.schema.json",
        structured_output=True,
        extra_contracts_for_earnings=(EARNINGS_PROMPT,),
    ),
}

ROLE_CARDS = tuple(sorted(spec.role_card for spec in ROLES.values()))
CORE_PROMPT_FILES = (CORE_POLICY, MODE_CONTRACT)


def role_spec(name: str) -> RoleSpec:
    try:
        return ROLES[name]
    except KeyError as exc:  # pragma: no cover - guarded by the phase table
        raise KeyError(f"unknown role: {name}") from exc


def contracts_for(spec: RoleSpec, mode: str) -> tuple[str, ...]:
    """Resolve the contract files compiled into a role's effective prompt."""

    contracts = list(spec.contracts)
    if mode == "earnings":
        for extra in spec.extra_contracts_for_earnings:
            if extra not in contracts:
                contracts.append(extra)
    if mode == "preview" and ARTICLE_PROMPT in contracts and PREVIEW_PROMPT not in contracts:
        contracts.append(PREVIEW_PROMPT)
    return tuple(contracts)
