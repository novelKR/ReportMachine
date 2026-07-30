"""Sequenced execution of the article pipeline phases.

`workflow.py` owns run setup, preflight, and final export. This module owns the ordered
role invocations between them, so each phase's gate and task instructions live in one place.
"""

from __future__ import annotations

import csv
import json
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Callable, Sequence

from .artifact_validation import (
    duplicate_object_ids,
    read_jsonl,
    validate_artifact_contracts,
)
from .final_sync import validate_final_image_ledger, validate_image_audit_report
from .paths import resolve_under_root
from .phase_contracts import (
    PHASE_UPSTREAM_REQUIREMENTS,
    RunArtifacts,
    earnings_delta_outputs,
    phase_output_contracts,
)
from .phase_provenance import (
    IngestReceiptBaseline,
    PhaseMutationGuard,
    ProvenanceContext,
    RunnerMutationViolation,
    capture_ingest_receipt_baseline,
    invalidate_from_phase,
    validate_upstream_provenance,
    write_ingest_receipt,
    write_phase_checkpoint,
)
from .phases import (
    PhaseOutcome,
    PhaseRunner,
    check_scope_gate,
    extra_research_lane_id,
    extra_research_lane_path,
    invalid_upstream_artifacts,
    lane_artifact_paths,
    load_question_tree,
    merge_lane_candidates,
    missing_upstream_artifacts,
    resolve_lanes,
    validate_question_tree_semantics,
)
from .review_issues import (
    review_report_paths,
    validate_review_reports,
    validate_revision_coverage,
)
from .source_ingest_paths import SourceIngestResult, build_source_ingest_paths


PIPELINE_ORDER = (
    "scope",
    "research-plan",
    "research",
    "evidence",
    "narrative",
    "draft",
    "review",
    "revise",
    "finalize",
)


@dataclass(frozen=True)
class PipelineContext:
    runner: PhaseRunner
    artifacts: RunArtifacts
    mode: str
    approve_scope: bool
    max_extra_research_rounds: int = 1
    # Source ingest returns its invocation-local written keys. The pipeline records the
    # receipt only after the narrow runner mutation capability has accepted every write.
    research_ingest: Callable[[Sequence[str]], SourceIngestResult] | None = None
    validate_schemas: bool = True
    provenance: ProvenanceContext | None = None
    mutation_guard: PhaseMutationGuard | None = None

    def provenance_context(self) -> ProvenanceContext:
        """Resolve this run's provenance scope, defaulting to the runner's own paths."""

        if self.provenance is not None:
            return self.provenance
        runner = self.runner
        contract = runner.run_contract
        earnings: tuple[str, ...] = ()
        if self.mode == "earnings":
            earnings = tuple(
                str(contract.get(key) or "")
                for key in ("earnings_delta_matrix", "earnings_delta_matrix_md")
                if str(contract.get(key) or "")
            )
        return ProvenanceContext(
            root=runner.root,
            run_id=runner.run_id,
            artifacts=self.artifacts,
            include_images=runner.has_rendered_images(),
            earnings_delta=earnings,
            topic_path=str(contract.get("topic_path") or ""),
            sources_path=str(contract.get("sources_path") or ""),
            run_manifest=str(contract.get("run_manifest") or ""),
        )


@dataclass(frozen=True)
class PhaseSemanticContract:
    validator: Callable[[PipelineContext], list[str]]
    producer_failure_label: str


def phases_to_run(phase: str) -> tuple[str, ...]:
    """Resolve which pipeline phases a `--phase` value should execute."""

    if phase == "all":
        return PIPELINE_ORDER
    if phase in PIPELINE_ORDER:
        return (phase,)
    return ()


def run_pipeline(context: PipelineContext, phases: Sequence[str]) -> PhaseOutcome:
    for index, phase in enumerate(phases):
        # Only the entry phase inherits artifacts this invocation did not produce.
        outcome = run_phase(context, phase, revalidate_upstream=index == 0)
        if not outcome.ok:
            return outcome
    return PhaseOutcome(status=0, label="pipeline_complete")


def _invalid_upstream_semantics(context: PipelineContext, phase: str) -> list[str]:
    """Re-apply the same semantic contracts used after producer completion."""

    errors: list[str] = []
    for upstream in PHASE_UPSTREAM_REQUIREMENTS.get(phase, ()):
        contract = PHASE_SEMANTIC_VALIDATORS.get(upstream)
        if contract is None:
            continue
        errors.extend(
            f"{upstream} phase artifact is unusable: {error}"
            for error in contract.validator(context)
        )
    return errors


