"""Runner-driven phase orchestration.

Each phase resolves its upstream requirements, compiles a role-specific prompt, invokes
`codex exec` for that role alone, records an attempt envelope, then validates the artifacts
the phase is contractually required to produce. A phase that cannot satisfy its contract
stops the run instead of letting later phases inherit an unverified state.
"""

from __future__ import annotations

import csv
import json
import sys
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Callable, Sequence

from .agent_attempts import AgentAttempt, finish_attempt, hash_artifacts, start_attempt
from .artifact_validation import read_jsonl, validate_artifact_contracts
from .codex_exec import (
    CodexExecConfig,
    RoleProcessCleanupError,
    build_role_command,
    make_role_write_snapshot,
    prepare_role_invocation,
    regular_file_signature,
    restore_latest_tree,
    snapshot_latest_tree,
    validate_latest_tree,
    validate_role_outputs,
    validate_role_write_snapshot,
    validate_runner_side_effects,
)
from .intent_contract import (
    apply_scope_approval,
    evaluate_scope_gate,
    has_unapproved_material_delta,
    read_intent_contract,
    read_intent_contract_unvalidated,
    write_intent_contract,
)
from .paths import as_posix_arg, resolve_under_root
from .phase_contracts import (
    DEPTH_LANE_LIMITS,
    PHASE_UPSTREAM_REQUIREMENTS,
    RunArtifacts,
    phase_input_contracts,
    phase_output_contracts,
)
from .phase_provenance import rendered_images_available
from .source_ingest_paths import build_source_ingest_paths
from .prompt_compiler import compile_prompt, write_compiled_prompt
from .roles import RoleSpec, role_spec


@dataclass(frozen=True)
class PhaseOutcome:
    status: int
    label: str
    messages: tuple[str, ...] = ()

    @property
    def ok(self) -> bool:
        return self.status == 0


