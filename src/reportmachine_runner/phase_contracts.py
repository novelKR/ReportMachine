"""Phase and artifact contracts for the runner-driven pipeline.

This module is the single source of truth for phase ordering, per-phase artifact paths,
and the schema each artifact must satisfy. `phases.py` executes it; the audit and manifest
layers read it so that gate definitions never drift between modules.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


PHASES = (
    "prepare",
    "scope",
    "research-plan",
    "research",
    "evidence",
    "narrative",
    "draft",
    "review",
    "revise",
    "finalize",
    "audit",
)

PHASE_CHOICES = (*PHASES, "all")

# Draft preserves the established App-native fallback: without jsonschema it performs
# syntax and semantic checks only. Every other non-prepare phase validates schemas.
SCHEMA_DEPENDENT_PHASES = frozenset(set(PHASE_CHOICES) - {"prepare", "draft"})

DEPTH_LANE_LIMITS = {
    "brief": 2,
    "standard": 4,
    "deep": 5,
}

ANALYSIS_MODES = (
    "industry_structure",
    "company_fundamental",
    "market_reaction",
    "event_analysis",
    "earnings_quality",
    "technology_validation",
)

DEPTH_CHOICES = ("brief", "standard", "deep")

SOURCE_STATUS_UNVERIFIED = ("blocked", "remote_unverified", "stale")
SOURCE_STATUS_HUMAN_REVIEW = ("remote_manual_verified",)
SOURCE_STATUS_VERIFIED = ("downloaded", "downloaded_hashed", "registered_local")


@dataclass(frozen=True)
class RunArtifacts:
    """Repo-relative paths for every pipeline artifact of one run."""

    work_run_dir: str
    outputs_dir: str

    # intake / scope
    original_request: str
    intent_contract: str

    # research
    question_tree: str
    lanes_dir: str
    primary_candidates: str
    counter_candidates: str
    extracted_text_dir: str

    # evidence
    evidence_cards: str
    contradiction_ledger: str
    evidence_gap_map: str

    # narrative
    thesis_decision: str
    narrative_plan: str

    # draft / review
    draft_article: str
    citation_logic_report: str
    red_team_report: str
    qa_report: str
    compression_report: str
    image_audit_report: str
    revision_notes: str

    # final
    article: str
    research_appendix: str
    paragraph_map: str
    claim_ledger: str
    source_ledger: str
    image_ledger: str
    article_result: str
    final_sync_report: str

    agents_dir: str
    contracts_dir: str


def build_run_artifacts(
    *,
    work_run_dir: str,
    outputs_dir: str,
    article: str,
    source_ledger: str,
    image_ledger: str,
    claim_ledger: str,
    article_result: str,
) -> RunArtifacts:
    work = Path(work_run_dir).as_posix().rstrip("/")
    out = Path(outputs_dir).as_posix().rstrip("/")
    return RunArtifacts(
        work_run_dir=work,
        outputs_dir=out,
        original_request=f"{work}/intake/original_request.md",
        intent_contract=f"{work}/intake/intent_contract.json",
        question_tree=f"{work}/research/research_question_tree.json",
        lanes_dir=f"{work}/research/lanes",
        primary_candidates=f"{work}/research/source_candidates.primary.jsonl",
        counter_candidates=f"{work}/research/source_candidates.counter.jsonl",
        extracted_text_dir=f"{work}/research/extracted_text",
        evidence_cards=f"{work}/evidence/evidence_cards.jsonl",
        contradiction_ledger=f"{work}/research/contradiction_ledger.json",
        evidence_gap_map=f"{work}/research/evidence_gap_map.json",
        thesis_decision=f"{work}/research/thesis_decision.json",
        narrative_plan=f"{work}/research/narrative_plan.json",
        draft_article=f"{work}/drafts/draft_article.md",
        citation_logic_report=f"{work}/reviews/citation_logic.json",
        red_team_report=f"{work}/reviews/red_team.json",
        qa_report=f"{work}/reviews/qa_report.json",
        compression_report=f"{work}/reviews/compression.json",
        image_audit_report=f"{work}/reviews/image_audit.json",
        revision_notes=f"{work}/reviews/revision_notes.json",
        article=Path(article).as_posix(),
        research_appendix=f"{out}/research_appendix.md",
        paragraph_map=f"{out}/paragraph_map.json",
        claim_ledger=Path(claim_ledger).as_posix(),
        source_ledger=Path(source_ledger).as_posix(),
        image_ledger=Path(image_ledger).as_posix(),
        article_result=Path(article_result).as_posix(),
        final_sync_report=f"{out}/final_sync_report.json",
        agents_dir=f"{work}/agents",
        contracts_dir=f"{work}/contracts",
    )


@dataclass(frozen=True)
class ArtifactContract:
    """One required artifact and the schema it must validate against."""

    path: str
    label: str
    schema: str | None = None
    jsonl: bool = False
    run_id_scoped: bool = False


def scope_outputs(artifacts: RunArtifacts) -> tuple[ArtifactContract, ...]:
    return (
        ArtifactContract(
            path=artifacts.intent_contract,
            label="intent contract",
            schema="schemas/intent_contract.schema.json",
            run_id_scoped=True,
        ),
    )


def research_plan_outputs(artifacts: RunArtifacts) -> tuple[ArtifactContract, ...]:
    return (
        ArtifactContract(
            path=artifacts.question_tree,
            label="research question tree",
            schema="schemas/research_question_tree.schema.json",
            run_id_scoped=True,
        ),
    )


def research_outputs(artifacts: RunArtifacts) -> tuple[ArtifactContract, ...]:
    return (
        ArtifactContract(
            path=artifacts.primary_candidates,
            label="primary source candidates",
            schema="schemas/source_candidate.schema.json",
            jsonl=True,
        ),
        ArtifactContract(
            path=artifacts.counter_candidates,
            label="counterevidence source candidates",
            schema="schemas/source_candidate.schema.json",
            jsonl=True,
        ),
    )


def evidence_outputs(artifacts: RunArtifacts) -> tuple[ArtifactContract, ...]:
    return (
        ArtifactContract(
            path=artifacts.evidence_cards,
            label="evidence cards",
            schema="schemas/evidence_card.schema.json",
            jsonl=True,
        ),
        ArtifactContract(
            path=artifacts.contradiction_ledger,
            label="contradiction ledger",
            schema="schemas/contradiction_ledger.schema.json",
            run_id_scoped=True,
        ),
        ArtifactContract(
            path=artifacts.evidence_gap_map,
            label="evidence gap map",
            schema="schemas/evidence_gap_map.schema.json",
            run_id_scoped=True,
        ),
    )


def earnings_delta_outputs(
    matrix_json: str,
    matrix_md: str,
) -> tuple[ArtifactContract, ...]:
    """Contracts for the earnings-only delta artifacts produced inside the evidence phase."""

    return (
        ArtifactContract(
            path=matrix_json,
            label="earnings delta matrix",
            schema="schemas/earnings_delta.schema.json",
        ),
        ArtifactContract(path=matrix_md, label="earnings delta matrix markdown"),
    )


def narrative_outputs(artifacts: RunArtifacts) -> tuple[ArtifactContract, ...]:
    return (
        ArtifactContract(
            path=artifacts.thesis_decision,
            label="thesis decision",
            schema="schemas/thesis_decision.schema.json",
            run_id_scoped=True,
        ),
        ArtifactContract(
            path=artifacts.narrative_plan,
            label="narrative plan",
            schema="schemas/narrative_plan.schema.json",
            run_id_scoped=True,
        ),
    )


def draft_outputs(artifacts: RunArtifacts) -> tuple[ArtifactContract, ...]:
    return (ArtifactContract(path=artifacts.draft_article, label="draft article"),)


def review_outputs(artifacts: RunArtifacts, *, include_images: bool) -> tuple[ArtifactContract, ...]:
    contracts = [
        ArtifactContract(
            path=artifacts.citation_logic_report,
            label="citation logic report",
            schema="schemas/citation_logic_report.schema.json",
            run_id_scoped=True,
        ),
        ArtifactContract(
            path=artifacts.red_team_report,
            label="red team report",
            schema="schemas/red_team_report.schema.json",
            run_id_scoped=True,
        ),
        ArtifactContract(
            path=artifacts.qa_report,
            label="QA report",
            schema="schemas/qa_report.schema.json",
            run_id_scoped=True,
        ),
        ArtifactContract(
            path=artifacts.compression_report,
            label="compression report",
            schema="schemas/compression_report.schema.json",
            run_id_scoped=True,
        ),
    ]
    if include_images:
        contracts.append(
            ArtifactContract(
                path=artifacts.image_audit_report,
                label="image audit report",
                schema="schemas/image_audit_report.schema.json",
                run_id_scoped=True,
            )
        )
    return tuple(contracts)


def revise_outputs(artifacts: RunArtifacts) -> tuple[ArtifactContract, ...]:
    return (
        ArtifactContract(path=artifacts.draft_article, label="revised draft article"),
        ArtifactContract(
            path=artifacts.revision_notes,
            label="revision notes",
            schema="schemas/revision_notes.schema.json",
            run_id_scoped=True,
        ),
    )


def finalize_outputs(artifacts: RunArtifacts) -> tuple[ArtifactContract, ...]:
    return (
        ArtifactContract(path=artifacts.article, label="final article"),
        ArtifactContract(path=artifacts.research_appendix, label="research appendix"),
        ArtifactContract(
            path=artifacts.paragraph_map,
            label="paragraph map",
            schema="schemas/paragraph_map.schema.json",
            run_id_scoped=True,
        ),
        ArtifactContract(
            path=artifacts.claim_ledger,
            label="claim ledger",
            schema="schemas/claim_ledger.schema.json",
        ),
        ArtifactContract(path=artifacts.source_ledger, label="source ledger"),
        ArtifactContract(path=artifacts.image_ledger, label="image ledger"),
        ArtifactContract(
            path=artifacts.article_result,
            label="article result",
            schema="schemas/article_result.schema.json",
        ),
    )


PHASE_UPSTREAM_REQUIREMENTS = {
    "scope": (),
    "research-plan": ("scope",),
    "research": ("scope", "research-plan"),
    "evidence": ("scope", "research-plan", "research"),
    "narrative": ("scope", "research-plan", "research", "evidence"),
    "draft": ("scope", "research-plan", "research", "evidence", "narrative"),
    "review": ("scope", "research-plan", "research", "evidence", "narrative", "draft"),
    "revise": ("scope", "research-plan", "research", "evidence", "narrative", "draft", "review"),
    "finalize": (
        "scope",
        "research-plan",
        "research",
        "evidence",
        "narrative",
        "draft",
        "review",
        "revise",
    ),
}


def phase_output_contracts(
    phase: str,
    artifacts: RunArtifacts,
    *,
    include_images: bool = False,
) -> tuple[ArtifactContract, ...]:
    if phase == "scope":
        return scope_outputs(artifacts)
    if phase == "research-plan":
        return research_plan_outputs(artifacts)
    if phase == "research":
        return research_outputs(artifacts)
    if phase == "evidence":
        return evidence_outputs(artifacts)
    if phase == "narrative":
        return narrative_outputs(artifacts)
    if phase == "draft":
        return draft_outputs(artifacts)
    if phase == "review":
        return review_outputs(artifacts, include_images=include_images)
    if phase == "revise":
        return revise_outputs(artifacts)
    if phase == "finalize":
        return finalize_outputs(artifacts)
    return ()


# Phases whose roles consume the runner-owned source inventory. Research scouts and the
# evidence roles read it directly; earlier phases exist before it is meaningful.
_SOURCE_INVENTORY_PHASES = frozenset(
    {"research", "evidence", "narrative", "draft", "review", "revise", "finalize"}
)


def phase_input_contracts(
    phase: str,
    artifacts: RunArtifacts,
    *,
    topic_path: str = "",
    sources_path: str = "",
    source_candidate_ledger: str = "",
    role: str = "",
    rendered_images_ledger: str = "",
    image_inputs: tuple[str, ...] = (),
    extracted_text_paths: tuple[str, ...] = (),
    include_images: bool = False,
) -> tuple[ArtifactContract, ...]:
    """Non-output artifacts a phase's roles actually read.

    Provenance protects the evidence chain, and that chain starts before the first phase
    output: the scope guardian reads the normalized topic, and the counterevidence scout is
    handed the source inventory. Declaring those here keeps one definition of what a phase
    consumed, so a protected input cannot be forgotten in a second ad-hoc list.

    Pass `role` to add that role's own declared inputs. Both the provenance checkpoint and the
    per-role attempt envelope resolve their inputs here, so the two cannot drift apart.
    """

    if phase not in PHASES:
        return ()
    contracts: list[ArtifactContract] = [
        ArtifactContract(path=artifacts.original_request, label="original request"),
    ]
    if topic_path:
        contracts.append(ArtifactContract(path=topic_path, label="normalized topic input"))
    if sources_path:
        contracts.append(ArtifactContract(path=sources_path, label="source inventory input"))
    if source_candidate_ledger and phase in _SOURCE_INVENTORY_PHASES:
        contracts.append(
            ArtifactContract(
                path=source_candidate_ledger,
                label="source candidate ledger",
            )
        )
    contracts.extend(
        _role_input_contracts(
            role,
            artifacts,
            rendered_images_ledger=rendered_images_ledger,
            image_inputs=image_inputs,
            extracted_text_paths=extracted_text_paths,
            include_images=include_images,
        )
    )
    return tuple(contracts)


# Roles that review the draft against the run's approved intent. Their prompts tell them to
# read the intent contract, so it is a real input and must be hashed like one.
_INTENT_READING_ROLES = frozenset(
    {"citation_logic_checker", "editorial_red_team", "qa_reviewer", "compression_editor"}
)


def _role_input_contracts(
    role: str,
    artifacts: RunArtifacts,
    *,
    rendered_images_ledger: str,
    image_inputs: tuple[str, ...],
    extracted_text_paths: tuple[str, ...],
    include_images: bool,
) -> tuple[ArtifactContract, ...]:
    """Inputs a specific role reads beyond its phase's shared inputs."""

    if not role:
        return ()
    contracts: list[ArtifactContract] = []
    if role in _INTENT_READING_ROLES:
        contracts.append(
            ArtifactContract(path=artifacts.intent_contract, label="intent contract")
        )
    if role == "evidence_verifier":
        # The verifier checks each quote against the extracted source text, so those files
        # are real inputs even though the ledger, not a contract, names them.
        contracts.extend(
            ArtifactContract(path=text_path, label="extracted source text")
            for text_path in extracted_text_paths
        )
    if role == "image_slide_auditor":
        if rendered_images_ledger:
            contracts.append(
                ArtifactContract(
                    path=rendered_images_ledger,
                    label="rendered image ledger",
                )
            )
        contracts.extend(
            ArtifactContract(path=image_input, label="rendered image input")
            for image_input in image_inputs
        )
    if role == "finalizer" and include_images:
        # The audit report is the canonical image decision artifact. The finalizer also reads
        # the runner-owned rendered ledger to preserve source and render metadata exactly.
        contracts.append(
            ArtifactContract(path=artifacts.image_audit_report, label="image audit report")
        )
        if rendered_images_ledger:
            contracts.append(
                ArtifactContract(
                    path=rendered_images_ledger,
                    label="rendered image ledger",
                )
            )
    return tuple(contracts)