def run_phase(
    context: PipelineContext,
    phase: str,
    *,
    revalidate_upstream: bool = True,
) -> PhaseOutcome:
    runner = context.runner
    root = runner.root
    artifacts = context.artifacts

    missing = missing_upstream_artifacts(
        root,
        phase,
        artifacts,
        include_images=runner.has_rendered_images(),
    )
    if missing:
        return PhaseOutcome(
            status=2,
            label=f"{phase}_missing_upstream",
            messages=tuple(
                [
                    f"phase `{phase}` cannot start because upstream artifacts are missing.",
                    *missing,
                    "Run the earlier phases first, or use --phase all.",
                ]
            ),
        )

    if revalidate_upstream:
        invalid = invalid_upstream_artifacts(
            root,
            phase,
            artifacts,
            include_images=runner.has_rendered_images(),
            validate_schemas=context.validate_schemas,
            expected_run_id=runner.run_id,
        )
        invalid.extend(_invalid_upstream_semantics(context, phase))
        # Contract and semantic checks cannot see an edit made after the producing role
        # ran, so a resumed phase also has to match the recorded completion state.
        invalid.extend(validate_upstream_provenance(context.provenance_context(), phase))
        if invalid:
            return PhaseOutcome(
                status=2,
                label=f"{phase}_invalid_upstream",
                messages=tuple(
                    [
                        f"phase `{phase}` cannot start because upstream artifacts are unusable.",
                        *invalid,
                        "Re-run the earlier phases to regenerate them, or use --phase all.",
                    ]
                ),
            )

    if phase != "scope":
        allowed, messages = check_scope_gate(
            root,
            artifacts,
            approve=False,
            validate_schema=context.validate_schemas,
        )
        if not allowed:
            return PhaseOutcome(status=2, label="scope_gate_blocked", messages=tuple(messages))

    handler = _HANDLERS.get(phase)
    if handler is None:  # pragma: no cover - phase table is closed
        return PhaseOutcome(status=2, label=f"{phase}_unknown")
    provenance = context.provenance_context()
    mutation_guard: PhaseMutationGuard | None = None
    if not runner.dry_run:
        # This phase is about to rewrite its outputs, so its own checkpoint and every
        # downstream one stop describing the run until they are regenerated.
        invalidate_from_phase(provenance, phase)
        # Freeze the upstream artifacts this phase does not own. Otherwise a role could edit
        # an earlier phase's output and have this phase's checkpoint record that edit as the
        # legitimate completion state.
        mutation_guard = PhaseMutationGuard.create(
            provenance,
            phase,
            owned_paths=[
                contract.path
                for contract in phase_output_contracts(
                    phase,
                    artifacts,
                    include_images=runner.has_rendered_images(),
                )
            ],
        )
    phase_context = replace(context, mutation_guard=mutation_guard)
    try:
        outcome = handler(phase_context)
    except BaseException:
        # KeyboardInterrupt and cancellation still close the phase boundary. If the
        # interrupted handler changed protected state, surface the fail-closed recovery
        # instruction instead of allowing a later checkpoint to canonize it.
        guard_errors = mutation_guard.violations() if mutation_guard is not None else []
        if guard_errors:
            return _upstream_mutation_outcome(phase_context, phase, guard_errors)
        raise
    guard_errors = mutation_guard.violations() if mutation_guard is not None else []
    if guard_errors:
        return _upstream_mutation_outcome(phase_context, phase, guard_errors)
    if not outcome.ok:
        return outcome
    if runner.dry_run:
        return outcome

    errors = runner.validate_phase(phase)
    if errors:
        return PhaseOutcome(
            status=2,
            label=f"{phase}_validation_fail",
            messages=tuple(errors),
        )
    semantic_contract = PHASE_SEMANTIC_VALIDATORS.get(phase)
    if semantic_contract is not None:
        semantic_errors = semantic_contract.validator(context)
        if semantic_errors:
            return PhaseOutcome(
                status=2,
                label=semantic_contract.producer_failure_label,
                messages=tuple(semantic_errors),
            )
    write_phase_checkpoint(provenance, phase)
    return outcome


def _upstream_mutation_outcome(
    context: PipelineContext,
    phase: str,
    errors: Sequence[str],
) -> PhaseOutcome:
    producer = _earliest_guard_producer(context, errors)
    recovery = (
        f"Re-run the earliest producer with --phase {producer}, then regenerate its "
        "downstream phases."
        if producer is not None
        else (
            "The affected producer cannot be established safely from the current "
            "receipt lineage; re-run with --phase all."
        )
    )
    return PhaseOutcome(
        status=2,
        label=f"{phase}_upstream_mutation",
        messages=tuple([*errors, recovery]),
    )


def _earliest_guard_producer(
    context: PipelineContext,
    errors: Sequence[str],
) -> str | None:
    producers: list[str] = []
    for error in errors:
        marker = "artifact it does not own: "
        if marker not in error:
            return None
        rel = error.split(marker, 1)[1].strip()
        producer = _guard_path_producer(context, rel)
        if producer is None:
            return None
        producers.append(producer)
    if not producers:
        return None
    order = ("prepare", *PIPELINE_ORDER)
    if any(producer not in order for producer in producers):
        return None
    return min(producers, key=order.index)


def _guard_path_producer(context: PipelineContext, rel: str) -> str | None:
    provenance = context.provenance_context()
    ledger_lookups = (
        (
            provenance.source_candidate_ledger,
            "extracted_text_path",
        ),
        (
            provenance.rendered_images_ledger,
            "image_path",
        ),
    )
    for ledger_rel, derived_field in ledger_lookups:
        if rel == ledger_rel:
            # A changed canonical ledger does not reveal which row was rewritten.
            return None
        try:
            ledger_path = resolve_under_root(context.runner.root, ledger_rel)
        except ValueError:
            continue
        if not ledger_path.is_file():
            continue
        try:
            with ledger_path.open(newline="", encoding="utf-8") as handle:
                rows = list(csv.DictReader(handle))
        except (OSError, UnicodeError, csv.Error):
            return None
        matching_rows = [
            row
            for row in rows
            if str(row.get(derived_field) or "").strip() == rel
        ]
        if matching_rows:
            # A receipt proves which ingest wrote a row, not which cumulative run state is
            # safe after the ledger-derived file itself was changed. Always restart the
            # full pipeline for canonical ledger authority and its referenced material.
            return None

    if rel in provenance.earnings_delta:
        return "evidence"

    for producer in PIPELINE_ORDER:
        if any(
            contract.path == rel
            for contract in phase_output_contracts(
                producer,
                context.artifacts,
                include_images=context.runner.has_rendered_images(),
            )
        ):
            return producer
    return None


def _run_candidate_merge(
    context: PipelineContext,
    *,
    label: str,
    target: str,
    sources: Sequence[str],
) -> tuple[str, ...]:
    """Run one runner-owned candidate merge under an exact-file capability."""

    callback = lambda: tuple(
        merge_lane_candidates(
            context.runner.root,
            target=target,
            sources=sources,
        )
    )
    guard = context.mutation_guard
    if guard is None:
        return callback()
    try:
        return tuple(
            guard.run_runner_mutation(
                label,
                callback,
                exact_files=(target,),
                error_getter=lambda errors: errors,
            )
        )
    except RunnerMutationViolation as exc:
        return exc.errors