@dataclass(frozen=True)
class PhaseRunner:
    """Executes one role invocation and validates its artifacts."""

    root: Path
    run_id: str
    mode: str
    artifacts: RunArtifacts
    exec_config: CodexExecConfig
    run_contract: dict[str, Any]
    run_codex: Callable[[CodexExecConfig, Sequence[str]], int]
    depth: str = "standard"
    include_images: bool = False
    max_image_inputs: int = 20
    dry_run: bool = False

    def invoke_role(
        self,
        *,
        role: str,
        phase: str,
        task_instructions: str,
        input_artifacts: Sequence[str],
        output_artifacts: Sequence[str],
        lane_id: str | None = None,
        structured_output: bool | None = None,
    ) -> PhaseOutcome:
        spec = role_spec(role)
        compiled = compile_prompt(
            root=self.root,
            spec=spec,
            mode=self.mode,
            phase=phase,
            run_contract=self.run_contract,
            task_instructions=task_instructions,
            lane_id=lane_id,
        )
        write_compiled_prompt(self.root, compiled)
        config = self._role_config(
            spec,
            phase,
            compiled.path,
            structured_output,
            output_artifacts,
        )
        command = build_role_command(config)
        if self.dry_run:
            return PhaseOutcome(status=0, label=f"{phase}:{role}:dry_run")

        preflight_errors = prepare_role_invocation(config)
        latest_snapshot = None
        try:
            latest_snapshot = snapshot_latest_tree(config)
        except (OSError, ValueError) as exc:
            preflight_errors.append(str(exc))

        attempt, attempt_rel = start_attempt(
            root=self.root,
            agents_dir=self.artifacts.agents_dir,
            run_id=self.run_id,
            agent_name=role,
            phase=phase,
            agent_prompt_sha256=compiled.sha256,
            effective_prompt_path=compiled.path,
            input_artifact_hashes=hash_artifacts(
                self.root,
                self._declared_inputs(role, phase, input_artifacts),
            ),
            output_artifacts=output_artifacts,
            model=spec.model,
            model_reasoning_effort=spec.reasoning_effort,
            command=command,
            lane_id=lane_id,
        )
        if preflight_errors:
            record_errors, record_interrupt = self._finish_attempt_errors(
                rel=attempt_rel,
                attempt=attempt,
                exit_code=2,
                errors=preflight_errors,
            )
            preflight_errors.extend(record_errors)
            if record_interrupt is not None and not record_errors:
                raise record_interrupt.with_traceback(record_interrupt.__traceback__)
            return PhaseOutcome(
                status=2,
                label=f"{phase}_role_attempt_record_fail"
                if record_errors
                else f"{phase}_role_boundary_preflight_fail",
                messages=tuple(preflight_errors),
            )

        # Snapshot outputs before the call so a leftover artifact from an earlier
        # attempt cannot be mistaken for work this role actually produced.
        before: dict[str, tuple[str | None, int | None]] = {}
        role_snapshot = None
        exit_code = 2
        caught: BaseException | None = None
        process_cleanup_failed = False
        cleanup_exceptions: list[BaseException] = []
        errors: list[str] = []
        scope_errors: list[str] = []
        output_type_errors: list[str] = []
        freshness_errors: list[str] = []
        runner_side_effect_errors: list[str] = []
        latest_errors: list[str] = []
        restore_errors: list[str] = []
        attempt_record_errors: list[str] = []
        sigterm_guard_retained = False

        def cleanup_errors(label: str, callback: Callable[[], list[str]]) -> list[str]:
            try:
                return callback()
            except BaseException as exc:
                cleanup_exceptions.append(exc)
                return [
                    f"{label} raised {type(exc).__name__}"
                    + (f": {exc}" if str(exc) else "")
                ]

        try:
            # Keep the role-only SIGTERM latch active until this invocation's boundary,
            # latest restore, and atomic attempt envelope have all settled. Direct
            # run_command(RoleCommand) calls continue to own their shorter guard lifetime.
            sigterm_guard_retained = True
            command.retain_sigterm_guard()
            before = self._output_signatures(output_artifacts)
            role_snapshot = make_role_write_snapshot(config)
            if command.sigterm_returncode is not None:
                exit_code = command.sigterm_returncode
            else:
                exit_code = self.run_codex(config, command)
        except BaseException as exc:
            caught = exc
            process_cleanup_failed = isinstance(exc, RoleProcessCleanupError)
            errors.append(
                f"{role} raised {type(exc).__name__}"
                + (f": {exc}" if str(exc) else "")
            )
        finally:
            if process_cleanup_failed:
                errors.append(
                    "role boundary validation and outputs/latest restore skipped because "
                    "process-group cleanup could not prove that all descendant writers "
                    "stopped; repository state may still be changing"
                )
            else:
                if role_snapshot is not None:
                    scope_errors = cleanup_errors(
                        "role write-scope validation",
                        lambda: validate_role_write_snapshot(role_snapshot),
                    )
                    errors.extend(scope_errors)
                    output_type_errors = cleanup_errors(
                        "role output type validation",
                        lambda: validate_role_outputs(
                            config,
                            require_present=caught is None and exit_code == 0,
                        ),
                    )
                    errors.extend(output_type_errors)
                    runner_side_effect_errors = cleanup_errors(
                        "runner log validation",
                        lambda: validate_runner_side_effects(role_snapshot),
                    )
                    errors.extend(runner_side_effect_errors)
                    freshness_errors = cleanup_errors(
                        "role output freshness validation",
                        lambda: self._output_freshness_errors(output_artifacts, before),
                    )
                    errors.extend(freshness_errors)
                    if caught is None and exit_code != 0:
                        errors.append(f"{role} exited with status {exit_code}")

                assert latest_snapshot is not None
                latest_errors = cleanup_errors(
                    "outputs/latest validation",
                    lambda: validate_latest_tree(config, latest_snapshot),
                )
                errors.extend(latest_errors)
                if latest_errors:
                    restore_errors.extend(
                        cleanup_errors(
                            "outputs/latest restore",
                            lambda: restore_latest_tree(config, latest_snapshot),
                        )
                    )
                    errors.extend(restore_errors)

            # Finalize the attempt only after process cleanup, the single boundary pass,
            # and any required latest restore plus verification have all completed.
            released_sigterm: int | None = None
            try:
                attempt_exit_code = exit_code if caught is None else 2
                if not process_cleanup_failed and command.sigterm_returncode is not None:
                    attempt_exit_code = command.sigterm_returncode
                    exit_code = attempt_exit_code

                record_errors, record_interrupt = self._finish_attempt_errors(
                    rel=attempt_rel,
                    attempt=attempt,
                    exit_code=attempt_exit_code,
                    errors=errors,
                )
                attempt_record_errors.extend(record_errors)
                if record_interrupt is not None:
                    cleanup_exceptions.append(record_interrupt)

                # A first SIGTERM can land inside the atomic attempt write. The latch is
                # monotonic, so one corrective replacement records 143; later signals are
                # ignored until the exact previous handler is restored below.
                settled_sigterm = command.sigterm_returncode
                if (
                    not process_cleanup_failed
                    and not record_errors
                    and settled_sigterm is not None
                    and attempt_exit_code != settled_sigterm
                ):
                    exit_code = settled_sigterm
                    correction_errors, correction_interrupt = self._finish_attempt_errors(
                        rel=attempt_rel,
                        attempt=attempt,
                        exit_code=settled_sigterm,
                        errors=errors,
                    )
                    attempt_record_errors.extend(correction_errors)
                    if correction_interrupt is not None:
                        cleanup_exceptions.append(correction_interrupt)
            finally:
                if sigterm_guard_retained:
                    released_sigterm = command.release_sigterm_guard()

            # The guard restoration is the exact invocation cutoff. If the first SIGTERM
            # landed after the prior settlement check but before that restoration, preserve
            # its termination meaning with one final atomic envelope replacement.
            if (
                not process_cleanup_failed
                and not attempt_record_errors
                and released_sigterm is not None
                and attempt_exit_code != released_sigterm
            ):
                exit_code = released_sigterm
                release_errors, release_interrupt = self._finish_attempt_errors(
                    rel=attempt_rel,
                    attempt=attempt,
                    exit_code=released_sigterm,
                    errors=errors,
                )
                attempt_record_errors.extend(release_errors)
                if release_interrupt is not None:
                    cleanup_exceptions.append(release_interrupt)
            errors.extend(attempt_record_errors)

        if process_cleanup_failed:
            return PhaseOutcome(
                status=2,
                label=f"{phase}_role_process_cleanup_fail",
                messages=tuple(errors),
            )
        if restore_errors:
            return PhaseOutcome(
                status=2,
                label=f"{phase}_role_latest_restore_fail",
                messages=tuple(errors),
            )
        if attempt_record_errors:
            return PhaseOutcome(
                status=2,
                label=f"{phase}_role_attempt_record_fail",
                messages=tuple(errors),
            )
        if (
            scope_errors
            or output_type_errors
            or runner_side_effect_errors
            or latest_errors
        ):
            return PhaseOutcome(
                status=2,
                label=f"{phase}_role_write_scope_violation",
                messages=tuple(errors),
            )
        if caught is None and exit_code != 0:
            return PhaseOutcome(
                status=exit_code,
                label=f"{phase}_codex_fail",
                messages=tuple(errors),
            )
        if freshness_errors:
            label = (
                f"{phase}_artifact_missing"
                if all(
                    error.startswith("missing expected artifact")
                    for error in freshness_errors
                )
                else f"{phase}_artifact_stale"
            )
            return PhaseOutcome(status=2, label=label, messages=tuple(errors))
        if caught is not None:
            raise caught.with_traceback(caught.__traceback__)
        if cleanup_exceptions:
            cleanup_exception = cleanup_exceptions[0]
            raise cleanup_exception.with_traceback(cleanup_exception.__traceback__)
        if errors:
            label = (
                f"{phase}_artifact_missing"
                if all(error.startswith("missing expected artifact") for error in errors)
                else f"{phase}_artifact_stale"
            )
            return PhaseOutcome(status=2, label=label, messages=tuple(errors))
        return PhaseOutcome(status=0, label=f"{phase}:{role}")

    def _finish_attempt_errors(
        self,
        *,
        rel: str,
        attempt: AgentAttempt,
        exit_code: int,
        errors: Sequence[str],
    ) -> tuple[list[str], BaseException | None]:
        try:
            finish_attempt(
                root=self.root,
                rel=rel,
                attempt=attempt,
                exit_code=exit_code,
                errors=errors,
            )
        except BaseException as exc:
            message = (
                f"role attempt finalization raised {type(exc).__name__}"
                + (f": {exc}" if str(exc) else "")
            )
            if isinstance(exc, Exception):
                return [f"failed to record completed role attempt: {exc}"], None
            # A cancellation or keyboard interrupt can land during the atomic write.
            # Retry once with a failed envelope, then propagate only after the caller has
            # completed its outputs/latest cleanup.
            try:
                finish_attempt(
                    root=self.root,
                    rel=rel,
                    attempt=attempt,
                    exit_code=2,
                    errors=(*errors, message),
                )
            except BaseException as retry_exc:
                return (
                    [
                        message,
                        "failed to record completed role attempt after interruption: "
                        f"{type(retry_exc).__name__}"
                        + (f": {retry_exc}" if str(retry_exc) else ""),
                    ],
                    exc,
                )
            return [], exc
        return [], None

    def _declared_inputs(
        self,
        role: str,
        phase: str,
        input_artifacts: Sequence[str],
    ) -> tuple[str, ...]:
        """Merge the caller's inputs with the phase and role input contract.

        The caller knows lane-specific inputs the contract cannot name, and the contract knows
        the shared inputs a call site can forget. Hashing the union keeps a role's attempt
        envelope and the provenance checkpoint working from one definition of what was read.
        """

        contract = self.run_contract
        merged: list[str] = []
        for rel in (
            *input_artifacts,
            *(
                item.path
                for item in phase_input_contracts(
                    phase,
                    self.artifacts,
                    topic_path=str(contract.get("topic_path") or ""),
                    sources_path=str(contract.get("sources_path") or ""),
                    source_candidate_ledger=str(
                        contract.get("source_candidate_ledger") or ""
                    ),
                    role=role,
                    rendered_images_ledger=str(contract.get("rendered_images_ledger") or ""),
                    image_inputs=self._current_image_inputs()
                    if role == "image_slide_auditor"
                    else (),
                    extracted_text_paths=self._extracted_text_paths()
                    if role == "evidence_verifier"
                    else (),
                    include_images=self.has_rendered_images(),
                )
            ),
        ):
            if rel and rel not in merged:
                merged.append(rel)
        return tuple(merged)

    def _extracted_text_paths(self) -> tuple[str, ...]:
        """Resolve the extracted source text the ledger points at."""

        # Resolve from the run directory rather than the flag-gated run contract: with
        # `--no-source-ingest` the contract omits the ledger, and reading it from there would
        # silently drop these inputs for a run whose ledger is present on disk.
        ledger_rel = build_source_ingest_paths(
            self.artifacts.work_run_dir
        ).source_candidate_ledger
        try:
            ledger_path = resolve_under_root(self.root, ledger_rel)
        except ValueError:
            return ()
        if not ledger_path.is_file():
            return ()
        values: list[str] = []
        with ledger_path.open(newline="", encoding="utf-8") as handle:
            for row in csv.DictReader(handle):
                text_path = (row.get("extracted_text_path") or "").strip()
                if text_path and text_path not in values:
                    values.append(text_path)
        return tuple(values)

    def _output_freshness_errors(
        self,
        output_artifacts: Sequence[str],
        before: dict[str, tuple[str | None, int | None]],
    ) -> list[str]:
        """Report outputs this attempt did not create or rewrite.

        Existence alone cannot prove a role ran: a no-op invocation that leaves a
        previous attempt's file in place would otherwise look successful. Freshness is
        therefore "was this file written", tracked as content hash plus modification
        time. A role may legitimately rewrite its own input with identical bytes, such
        as the scope guardian re-affirming an aligned contract or the revise-phase
        writer keeping a draft, so identical content with a new mtime still counts.
        """

        errors: list[str] = []
        after = self._output_signatures(output_artifacts)
        for rel in output_artifacts:
            after_signature = after.get(rel, (None, None))
            if after_signature[0] is None:
                errors.append(f"missing expected artifact: {rel}")
                continue
            before_signature = before.get(rel, (None, None))
            if before_signature[0] is not None and before_signature == after_signature:
                errors.append(
                    f"stale expected artifact: {rel} was not written by this attempt; "
                    "it is unchanged from the artifact that existed before the role ran"
                )
        return errors

    def _output_signatures(
        self,
        output_artifacts: Sequence[str],
    ) -> dict[str, tuple[str | None, int | None]]:
        """Pair each output's content hash with its modification time."""

        signatures: dict[str, tuple[str | None, int | None]] = {}
        for rel in output_artifacts:
            signatures[rel] = regular_file_signature(self.root, rel)
        return signatures

    def _role_config(
        self,
        spec: RoleSpec,
        phase: str,
        prompt_path: str,
        structured_output: bool | None,
        output_artifacts: Sequence[str],
    ) -> CodexExecConfig:
        use_structured = spec.structured_output if structured_output is None else structured_output
        config = replace(
            self.exec_config,
            phase=phase,
            role=spec.name,
            role_model=spec.model,
            role_reasoning_effort=spec.reasoning_effort,
            role_prompt_path=prompt_path,
            structured_output=use_structured,
            prompt=resolve_under_root(self.root, prompt_path).read_text(encoding="utf-8"),
            image_inputs=self._current_image_inputs()
            if spec.name == "image_slide_auditor"
            else (),
            role_output_artifacts=tuple(output_artifacts),
        )
        return config

    def _current_image_inputs(self) -> tuple[str, ...]:
        """Refresh rendered images after research candidate ingest added new PDFs."""

        values: list[str] = []
        for value in self.exec_config.image_inputs:
            if value not in values:
                values.append(value)
        ledger_rel = str(self.run_contract.get("rendered_images_ledger") or "")
        if ledger_rel:
            try:
                ledger_path = resolve_under_root(self.root, ledger_rel)
            except ValueError:
                ledger_path = None
            if ledger_path is not None and ledger_path.is_file():
                with ledger_path.open(newline="", encoding="utf-8") as handle:
                    for row in csv.DictReader(handle):
                        image_path = (row.get("image_path") or "").strip()
                        if (row.get("status") or "").strip() != "ok" or not image_path:
                            continue
                        try:
                            exists = resolve_under_root(self.root, image_path).is_file()
                        except ValueError:
                            exists = False
                        if exists and image_path not in values:
                            values.append(image_path)
                        if len(values) >= self.max_image_inputs:
                            break
        return tuple(values[: self.max_image_inputs])

    def has_rendered_images(self) -> bool:
        """Return true only when the current run has an inspectable rendered image."""

        # Resolve from the run directory, not the flag-gated run contract. With
        # `--no-source-ingest` the contract omits the ledger, and answering from there would
        # disagree with the workflow-level provenance context for the very same run.
        ledger_rel = build_source_ingest_paths(
            self.artifacts.work_run_dir
        ).rendered_images_ledger
        return rendered_images_available(
            self.root,
            ledger_rel,
            fallback=self.include_images,
        )

    def validate_phase(self, phase: str) -> list[str]:
        contracts = phase_output_contracts(
            phase,
            self.artifacts,
            include_images=self.has_rendered_images(),
        )
        return validate_artifact_contracts(
            self.root,
            contracts,
            expected_run_id=self.run_id,
        )

    def lane_limit(self) -> int:
        return DEPTH_LANE_LIMITS.get(self.depth, DEPTH_LANE_LIMITS["standard"])