def _run_research_ingest(
    context: PipelineContext,
    *,
    stage: str,
    candidate_paths: Sequence[str],
    baseline: IngestReceiptBaseline,
) -> tuple[str, ...]:
    """Run source ingest narrowly, then bind only its reported row delta to a receipt."""

    ingest = context.research_ingest
    if ingest is None:
        return ()
    paths = build_source_ingest_paths(context.artifacts.work_run_dir)
    callback = lambda: ingest(candidate_paths)
    guard = context.mutation_guard
    try:
        result = (
            callback()
            if guard is None
            else guard.run_runner_mutation(
                f"{stage} source ingest",
                callback,
                exact_files=(
                    *candidate_paths,
                    paths.manifest,
                    paths.source_candidate_ledger,
                    paths.pdf_index,
                    paths.rendered_images_ledger,
                ),
                allowed_prefixes=(
                    paths.sources_dir,
                    paths.pdf_text_dir,
                    paths.extracted_text_dir,
                    paths.rendered_images_dir,
                ),
                error_getter=lambda value: value.errors,
            )
        )
    except RunnerMutationViolation as exc:
        return exc.errors
    if result.errors:
        return result.errors
    provenance = context.provenance_context()
    try:
        write_ingest_receipt(
            provenance,
            stage,
            baseline=baseline,
            input_paths=(provenance.sources_path, *candidate_paths),
            written_source_ids=result.written_source_ids,
            written_image_paths=result.written_image_paths,
        )
    except OSError as exc:
        return (f"failed to record {stage} source ingest receipt: {exc}",)
    return ()


def _run_scope(context: PipelineContext) -> PhaseOutcome:
    runner = context.runner
    artifacts = context.artifacts
    # The guardian reads the normalized topic, so it belongs in the recorded inputs.
    topic_path = str(runner.run_contract.get("topic_path") or "")
    outcome = runner.invoke_role(
        role="scope_guardian",
        phase="scope",
        task_instructions=(
            "Compare the recorded original request with the normalized run inputs.\n"
            f"Read the original request from `{artifacts.original_request}`.\n"
            f"Read the current draft intent contract from `{artifacts.intent_contract}`.\n"
            f"Read the normalized topic input from `{runner.run_contract['topic_path']}`.\n"
            f"Write the completed intent contract to `{artifacts.intent_contract}`.\n"
            "Preserve original_request verbatim, and copy approval_status and "
            "approved_scope_delta_sha256 from the existing contract without changing them."
        ),
        input_artifacts=[
            artifacts.original_request,
            artifacts.intent_contract,
            *([topic_path] if topic_path else []),
        ],
        output_artifacts=[artifacts.intent_contract],
    )
    if not outcome.ok:
        return outcome
    if runner.dry_run:
        return outcome

    allowed, messages = check_scope_gate(
        runner.root,
        artifacts,
        approve=context.approve_scope,
    )
    if not allowed:
        return PhaseOutcome(status=2, label="scope_approval_required", messages=tuple(messages))
    return PhaseOutcome(status=0, label="scope", messages=tuple(messages))


def _run_research_plan(context: PipelineContext) -> PhaseOutcome:
    runner = context.runner
    artifacts = context.artifacts
    lane_limit = runner.lane_limit()
    return runner.invoke_role(
        role="research_planner",
        phase="research-plan",
        task_instructions=(
            "Design the research question tree and exploration lanes for this run.\n"
            f"Read the approved intent contract from `{artifacts.intent_contract}`.\n"
            f"Write the question tree to `{artifacts.question_tree}`.\n"
            f"Depth is `{runner.depth}`; produce at most {lane_limit} lanes.\n"
            "At least one lane must target disconfirmation or downside risk."
        ),
        input_artifacts=[artifacts.intent_contract],
        output_artifacts=[artifacts.question_tree],
    )


def _run_research(context: PipelineContext) -> PhaseOutcome:
    runner = context.runner
    artifacts = context.artifacts
    tree, errors = load_question_tree(runner.root, artifacts)
    if tree is None:
        return PhaseOutcome(status=2, label="research_missing_tree", messages=tuple(errors))
    lanes = resolve_lanes(tree)
    if not lanes and not runner.dry_run:
        return PhaseOutcome(
            status=2,
            label="research_no_lanes",
            messages=("research question tree defines no lanes",),
        )

    # Track the lanes this invocation produced so the merge cannot pick up outputs
    # from a superseded research plan.
    lane_outputs: list[str] = []
    for lane in lanes:
        lane_id = str(lane.get("lane_id", "")).strip()
        if not lane_id:
            continue
        lane_output = lane_artifact_paths(artifacts, lane_id)
        lane_outputs.append(lane_output)
        question_ids = ", ".join(str(item) for item in (lane.get("question_ids") or []))
        outcome = runner.invoke_role(
            role="primary_source_scout",
            phase="research",
            lane_id=lane_id,
            task_instructions=(
                f"You are assigned lane `{lane_id}`: {lane.get('title', '')}.\n"
                f"Work only on question ids: {question_ids}.\n"
                f"Read the question tree from `{artifacts.question_tree}`.\n"
                f"Write JSONL source candidates to `{lane_output}`, one JSON object per line.\n"
                "Ignore every other lane."
            ),
            input_artifacts=[artifacts.question_tree],
            output_artifacts=[lane_output],
        )
        if not outcome.ok:
            return outcome

    counter_output = lane_artifact_paths(artifacts, "counter", counter=True)
    # The counterevidence scout's role card lists the source inventory among its inputs, so
    # record it here rather than letting the ledger reach the role unrecorded.
    source_ledger = str(runner.run_contract.get("source_candidate_ledger") or "")
    outcome = runner.invoke_role(
        role="counterevidence_scout",
        phase="research",
        lane_id="counter",
        task_instructions=(
            "Search for the strongest material that would falsify this run's hypotheses.\n"
            f"Read the question tree and its hypotheses from `{artifacts.question_tree}`.\n"
            f"Write JSONL disconfirming candidates to `{counter_output}`.\n"
            "You must not read any draft article or chosen thesis."
        ),
        input_artifacts=[
            artifacts.question_tree,
            *([source_ledger] if source_ledger else []),
        ],
        output_artifacts=[counter_output],
    )
    if not outcome.ok:
        return outcome

    if runner.dry_run:
        return PhaseOutcome(status=0, label="research:dry_run")

    receipt_baseline = (
        capture_ingest_receipt_baseline(context.provenance_context())
        if context.research_ingest is not None
        else None
    )
    merge_errors = list(_run_candidate_merge(
        context,
        label="research primary candidate merge",
        target=artifacts.primary_candidates,
        sources=lane_outputs,
    ))
    merge_errors += _run_candidate_merge(
        context,
        label="research counter candidate merge",
        target=artifacts.counter_candidates,
        sources=[counter_output],
    )
    if merge_errors:
        return PhaseOutcome(status=2, label="research_merge_fail", messages=tuple(merge_errors))
    candidate_errors = runner.validate_phase("research")
    if candidate_errors:
        return PhaseOutcome(
            status=2,
            label="research_candidate_validation_fail",
            messages=tuple(candidate_errors),
        )
    if context.research_ingest is not None and receipt_baseline is not None:
        ingest_errors = _run_research_ingest(
            context,
            stage="research",
            candidate_paths=(
                artifacts.primary_candidates,
                artifacts.counter_candidates,
            ),
            baseline=receipt_baseline,
        )
        if ingest_errors:
            return PhaseOutcome(
                status=2,
                label="research_source_ingest_fail",
                messages=ingest_errors,
            )
    return PhaseOutcome(status=0, label="research")


def _run_evidence(context: PipelineContext) -> PhaseOutcome:
    runner = context.runner
    artifacts = context.artifacts
    source_ledger = str(runner.run_contract.get("source_candidate_ledger") or "")
    source_ledger_instruction = (
        f"Use runner-assigned source ids and ingest statuses from `{source_ledger}`.\n"
        if source_ledger
        else ""
    )
    outcome = runner.invoke_role(
        role="evidence_verifier",
        phase="evidence",
        task_instructions=(
            "Turn source candidates into verifiable evidence cards.\n"
            f"Read candidates from `{artifacts.primary_candidates}` and `{artifacts.counter_candidates}`.\n"
            f"Extracted source text for quote verification is under `{artifacts.extracted_text_dir}/`.\n"
            f"{source_ledger_instruction}"
            f"Write JSONL evidence cards to `{artifacts.evidence_cards}`.\n"
            "Every factual card needs a verbatim exact_quote and a locator that a code audit can check. "
            "A blocked or remote-unverified candidate cannot be promoted into verified evidence."
        ),
        input_artifacts=[
            artifacts.primary_candidates,
            artifacts.counter_candidates,
            *([source_ledger] if source_ledger else []),
        ],
        output_artifacts=[artifacts.evidence_cards],
    )
    if not outcome.ok:
        return outcome
    if not runner.dry_run:
        evidence_errors = _validate_evidence_cards_boundary(context)
        if evidence_errors:
            return PhaseOutcome(
                status=2,
                label="evidence_semantic_fail",
                messages=tuple(evidence_errors),
            )

    outcome = runner.invoke_role(
        role="evidence_integrator",
        phase="evidence",
        task_instructions=(
            "Merge duplicates, record conflicts, and map remaining gaps.\n"
            f"Read evidence cards from `{artifacts.evidence_cards}`.\n"
            f"Read the research question tree from `{artifacts.question_tree}`; it is authoritative "
            "for question ids and importance.\n"
            f"Read discovery and ingest coverage from `{artifacts.primary_candidates}` and "
            f"`{artifacts.counter_candidates}`.\n"
            f"Write the contradiction ledger to `{artifacts.contradiction_ledger}`.\n"
            f"Write the evidence gap map to `{artifacts.evidence_gap_map}`.\n"
            "Every gap and contradiction needs the question_id it belongs to, and gap importance "
            "must equal that question's importance in the tree.\n"
            "Mirror every unresolved p0 contradiction into the gap map as an `unresolved_conflict` gap."
        ),
        input_artifacts=[
            artifacts.question_tree,
            artifacts.primary_candidates,
            artifacts.counter_candidates,
            artifacts.evidence_cards,
        ],
        output_artifacts=[artifacts.contradiction_ledger, artifacts.evidence_gap_map],
    )
    if not outcome.ok:
        return outcome
    if not runner.dry_run:
        evidence_errors = [
            *runner.validate_phase("evidence"),
            *_validate_evidence_integration_semantics(context),
        ]
        if evidence_errors:
            return PhaseOutcome(
                status=2,
                label="evidence_validation_fail",
                messages=tuple(sorted(set(evidence_errors))),
            )

    outcome = _maybe_extra_research(context)
    if not outcome.ok:
        return outcome
    # The analyst runs only after every extra research round settled the evidence set,
    # so its matrix and recorded input hash describe the final cards rather than a
    # pre-extra snapshot that later rounds silently replaced.
    return _run_earnings_delta(context)


def _run_earnings_delta(context: PipelineContext) -> PhaseOutcome:
    """Build the earnings delta matrix from the run's final evidence cards."""

    runner = context.runner
    artifacts = context.artifacts
    if context.mode != "earnings":
        return PhaseOutcome(status=0, label="evidence")
    matrix_json = str(runner.run_contract["earnings_delta_matrix"])
    matrix_md = str(runner.run_contract["earnings_delta_matrix_md"])
    outcome = runner.invoke_role(
        role="earnings_delta_analyst",
        phase="evidence",
        task_instructions=(
            "Compare current-quarter and prior-quarter materials side by side.\n"
            f"Read evidence cards from `{artifacts.evidence_cards}`.\n"
            f"Write the delta matrix JSON to `{matrix_json}` "
            f"and the readable matrix to `{matrix_md}`."
        ),
        input_artifacts=[artifacts.evidence_cards],
        output_artifacts=[matrix_json, matrix_md],
    )
    if not outcome.ok or runner.dry_run:
        return outcome
    errors = validate_artifact_contracts(
        runner.root,
        earnings_delta_outputs(matrix_json, matrix_md),
        validate_schemas=context.validate_schemas,
    )
    if errors:
        return PhaseOutcome(
            status=2,
            label="evidence_earnings_delta_fail",
            messages=tuple(errors),
        )
    return PhaseOutcome(status=0, label="evidence")


def _planned_primary_lane_outputs(context: PipelineContext) -> list[str]:
    """Resolve the primary lane outputs the current question tree defines."""

    runner = context.runner
    artifacts = context.artifacts
    tree, _ = load_question_tree(runner.root, artifacts)
    if tree is None:
        return []
    outputs: list[str] = []
    for lane in resolve_lanes(tree):
        lane_id = str(lane.get("lane_id", "")).strip()
        if lane_id:
            outputs.append(lane_artifact_paths(artifacts, lane_id))
    return outputs