def missing_upstream_artifacts(
    root: Path,
    phase: str,
    artifacts: RunArtifacts,
    *,
    include_images: bool = False,
) -> list[str]:
    """Report upstream artifacts a phase requires but cannot find."""

    missing: list[str] = []
    for upstream in PHASE_UPSTREAM_REQUIREMENTS.get(phase, ()):
        for contract in phase_output_contracts(upstream, artifacts, include_images=include_images):
            if not resolve_under_root(root, contract.path).exists():
                missing.append(f"{upstream} phase artifact is missing: {contract.path}")
    return missing


def invalid_upstream_artifacts(
    root: Path,
    phase: str,
    artifacts: RunArtifacts,
    *,
    include_images: bool = False,
    validate_schemas: bool = True,
    expected_run_id: str | None = None,
) -> list[str]:
    """Validate upstream artifacts a phase consumes, not just their existence.

    Resumed runs such as `--phase draft` inherit artifacts this invocation never
    produced, so corrupted or hand-edited upstream files would otherwise reach a role
    unchecked. Callers apply this to the first phase they execute; later phases in the
    same run were already validated when they wrote their own outputs.
    """

    errors: list[str] = []
    for upstream in PHASE_UPSTREAM_REQUIREMENTS.get(phase, ()):
        contracts = phase_output_contracts(upstream, artifacts, include_images=include_images)
        for error in validate_artifact_contracts(
            root,
            contracts,
            validate_schemas=validate_schemas,
            expected_run_id=expected_run_id,
        ):
            errors.append(f"{upstream} phase artifact is unusable: {error}")
    return errors