def _maybe_extra_research(context: PipelineContext) -> PhaseOutcome:
    """Run up to N isolated extra rounds while actionable p0 gaps remain."""

    runner = context.runner
    artifacts = context.artifacts
    if runner.dry_run or context.max_extra_research_rounds <= 0:
        return PhaseOutcome(status=0, label="evidence")

    completed_extra_outputs: list[str] = []
    for round_number in range(1, context.max_extra_research_rounds + 1):
        gap_map = _read_json(runner.root, artifacts.evidence_gap_map)
        if not isinstance(gap_map, dict) or not gap_map.get(
            "extra_research_recommended"
        ):
            break
        p0_gaps = [
            gap
            for gap in (gap_map.get("gaps") or [])
            if isinstance(gap, dict) and gap.get("importance") == "p0"
        ]
        if not p0_gaps:
            break

        lane_id = extra_research_lane_id(round_number)
        lane_output = extra_research_lane_path(artifacts, round_number)
        descriptions = "; ".join(
            str(gap.get("description", "")) for gap in p0_gaps[:5]
        )
        outcome = runner.invoke_role(
            role="primary_source_scout",
            phase="evidence",
            lane_id=lane_id,
            task_instructions=(
                f"Run targeted extra research round {round_number} for unresolved p0 gaps only.\n"
                f"Gaps: {descriptions}\n"
                f"Read the current gap map from `{artifacts.evidence_gap_map}`.\n"
                f"Write JSONL candidates to `{lane_output}`."
            ),
            input_artifacts=[artifacts.evidence_gap_map],
            output_artifacts=[lane_output],
        )
        if not outcome.ok:
            return outcome
        completed_extra_outputs.append(lane_output)

        receipt_baseline = (
            capture_ingest_receipt_baseline(context.provenance_context())
            if context.research_ingest is not None
            else None
        )
        merge_errors = _run_candidate_merge(
            context,
            label=f"evidence extra-research candidate merge round {round_number}",
            target=artifacts.primary_candidates,
            # Rewrite only from current-plan lanes and completed internal rounds.
            # The internal namespace and path never collide with a planned lane.
            sources=[
                *_planned_primary_lane_outputs(context),
                *completed_extra_outputs,
            ],
        )
        if merge_errors:
            return PhaseOutcome(
                status=2,
                label="evidence_merge_fail",
                messages=tuple(merge_errors),
            )
        candidate_errors = runner.validate_phase("research")
        if candidate_errors:
            return PhaseOutcome(
                status=2,
                label="evidence_candidate_validation_fail",
                messages=tuple(candidate_errors),
            )
        if context.research_ingest is not None and receipt_baseline is not None:
            ingest_errors = _run_research_ingest(
                context,
                stage="evidence",
                candidate_paths=(artifacts.primary_candidates,),
                baseline=receipt_baseline,
            )
            if ingest_errors:
                return PhaseOutcome(
                    status=2,
                    label="evidence_source_ingest_fail",
                    messages=ingest_errors,
                )

        outcome = runner.invoke_role(
            role="evidence_verifier",
            phase="evidence",
            lane_id=lane_id,
            task_instructions=(
                f"Rebuild the evidence cards after extra research round {round_number}.\n"
                f"Read merged candidates from `{artifacts.primary_candidates}`.\n"
                f"Rewrite `{artifacts.evidence_cards}` while preserving existing verified cards "
                "and keeping every evidence_id unique."
            ),
            input_artifacts=[
                artifacts.primary_candidates,
                artifacts.counter_candidates,
                artifacts.evidence_cards,
            ],
            output_artifacts=[artifacts.evidence_cards],
        )
        if not outcome.ok:
            return outcome
        evidence_errors = _validate_evidence_cards_boundary(context)
        if evidence_errors:
            return PhaseOutcome(
                status=2,
                label="evidence_semantic_fail",
                messages=tuple(evidence_errors),
            )

        outcome = runner.invoke_role(
            role="evidence_integrator",
            phase="evidence",
            lane_id=lane_id,
            task_instructions=(
                f"Re-integrate after extra research round {round_number}.\n"
                f"Read the research question tree from `{artifacts.question_tree}`; it is "
                "authoritative for question ids and importance.\n"
                f"Read merged candidates from `{artifacts.primary_candidates}` and "
                f"`{artifacts.counter_candidates}`.\n"
                f"Rewrite `{artifacts.contradiction_ledger}` and `{artifacts.evidence_gap_map}`.\n"
                "Every gap and contradiction needs its question_id, and gap importance must equal "
                "that question's importance in the tree.\n"
                "Mirror every unresolved p0 contradiction as an `unresolved_conflict` gap.\n"
                "Set extra_research_recommended to false unless a p0 gap genuinely remains."
            ),
            input_artifacts=[
                artifacts.question_tree,
                artifacts.primary_candidates,
                artifacts.counter_candidates,
                artifacts.evidence_cards,
            ],
            output_artifacts=[artifacts.contradiction_ledger, artifacts.evidence_gap_map],
        )
        if not outcome.ok:
            return outcome
        evidence_errors = [
            *runner.validate_phase("evidence"),
            *_validate_evidence_integration_semantics(context),
        ]
        if evidence_errors:
            return PhaseOutcome(
                status=2,
                label="evidence_validation_fail",
                messages=tuple(sorted(set(evidence_errors))),
            )

    # Reaching the configured maximum is not a claim that the gaps were resolved.
    # Keep the last integrator output intact so downstream work can qualify it.
    return PhaseOutcome(status=0, label="evidence")


def _run_narrative(context: PipelineContext) -> PhaseOutcome:
    runner = context.runner
    artifacts = context.artifacts
    outcome = runner.invoke_role(
        role="thesis_adjudicator",
        phase="narrative",
        task_instructions=(
            "Compare at least three competing theses and choose one.\n"
            f"Read evidence cards from `{artifacts.evidence_cards}`.\n"
            f"Read conflicts from `{artifacts.contradiction_ledger}`.\n"
            f"Write the decision to `{artifacts.thesis_decision}`.\n"
            "Choose the thesis whose evidence and limitations are jointly best explained."
        ),
        input_artifacts=[artifacts.evidence_cards, artifacts.contradiction_ledger],
        output_artifacts=[artifacts.thesis_decision],
    )
    if not outcome.ok:
        return outcome

    return runner.invoke_role(
        role="narrative_architect",
        phase="narrative",
        task_instructions=(
            "Turn the chosen thesis into a section plan.\n"
            f"Read the thesis decision from `{artifacts.thesis_decision}`.\n"
            f"Write the narrative plan to `{artifacts.narrative_plan}`.\n"
            "Every section needs one new claim or causal link and its claim ids.\n"
            "Route methodology and source-limitation tables to the research appendix."
        ),
        input_artifacts=[artifacts.thesis_decision, artifacts.evidence_cards],
        output_artifacts=[artifacts.narrative_plan],
    )


def _run_draft(context: PipelineContext) -> PhaseOutcome:
    runner = context.runner
    artifacts = context.artifacts
    outcome = runner.invoke_role(
        role="article_writer",
        phase="draft",
        task_instructions=(
            "Write the draft article from approved artifacts only.\n"
            f"Read the narrative plan from `{artifacts.narrative_plan}`.\n"
            f"Read evidence cards from `{artifacts.evidence_cards}`.\n"
            f"Read the thesis decision from `{artifacts.thesis_decision}`.\n"
            f"Write the draft to `{artifacts.draft_article}`.\n"
            "End each substantive paragraph with `<!-- claims: ... | evidence: ... -->` using real ids.\n"
            "Do not write any final output, ledger, or result JSON in this phase."
        ),
        input_artifacts=[
            artifacts.narrative_plan,
            artifacts.evidence_cards,
            artifacts.thesis_decision,
        ],
        output_artifacts=[artifacts.draft_article],
    )
    if not outcome.ok:
        return outcome
    return outcome


def _run_review(context: PipelineContext) -> PhaseOutcome:
    runner = context.runner
    artifacts = context.artifacts
    reviews = (
        (
            "citation_logic_checker",
            artifacts.citation_logic_report,
            "Audit the draft's factual and logical integrity against the evidence cards.",
        ),
        (
            "editorial_red_team",
            artifacts.red_team_report,
            "Attack the draft's thesis with the strongest opposing case you can build.",
        ),
        (
            "qa_reviewer",
            artifacts.qa_report,
            "Review the draft against every applicable contract and the intent contract.",
        ),
        (
            "compression_editor",
            artifacts.compression_report,
            "Find repeated conclusions and methodology that belongs in the research appendix.",
        ),
    )
    for role, output, summary in reviews:
        outcome = runner.invoke_role(
            role=role,
            phase="review",
            task_instructions=(
                f"{summary}\n"
                f"Read the draft from `{artifacts.draft_article}`.\n"
                f"Read evidence cards from `{artifacts.evidence_cards}`.\n"
                f"Read the intent contract from `{artifacts.intent_contract}`.\n"
                f"Write your report to `{output}`."
            ),
            input_artifacts=[artifacts.draft_article, artifacts.evidence_cards],
            output_artifacts=[output],
        )
        if not outcome.ok:
            return outcome

    if runner.has_rendered_images():
        rendered_ledger = str(
            runner.run_contract.get("rendered_images_ledger")
            or f"{artifacts.work_run_dir}/ledgers/rendered_images.csv"
        )
        outcome = runner.invoke_role(
            role="image_slide_auditor",
            phase="review",
            task_instructions=(
                "Decide which rendered images close a real evidence gap.\n"
                f"Read the rendered image inventory from `{rendered_ledger}`.\n"
                f"Inspect each actual image before writing a caption.\n"
                f"Write every keep/drop decision to the single canonical report "
                f"`{artifacts.image_audit_report}`.\n"
                "Each kept image needs visual_question, supported_claim_ids, and why_prose_is_insufficient."
            ),
            input_artifacts=[artifacts.draft_article, rendered_ledger],
            output_artifacts=[artifacts.image_audit_report],
        )
        if not outcome.ok:
            return outcome
    return PhaseOutcome(status=0, label="review")


def _run_revise(context: PipelineContext) -> PhaseOutcome:
    runner = context.runner
    artifacts = context.artifacts
    active_reports = review_report_paths(
        artifacts,
        include_images=runner.has_rendered_images(),
    )
    reports = [path for _source_report, path in active_reports]
    report_list = "\n".join(
        f"- source_report `{source_report}`: `{path}`"
        for source_report, path in active_reports
    )
    outcome = runner.invoke_role(
        role="article_writer",
        phase="revise",
        task_instructions=(
            "Revise the draft against every review report.\n"
            f"Read the current draft from `{artifacts.draft_article}`.\n"
            f"Review reports:\n{report_list}\n"
            f"Write the revised draft back to `{artifacts.draft_article}`.\n"
            f"Write revision notes to `{artifacts.revision_notes}`.\n"
            "Every blocking issue must appear exactly once in handled_issues or "
            "unresolved_blocking_issues using the source_report and issue_id shown above."
        ),
        input_artifacts=[artifacts.draft_article, *reports],
        output_artifacts=[artifacts.draft_article, artifacts.revision_notes],
    )
    if not outcome.ok:
        return outcome
    return outcome