def check_scope_gate(
    root: Path,
    artifacts: RunArtifacts,
    *,
    approve: bool,
    validate_schema: bool = True,
) -> tuple[bool, list[str]]:
    """Read the intent contract and decide whether drafting may proceed."""

    if validate_schema:
        payload, errors = read_intent_contract(root, artifacts.intent_contract)
    else:
        payload = read_intent_contract_unvalidated(root, artifacts.intent_contract)
        errors = [] if payload is not None else [f"invalid intent contract: {artifacts.intent_contract}"]
    if payload is None:
        return False, errors or [f"missing intent contract: {artifacts.intent_contract}"]
    if errors:
        return False, errors

    # A CLI approval is only informed about the deltas that already existed when the
    # user passed the flag. Once the guardian records a new material change, that
    # approval cannot cover it, so never auto-approve it here.
    if approve and has_unapproved_material_delta(payload):
        approve = False
    updated = apply_scope_approval(payload, approved=True) if approve else payload
    if updated is not payload:
        write_intent_contract(root, artifacts.intent_contract, updated)
    gate = evaluate_scope_gate(updated)
    return gate.allowed, list(gate.messages)


def load_question_tree(root: Path, artifacts: RunArtifacts) -> tuple[dict[str, Any] | None, list[str]]:
    path = resolve_under_root(root, artifacts.question_tree)
    if not path.is_file():
        return None, [f"missing research question tree: {artifacts.question_tree}"]
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        return None, [f"invalid research question tree JSON: {exc.msg}"]
    if not isinstance(payload, dict):
        return None, ["research question tree is not a JSON object"]
    return payload, []