def _run_finalize(context: PipelineContext) -> PhaseOutcome:
    runner = context.runner
    artifacts = context.artifacts
    image_inputs: list[str] = []
    image_instruction = ""
    if runner.has_rendered_images():
        rendered_ledger = str(
            runner.run_contract.get("rendered_images_ledger")
            or f"{artifacts.work_run_dir}/ledgers/rendered_images.csv"
        )
        image_inputs = [artifacts.image_audit_report, rendered_ledger]
        image_instruction = (
            f"Read canonical image decisions from `{artifacts.image_audit_report}` and "
            f"render metadata from `{rendered_ledger}`.\n"
            "Preserve every shared decision field and caption_candidate in the final image "
            "ledger. A final caption may be derived for the frozen article context, but it "
            "must not overwrite the canonical caption_candidate.\n"
        )
    return runner.invoke_role(
        role="finalizer",
        phase="finalize",
        task_instructions=(
            "Freeze the article, then regenerate every ledger against the frozen text.\n"
            f"Read the revised draft from `{artifacts.draft_article}`.\n"
            f"Write the final article to `{artifacts.article}` with traceability comments removed.\n"
            f"Write the research appendix to `{artifacts.research_appendix}`.\n"
            f"Write the paragraph map to `{artifacts.paragraph_map}`.\n"
            f"Write the claim ledger to `{artifacts.claim_ledger}`.\n"
            f"Write the source ledger to `{artifacts.source_ledger}`.\n"
            f"Write the image ledger to `{artifacts.image_ledger}`.\n"
            f"{image_instruction}"
            "Ledgers must be written after the article is final: every claim anchor heading must be a "
            "verbatim heading of the frozen article, and article_sha256 must match the article file."
        ),
        input_artifacts=[
            artifacts.draft_article,
            artifacts.evidence_cards,
            artifacts.thesis_decision,
            artifacts.revision_notes,
            *image_inputs,
        ],
        output_artifacts=[
            artifacts.article,
            artifacts.research_appendix,
            artifacts.paragraph_map,
            artifacts.claim_ledger,
            artifacts.source_ledger,
            artifacts.image_ledger,
            artifacts.article_result,
        ],
        structured_output=True,
    )


def validate_draft_traceability(
    root: Path,
    *,
    draft: str,
    evidence_cards: str,
) -> list[str]:
    """Check that draft claim/evidence comments reference real evidence ids."""

    import re

    path = resolve_under_root(root, draft)
    if not path.is_file():
        return [f"missing draft article: {draft}"]
    text = path.read_text(encoding="utf-8")
    comments = re.findall(r"<!--\s*claims:([^|]*)\|\s*evidence:([^>]*)-->", text)
    if not comments:
        return [
            f"draft has no `<!-- claims: ... | evidence: ... -->` traceability comments: {draft}"
        ]
    rows, read_errors = read_jsonl(root, evidence_cards)
    duplicate_evidence_ids = set(
        duplicate_object_ids(rows, id_field="evidence_id")
    )
    evidence_claims: dict[str, set[str]] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        evidence_id = str(row.get("evidence_id") or "").strip()
        if not evidence_id or evidence_id in duplicate_evidence_ids:
            continue
        evidence_claims[evidence_id] = {
            str(claim_id).strip()
            for claim_id in (row.get("claim_ids") or [])
            if str(claim_id).strip()
        }
    known_evidence = set(evidence_claims)
    known_claims = {
        str(claim_id).strip()
        for row in rows
        if isinstance(row, dict)
        for claim_id in (row.get("claim_ids") or [])
        if str(claim_id).strip()
    }
    errors: list[str] = list(read_errors)
    errors.extend(
        f"evidence cards contain duplicate evidence_id: {evidence_id}"
        for evidence_id in sorted(duplicate_evidence_ids)
    )
    for claim_field, evidence_field in comments:
        claim_ids = [value.strip() for value in claim_field.split(",") if value.strip()]
        evidence_ids = [value.strip() for value in evidence_field.split(",") if value.strip()]
        if not claim_ids:
            errors.append("draft traceability comment has no claim ids")
        if not evidence_ids:
            errors.append("draft traceability comment has no evidence ids")
        for claim_id in claim_ids:
            if claim_id not in known_claims:
                errors.append(f"draft references unknown claim id: {claim_id}")
                continue
            referenced_known_evidence = [
                evidence_id for evidence_id in evidence_ids if evidence_id in known_evidence
            ]
            if referenced_known_evidence and not any(
                claim_id in evidence_claims[evidence_id]
                for evidence_id in referenced_known_evidence
            ):
                errors.append(
                    f"draft claim id {claim_id} is not supported by referenced evidence ids: "
                    + ", ".join(referenced_known_evidence)
                )
        for evidence_id in evidence_ids:
            if evidence_id in duplicate_evidence_ids:
                errors.append(
                    f"draft references ambiguous duplicate evidence id: {evidence_id}"
                )
            elif evidence_id not in known_evidence:
                errors.append(f"draft references unknown evidence id: {evidence_id}")
    return sorted(set(errors))


def _validate_research_plan_semantics(context: PipelineContext) -> list[str]:
    tree, errors = load_question_tree(context.runner.root, context.artifacts)
    if tree is None:
        return errors
    return [
        *errors,
        *validate_question_tree_semantics(
            tree,
            lane_limit=context.runner.lane_limit(),
        ),
    ]


def _validate_evidence_semantics(context: PipelineContext) -> list[str]:
    rows, errors = read_jsonl(
        context.runner.root,
        context.artifacts.evidence_cards,
    )
    errors.extend(
        f"evidence cards contain duplicate evidence_id: {evidence_id}"
        for evidence_id in duplicate_object_ids(rows, id_field="evidence_id")
    )
    return sorted(set(errors))


def _validate_evidence_cards_boundary(context: PipelineContext) -> list[str]:
    """Validate cards before any integrator consumes a verifier's output."""

    card_contract = phase_output_contracts(
        "evidence",
        context.artifacts,
        include_images=context.runner.has_rendered_images(),
    )[0]
    errors = validate_artifact_contracts(
        context.runner.root,
        (card_contract,),
        expected_run_id=context.runner.run_id,
    )
    errors.extend(_validate_evidence_semantics(context))
    return sorted(set(errors))


def _validate_evidence_integration_semantics(context: PipelineContext) -> list[str]:
    """Bind integration outputs to the question tree and the verified evidence set.

    The gap map decides whether extra research runs, so an unbound or mislabeled gap
    would let the runner spend rounds on questions the plan never asked, or silently
    skip a p0 question that is genuinely unanswered.
    """

    runner = context.runner
    artifacts = context.artifacts
    errors = _validate_evidence_semantics(context)
    tree, tree_errors = load_question_tree(runner.root, artifacts)
    errors.extend(tree_errors)
    if tree is None:
        return sorted(set(errors))

    importance_by_question = {
        str(question.get("question_id") or "").strip(): str(
            question.get("importance") or ""
        ).strip()
        for question in (tree.get("questions") or [])
        if isinstance(question, dict) and str(question.get("question_id") or "").strip()
    }
    cards, card_errors = read_jsonl(runner.root, artifacts.evidence_cards)
    errors.extend(card_errors)
    known_evidence = {
        str(card.get("evidence_id") or "").strip()
        for card in cards
        if isinstance(card, dict) and str(card.get("evidence_id") or "").strip()
    }

    gap_map = _read_json(runner.root, artifacts.evidence_gap_map)
    gaps = (gap_map or {}).get("gaps") or [] if isinstance(gap_map, dict) else []
    seen_gap_ids: set[str] = set()
    conflict_gap_questions: set[str] = set()
    for index, gap in enumerate(gaps, start=1):
        if not isinstance(gap, dict):
            continue
        gap_id = str(gap.get("gap_id") or f"row_{index}").strip()
        if gap_id in seen_gap_ids:
            errors.append(f"evidence gap map contains duplicate gap_id: {gap_id}")
        seen_gap_ids.add(gap_id)
        question_id = str(gap.get("question_id") or "").strip()
        if not question_id:
            errors.append(f"evidence gap {gap_id} is not bound to a question_id")
            continue
        expected = importance_by_question.get(question_id)
        if expected is None:
            errors.append(
                f"evidence gap {gap_id} references unknown question_id: {question_id}"
            )
            continue
        importance = str(gap.get("importance") or "").strip()
        if expected and importance != expected:
            errors.append(
                f"evidence gap {gap_id} importance `{importance}` does not match "
                f"question {question_id} importance `{expected}`"
            )
        if str(gap.get("gap_kind") or "").strip() == "unresolved_conflict":
            conflict_gap_questions.add(question_id)

    ledger = _read_json(runner.root, artifacts.contradiction_ledger)
    contradictions = (
        (ledger or {}).get("contradictions") or [] if isinstance(ledger, dict) else []
    )
    seen_conflict_ids: set[str] = set()
    for index, conflict in enumerate(contradictions, start=1):
        if not isinstance(conflict, dict):
            continue
        conflict_id = str(conflict.get("contradiction_id") or f"row_{index}").strip()
        if conflict_id in seen_conflict_ids:
            errors.append(
                f"contradiction ledger contains duplicate contradiction_id: {conflict_id}"
            )
        seen_conflict_ids.add(conflict_id)
        question_id = str(conflict.get("question_id") or "").strip()
        if not question_id:
            errors.append(f"contradiction {conflict_id} is not bound to a question_id")
        elif question_id not in importance_by_question:
            errors.append(
                f"contradiction {conflict_id} references unknown question_id: {question_id}"
            )
        for evidence_id in conflict.get("evidence_ids") or []:
            normalized = str(evidence_id).strip()
            if normalized and normalized not in known_evidence:
                errors.append(
                    f"contradiction {conflict_id} references unknown evidence_id: {normalized}"
                )
        if (
            str(conflict.get("resolution") or "").strip() == "unresolved"
            and question_id
            and importance_by_question.get(question_id) == "p0"
            and question_id not in conflict_gap_questions
        ):
            errors.append(
                f"unresolved p0 contradiction {conflict_id} is not mirrored into the "
                "evidence gap map as an unresolved_conflict gap"
            )
    return sorted(set(errors))


def _validate_draft_semantics(context: PipelineContext) -> list[str]:
    return validate_draft_traceability(
        context.runner.root,
        draft=context.artifacts.draft_article,
        evidence_cards=context.artifacts.evidence_cards,
    )


def _validate_review_semantics(context: PipelineContext) -> list[str]:
    rendered_ledger = str(
        context.runner.run_contract.get("rendered_images_ledger") or ""
    )
    return sorted(
        set(
            [
                *validate_review_reports(
                    context.runner.root,
                    context.artifacts,
                    include_images=context.runner.has_rendered_images(),
                    expected_run_id=context.runner.run_id,
                ),
                *validate_image_audit_report(
                    root=context.runner.root,
                    run_id=context.runner.run_id,
                    artifacts=context.artifacts,
                    rendered_images_ledger=rendered_ledger or None,
                ),
            ]
        )
    )


def _validate_revise_semantics(context: PipelineContext) -> list[str]:
    return validate_revision_coverage(
        context.runner.root,
        context.artifacts,
        include_images=context.runner.has_rendered_images(),
        expected_run_id=context.runner.run_id,
        # Review semantics run immediately after review production and again at a
        # resume boundary. Revise validates only notes identity and issue coverage.
        validate_report_contract=False,
    )


def _validate_finalize_semantics(context: PipelineContext) -> list[str]:
    rendered_ledger = str(
        context.runner.run_contract.get("rendered_images_ledger") or ""
    )
    return validate_final_image_ledger(
        root=context.runner.root,
        run_id=context.runner.run_id,
        artifacts=context.artifacts,
        rendered_images_ledger=rendered_ledger or None,
    )


PHASE_SEMANTIC_VALIDATORS = {
    "research-plan": PhaseSemanticContract(
        validator=_validate_research_plan_semantics,
        producer_failure_label="research_plan_semantic_fail",
    ),
    "evidence": PhaseSemanticContract(
        validator=_validate_evidence_integration_semantics,
        producer_failure_label="evidence_semantic_fail",
    ),
    "draft": PhaseSemanticContract(
        validator=_validate_draft_semantics,
        producer_failure_label="draft_traceability_fail",
    ),
    "review": PhaseSemanticContract(
        validator=_validate_review_semantics,
        producer_failure_label="review_semantic_fail",
    ),
    "revise": PhaseSemanticContract(
        validator=_validate_revise_semantics,
        producer_failure_label="revise_unresolved_blocking",
    ),
    "finalize": PhaseSemanticContract(
        validator=_validate_finalize_semantics,
        producer_failure_label="finalize_image_authority_fail",
    ),
}


def _read_json(root: Path, rel: str) -> Any:
    try:
        path = resolve_under_root(root, rel)
    except ValueError:
        return None
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None


_HANDLERS = {
    "scope": _run_scope,
    "research-plan": _run_research_plan,
    "research": _run_research,
    "evidence": _run_evidence,
    "narrative": _run_narrative,
    "draft": _run_draft,
    "review": _run_review,
    "revise": _run_revise,
    "finalize": _run_finalize,
}