def resolve_lanes(tree: dict[str, Any]) -> list[dict[str, Any]]:
    """Return every planned lane after the semantic gate has accepted the tree."""

    return [lane for lane in (tree.get("lanes") or []) if isinstance(lane, dict)]


def _duplicate_ids(rows: list[Any], *, field: str) -> tuple[str, ...]:
    seen: set[str] = set()
    duplicates: set[str] = set()
    for row in rows:
        if not isinstance(row, dict):
            continue
        value = str(row.get(field) or "").strip()
        if not value:
            continue
        if value in seen:
            duplicates.add(value)
        seen.add(value)
    return tuple(sorted(duplicates))


def validate_question_tree_semantics(
    tree: dict[str, Any],
    *,
    lane_limit: int,
) -> list[str]:
    """Validate lane capacity, stable identities, and question references."""

    questions = list(tree.get("questions") or [])
    lanes = list(tree.get("lanes") or [])
    errors = [
        f"research question tree contains duplicate question_id: {question_id}"
        for question_id in _duplicate_ids(questions, field="question_id")
    ]
    errors.extend(
        f"research question tree contains duplicate lane_id: {lane_id}"
        for lane_id in _duplicate_ids(lanes, field="lane_id")
    )
    if len(lanes) > lane_limit:
        errors.append(
            f"research question tree defines {len(lanes)} lanes, "
            f"but this depth permits at most {lane_limit}"
        )

    known_questions = {
        str(question.get("question_id") or "").strip()
        for question in questions
        if isinstance(question, dict) and str(question.get("question_id") or "").strip()
    }
    assigned_questions: set[str] = set()
    for index, lane in enumerate(lanes, start=1):
        if not isinstance(lane, dict):
            continue
        lane_id = str(lane.get("lane_id") or "").strip() or f"lane_{index}"
        for question_id in lane.get("question_ids") or []:
            normalized = str(question_id).strip()
            if normalized in known_questions:
                assigned_questions.add(normalized)
            if normalized and normalized not in known_questions:
                errors.append(
                    f"research lane {lane_id} references unknown question_id: {normalized}"
                )
    errors.extend(
        f"research question tree question is not assigned to any lane: {question_id}"
        for question_id in sorted(known_questions - assigned_questions)
    )
    return sorted(set(errors))


def merge_lane_candidates(
    root: Path,
    *,
    target: str,
    sources: Sequence[str],
) -> list[str]:
    """Concatenate the given lane JSONL outputs into one merged candidate file.

    Sources are the lane paths this invocation actually produced. Globbing the lanes
    directory instead would re-merge lanes a superseded research plan created, letting
    dropped lanes leak back into the evidence phase.
    """

    resolved_sources: list[tuple[str, Path]] = []
    errors: list[str] = []
    seen_paths: dict[Path, str] = {}
    for rel in sources:
        try:
            path = resolve_under_root(root, rel)
        except ValueError as exc:
            errors.append(str(exc))
            continue
        prior = seen_paths.get(path)
        if prior is not None:
            errors.append(
                f"duplicate lane output path in merge: {rel} (already included as {prior})"
            )
            continue
        seen_paths[path] = rel
        resolved_sources.append((rel, path))
    if errors:
        return errors

    rows: list[str] = []
    for rel, path in resolved_sources:
        if not path.is_file():
            errors.append(f"missing expected lane output: {rel}")
            continue
        parsed, parse_errors = read_jsonl(root, rel)
        errors.extend(parse_errors)
        for row in parsed:
            rows.append(json.dumps(row, ensure_ascii=False, sort_keys=True))
    merged = resolve_under_root(root, target)
    merged.parent.mkdir(parents=True, exist_ok=True)
    merged.write_text("\n".join(rows) + ("\n" if rows else ""), encoding="utf-8")
    return errors


def print_phase_failure(label: str, messages: Sequence[str]) -> None:
    print(f"{label}:", file=sys.stderr)
    for message in messages:
        print(f" - {message}", file=sys.stderr)


def lane_artifact_paths(artifacts: RunArtifacts, lane_id: str, *, counter: bool = False) -> str:
    suffix = "counter" if counter else "primary"
    return f"{artifacts.lanes_dir}/lane_{lane_id}.{suffix}.jsonl"


def extra_research_lane_id(round_number: int) -> str:
    return f"__extra_research_round_{round_number:03d}"


def extra_research_lane_path(artifacts: RunArtifacts, round_number: int) -> str:
    return f"{artifacts.lanes_dir}/extra/round_{round_number:03d}.primary.jsonl"


def as_rel(value: str) -> str:
    return as_posix_arg(value)
