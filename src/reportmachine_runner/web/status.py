"""Read-only run status assembled without equating process state with article validity."""

from __future__ import annotations

import json
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Protocol

from ..codex_exec import LATEST_OPTIONAL_OUTPUTS, LATEST_REQUIRED_OUTPUTS
from ..final_sync import final_sync_artifact_hashes
from ..intent_contract import scope_delta_fingerprint
from ..paths import resolve_under_root, validate_run_id
from ..phase_contracts import build_run_artifacts
from ..phase_provenance import (
    ProvenanceContext,
    provenance_marker_state,
    rendered_images_available,
    validate_checkpoint,
)
from ..schema_validation import load_schema_validator
from .artifacts import ArtifactBroker, ArtifactError, ArtifactName, _read_regular_under
from .state import TERMINAL_STATES, ExecutionReceipt


class _ReceiptReader(Protocol):
    def current_receipt(self) -> ExecutionReceipt | None: ...


class _OwnerReader(Protocol):
    def read_safely(self) -> tuple[dict[str, object] | None, str | None]: ...


@dataclass(frozen=True)
class RunStatus:
    run_id: str
    operation: dict[str, object]
    writer: dict[str, object]
    article: dict[str, object]
    artifacts: dict[str, object]
    latest: dict[str, object]

    def to_mapping(self) -> dict[str, object]:
        return {
            "run_id": self.run_id,
            "operation": self.operation,
            "writer": self.writer,
            "article": self.article,
            "artifacts": self.artifacts,
            "latest": self.latest,
        }


class StatusReader:
    """Read status only after no Web operation or core writer can be mutating a run."""

    def __init__(
        self,
        project_root: Path,
        *,
        supervisor: _ReceiptReader | None = None,
        owner_adapter: _OwnerReader | None = None,
        broker: ArtifactBroker | None = None,
    ) -> None:
        self.project_root = Path(project_root).expanduser().resolve()
        self.supervisor = supervisor
        self.owner_adapter = owner_adapter
        self.broker = broker or ArtifactBroker(self.project_root)

    def read(self, run_id: str) -> RunStatus:
        try:
            checked_run_id = validate_run_id(run_id, "run_id")
        except (TypeError, ValueError) as exc:
            raise ValueError(str(exc)) from exc
        operation = self._operation()
        # Never let a polling reader win the non-blocking writer lock before a freshly
        # spawned Web child. Active Web receipts are rejected without touching that lock.
        if operation["state"] in {"queued", "running", "cancelling"} or (
            operation["state"] == "interrupted_unknown"
            and bool(operation.get("process_may_be_live"))
        ):
            return self._not_checked(checked_run_id, operation, self._writer())

        writer_before = self._writer()
        if not writer_before.get("stable_for_inspection", False):
            return self._not_checked(checked_run_id, operation, writer_before)

        article, artifacts = self._read_terminal_artifacts(checked_run_id)
        latest = self._latest_membership(checked_run_id)
        writer_after = self._writer()
        if not self._same_writer_snapshot(writer_before, writer_after):
            return self._not_checked(checked_run_id, operation, writer_after)

        # A second identical snapshot catches a direct CLI start and also guards recovery
        # inspection from catchable orphan activity, without ever taking the writer lock.
        second_article, second_artifacts = self._read_terminal_artifacts(checked_run_id)
        second_latest = self._latest_membership(checked_run_id)
        writer_final = self._writer()
        if (
            not self._same_writer_snapshot(writer_after, writer_final)
            or article != second_article
            or artifacts != second_artifacts
            or latest != second_latest
        ):
            return self._not_checked(checked_run_id, operation, writer_final)
        article, artifacts, latest, writer_after = (
            second_article,
            second_artifacts,
            second_latest,
            writer_final,
        )

        operation_final = self._operation()
        if not self._same_operation_snapshot(operation, operation_final):
            return self._not_checked(checked_run_id, operation_final, self._writer())
        operation = operation_final
        self._attach_operation_relationship(article, operation, checked_run_id)
        return RunStatus(
            checked_run_id,
            operation,
            writer_after,
            article,
            artifacts,
            latest,
        )

    @staticmethod
    def _not_checked(
        run_id: str,
        operation: dict[str, object],
        writer: dict[str, object],
    ) -> RunStatus:
        return RunStatus(
            run_id,
            operation,
            writer,
            {"state": "not_checked", "reason": "writer_or_operation_active"},
            {"state": "not_checked", "reason": "writer_or_operation_active"},
            {"member": None},
        )

    @staticmethod
    def _same_writer_snapshot(
        before: dict[str, object],
        after: dict[str, object],
    ) -> bool:
        return (
            before.get("state") == after.get("state")
            and before.get("owner") == after.get("owner")
            and before.get("stable_for_inspection")
            == after.get("stable_for_inspection")
        )

    @staticmethod
    def _same_operation_snapshot(
        before: dict[str, object],
        after: dict[str, object],
    ) -> bool:
        return all(
            before.get(key) == after.get(key)
            for key in ("state", "execution_id", "receipt_run_id", "updated_at")
        )

    get = read

    def _operation(self) -> dict[str, object]:
        if self.supervisor is None:
            return {"state": "idle", "receipt_state": None, "execution_id": None}
        try:
            receipt = self.supervisor.current_receipt()
        except Exception as exc:  # A status page must fail closed if durable state is unreadable.
            return {"state": "unavailable", "receipt_state": None, "execution_id": None, "error": str(exc)}
        if receipt is None:
            return {"state": "idle", "receipt_state": None, "execution_id": None}
        state = receipt.state
        stable_observations = {"stable", "stable_owned_recovery_fence"}
        baseline_observation = receipt.artifact_baseline.get("_observation")
        final_observation = receipt.artifact_final.get("_observation")
        comparison_available = (
            baseline_observation in stable_observations
            and final_observation in stable_observations
        )
        changed_artifacts = (
            sorted(
                key
                for key in set(receipt.artifact_baseline) | set(receipt.artifact_final)
                if not key.startswith("_")
                and receipt.artifact_baseline.get(key) != receipt.artifact_final.get(key)
            )
            if comparison_available
            else []
        )
        if receipt.artifact_final:
            evidence_observation = final_observation or "unqualified_legacy_observation"
        elif state in TERMINAL_STATES:
            evidence_observation = "not_recorded"
        else:
            evidence_observation = "pending"
        try:
            from .supervisor import process_identity_is_live

            process_may_be_live = process_identity_is_live(
                receipt.pid,
                receipt.process_birth_hint,
            )
        except Exception:
            process_may_be_live = receipt.pid is not None and state not in TERMINAL_STATES
        return {
            "state": state.value,
            "receipt_state": state.value,
            "execution_id": receipt.execution_id,
            "receipt_run_id": receipt.run_id,
            "action": receipt.action,
            "requested_phase": receipt.requested_phase,
            "exit_code": receipt.exit_code,
            "started_at": receipt.started_at,
            "updated_at": receipt.updated_at,
            "finished_at": receipt.finished_at,
            "terminal": state in TERMINAL_STATES,
            "process_may_be_live": process_may_be_live,
            "process_outcome": (
                "exit_zero_requires_artifact_validation"
                if state.value == "exited" and receipt.exit_code == 0
                else "process_failed"
                if state.value == "exited"
                else state.value
            ),
            "evidence_observation": evidence_observation,
            "artifact_change_comparison_available": comparison_available,
            "changed_artifacts_since_start": changed_artifacts,
            "message": receipt.message,
        }

    def _writer(self) -> dict[str, object]:
        if self.owner_adapter is None:
            try:
                from .supervisor import OwnerStateAdapter

                owner_adapter: _OwnerReader = OwnerStateAdapter(self.project_root)
            except Exception as exc:
                return {
                    "unlocked": False,
                    "stable_for_inspection": False,
                    "state": "unavailable",
                    "error": str(exc),
                }
        else:
            owner_adapter = self.owner_adapter
        try:
            owner, error = owner_adapter.read_safely()
        except Exception as exc:
            return {
                "unlocked": False,
                "stable_for_inspection": False,
                "state": "unavailable",
                "error": str(exc),
            }
        if error:
            return {
                "unlocked": False,
                "stable_for_inspection": False,
                "state": "unavailable",
                "error": error,
            }
        if owner is None:
            return {
                "unlocked": True,
                "stable_for_inspection": True,
                "state": "unlocked",
                "owner": None,
            }
        recorded_state = str(owner.get("state") or "locked")
        process_may_be_live = True
        pid = owner.get("pid")
        if isinstance(pid, int) and not isinstance(pid, bool):
            try:
                from .supervisor import process_identity_is_live

                process_may_be_live = process_identity_is_live(pid, None)
            except Exception:
                process_may_be_live = True
        stable = recorded_state == "recovery_required" or not process_may_be_live
        return {
            "unlocked": False,
            "stable_for_inspection": stable,
            "state": (
                recorded_state
                if recorded_state == "recovery_required" or process_may_be_live
                else "stale_active_recovery_required"
            ),
            "recorded_state": recorded_state,
            "owner_process_may_be_live": process_may_be_live,
            "owner": owner,
        }

    @staticmethod
    def _attach_operation_relationship(
        article: dict[str, object],
        operation: dict[str, object],
        run_id: str,
    ) -> None:
        receipt_run_matches = operation.get("receipt_run_id") == run_id
        changed = operation.get("changed_artifacts_since_start")
        changed_names = changed if isinstance(changed, list) else []
        article_evidence = {
            "run_summary",
            "preflight",
            "article",
            "article_result",
            "final_sync",
            "finalize_provenance",
            "latest_manifest",
        }
        article["operation_relationship"] = {
            "receipt_run_matches": receipt_run_matches,
            "evidence_observation": operation.get("evidence_observation"),
            "article_evidence_changed_since_start": (
                bool(receipt_run_matches and article_evidence.intersection(changed_names))
                if operation.get("artifact_change_comparison_available") is True
                else None
            ),
            "interpretation": (
                "article validity describes the current validated artifacts and is not "
                "inferred from the process exit code"
            ),
        }

    def _json(self, run_id: str, artifact: ArtifactName) -> tuple[dict[str, Any] | None, str | None]:
        try:
            payload = self.broker.read(run_id, artifact).data
            decoded = json.loads(payload.decode("utf-8"))
        except (ArtifactError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            return None, str(exc)
        if not isinstance(decoded, dict):
            return None, "artifact is not a JSON object"
        return decoded, None

    def _schema_errors(
        self,
        payload: dict[str, Any],
        *,
        schema_name: str,
        label: str,
    ) -> list[str]:
        try:
            schema = self._safe_json_path(self.project_root / "schemas" / schema_name)
        except ValueError as exc:
            return [str(exc)]
        if schema is None:
            return [f"missing schema for {label}: schemas/{schema_name}"]
        validator, dependency_errors = load_schema_validator(schema)
        if validator is None:
            return dependency_errors
        errors = sorted(
            validator.iter_errors(payload),
            key=lambda error: (
                tuple(str(part) for part in error.absolute_path),
                error.message,
            ),
        )
        return [f"{label} schema validation failed: {error.message}" for error in errors]

    def _read_terminal_artifacts(self, run_id: str) -> tuple[dict[str, object], dict[str, object]]:
        preflight, preflight_error = self._json(run_id, ArtifactName.PREFLIGHT)
        result, result_error = self._json(run_id, ArtifactName.ARTICLE_RESULT)
        final_sync, sync_error = self._json(run_id, ArtifactName.FINAL_SYNC)
        manifest, manifest_error = self._json(run_id, ArtifactName.RUN_MANIFEST)
        preflight_schema_errors = (
            self._schema_errors(
                preflight,
                schema_name="preflight_result.schema.json",
                label="preflight result",
            )
            if preflight is not None
            else []
        )
        result_schema_errors = (
            self._schema_errors(
                result,
                schema_name="article_result.schema.json",
                label="article result",
            )
            if result is not None
            else []
        )
        sync_schema_errors = (
            self._schema_errors(
                final_sync,
                schema_name="final_sync_report.schema.json",
                label="final sync report",
            )
            if final_sync is not None
            else []
        )
        provenance = self._provenance(run_id, manifest)
        sync_hashes_match, sync_hash_errors = self._final_sync_hashes_match(
            run_id,
            manifest,
            final_sync,
        )
        summary = self._summary(run_id)
        intent = self._intent(run_id)

        article: dict[str, object] = {"state": "unavailable"}
        if result is not None:
            checklist = result.get("checklist_status")
            declared_hash = result.get("article_sha256")
            try:
                actual_hash = self.broker.read(run_id, ArtifactName.ARTICLE).sha256
            except ArtifactError as exc:
                actual_hash = None
                article_error = str(exc)
            else:
                article_error = None
            hash_matches = isinstance(declared_hash, str) and declared_hash == actual_hash
            preflight_passes = bool(
                preflight
                and preflight.get("run_id") == run_id
                and preflight.get("status") == "pass"
                and not preflight_schema_errors
            )
            sync_hash_matches = bool(
                final_sync
                and isinstance(final_sync.get("article_sha256"), str)
                and final_sync.get("article_sha256") == actual_hash
            )
            sync_passes = bool(
                final_sync
                and final_sync.get("run_id") == run_id
                and final_sync.get("status") in {"pass", "pass_with_warnings"}
                and final_sync.get("regenerated_after_freeze") is True
                and sync_hash_matches
                and sync_hashes_match
                and not sync_schema_errors
            )
            provenance_passes = bool(
                provenance.get("state") == "available"
                and provenance.get("valid_for_run") is True
                and provenance.get("article_hash_matches") is True
                and provenance.get("marker_state") == "expected"
                and provenance.get("current_checkpoint_valid") is True
            )
            summary_passes = bool(
                summary.get("state") == "available"
                and summary.get("run_id_matches") is True
                and summary.get("valid_shape") is True
                and summary.get("status") in {"completed", "audit_passed"}
            )
            checklist_passes = (
                checklist in {"pass", "pass_with_warnings"}
                and not result_schema_errors
            )
            valid = bool(
                checklist_passes
                and hash_matches
                and preflight_passes
                and sync_passes
                and provenance_passes
                and summary_passes
            )
            article = {
                "state": "valid" if valid else "invalid",
                "available": True,
                "valid": valid,
                "checklist_status": checklist if isinstance(checklist, str) else None,
                "declared_sha256": declared_hash if isinstance(declared_hash, str) else None,
                "actual_sha256": actual_hash,
                "hash_matches": hash_matches,
            }
            if article_error:
                article["error"] = article_error
        elif result_error:
            article["error"] = result_error

        artifacts: dict[str, object] = {
            "preflight_status": preflight.get("status") if preflight else None,
            "preflight_valid_for_run": bool(preflight and preflight.get("run_id") == run_id),
            "final_sync_status": final_sync.get("status") if final_sync else None,
            "final_sync_valid_for_run": bool(final_sync and final_sync.get("run_id") == run_id),
            "final_sync_article_hash_matches": bool(
                final_sync
                and isinstance(final_sync.get("article_sha256"), str)
                and final_sync.get("article_sha256") == article.get("actual_sha256")
            ),
            "final_sync_artifact_hashes_match": sync_hashes_match,
            "final_sync_artifact_hash_errors": sync_hash_errors,
            "finalize_provenance": provenance,
            "run_summary": summary,
            "intent_contract": intent,
            "schema_validation": {
                "preflight": preflight_schema_errors,
                "article_result": result_schema_errors,
                "final_sync": sync_schema_errors,
            },
        }
        for key, error in (
            ("preflight_error", preflight_error),
            ("article_result_error", result_error),
            ("final_sync_error", sync_error),
            ("run_manifest_error", manifest_error),
        ):
            if error:
                artifacts[key] = error
        return article, artifacts

    def _intent(self, run_id: str) -> dict[str, object]:
        path = self.project_root / "work" / "runs" / run_id / "intake" / "intent_contract.json"
        try:
            data = self._safe_json_path(path)
        except ValueError as exc:
            return {"state": "unavailable", "error": str(exc)}
        if data is None:
            return {"state": "missing"}
        schema_errors = self._schema_errors(
            data,
            schema_name="intent_contract.schema.json",
            label="intent contract",
        )
        try:
            current_fingerprint = scope_delta_fingerprint(data)
        except (TypeError, ValueError):
            return {"state": "invalid", "error": "scope delta is not inspectable"}
        return {
            "state": "available",
            "valid_for_run": data.get("run_id") == run_id and not schema_errors,
            "article_type": data.get("article_type"),
            "original_request": data.get("original_request"),
            "normalized_scope": {
                "primary_question": data.get("primary_question"),
                "must_include": data.get("must_include") if isinstance(data.get("must_include"), list) else [],
                "must_not_include": data.get("must_not_include") if isinstance(data.get("must_not_include"), list) else [],
                "analysis_modes": data.get("analysis_modes") if isinstance(data.get("analysis_modes"), list) else [],
                "depth": data.get("depth"),
            },
            "scope_status": data.get("scope_status"),
            "approval_status": data.get("approval_status"),
            "current_scope_sha256": current_fingerprint,
            "approved_scope_sha256": data.get("approved_scope_delta_sha256"),
            "scope_delta": data.get("scope_delta") if isinstance(data.get("scope_delta"), list) else [],
            "proposed_exclusions": (
                data.get("proposed_exclusions")
                if isinstance(data.get("proposed_exclusions"), list)
                else []
            ),
            "required_user_decisions": (
                data.get("required_user_decisions")
                if isinstance(data.get("required_user_decisions"), list)
                else []
            ),
            "schema_errors": schema_errors,
        }

    def _build_provenance_context(
        self,
        run_id: str,
        manifest: dict[str, Any] | None,
    ) -> tuple[ProvenanceContext | None, list[str]]:
        errors: list[str] = []
        if manifest is None:
            return None, ["run manifest is unavailable"]
        if manifest.get("run_id") != run_id:
            errors.append("run manifest run_id does not match the selected run")
        mode = manifest.get("article_type")
        if mode not in {"industry", "earnings", "preview"}:
            errors.append("run manifest article_type is invalid")
        raw_paths = manifest.get("paths")
        if not isinstance(raw_paths, dict):
            return None, [*errors, "run manifest paths are unavailable"]

        def manifest_path(name: str) -> str | None:
            value = raw_paths.get(name)
            if not isinstance(value, str) or not value:
                errors.append(f"run manifest path {name} is unavailable")
                return None
            try:
                resolved = resolve_under_root(self.project_root, value)
            except ValueError as exc:
                errors.append(f"run manifest path {name} is unsafe: {exc}")
                return None
            return resolved.relative_to(self.project_root).as_posix()

        values = {
            name: manifest_path(name)
            for name in (
                "topic",
                "sources",
                "work_run_dir",
                "article_result",
                "article",
                "source_ledger",
                "image_ledger",
                "claim_ledger",
            )
        }
        expected_work_dir = f"work/runs/{run_id}"
        if values["work_run_dir"] != expected_work_dir:
            errors.append(
                f"run manifest work_run_dir must be {expected_work_dir} for the selected run"
            )
        if errors:
            return None, errors

        artifacts = build_run_artifacts(
            work_run_dir=values["work_run_dir"] or "",
            outputs_dir=f"outputs/{run_id}",
            article=values["article"] or "",
            source_ledger=values["source_ledger"] or "",
            image_ledger=values["image_ledger"] or "",
            claim_ledger=values["claim_ledger"] or "",
            article_result=values["article_result"] or "",
        )
        earnings: tuple[str, ...] = ()
        if mode == "earnings":
            matrix = manifest_path("earnings_delta_matrix")
            matrix_md = manifest_path("earnings_delta_matrix_md")
            if matrix is None or matrix_md is None:
                return None, errors
            earnings = (matrix, matrix_md)
        context = ProvenanceContext(
            root=self.project_root,
            run_id=run_id,
            artifacts=artifacts,
            earnings_delta=earnings,
            topic_path=values["topic"] or "",
            sources_path=values["sources"] or "",
            run_manifest=f"outputs/{run_id}/run_manifest.json",
        )
        try:
            include_images = rendered_images_available(
                self.project_root,
                context.rendered_images_ledger,
                fallback=False,
            )
        except Exception as exc:
            return None, [
                f"rendered image provenance state is unreadable: {type(exc).__name__}: {exc}"
            ]
        return replace(context, include_images=include_images), []

    def _final_sync_hashes_match(
        self,
        run_id: str,
        manifest: dict[str, Any] | None,
        final_sync: dict[str, Any] | None,
    ) -> tuple[bool, list[str]]:
        context, errors = self._build_provenance_context(run_id, manifest)
        if context is None:
            return False, errors
        recorded = final_sync.get("artifact_hashes") if final_sync is not None else None
        if not isinstance(recorded, dict):
            return False, ["final sync report has no artifact_hashes"]
        try:
            current = final_sync_artifact_hashes(self.project_root, context.artifacts)
        except Exception as exc:
            return False, [
                f"current final sync hashes are unreadable: {type(exc).__name__}: {exc}"
            ]
        if recorded != current:
            return False, ["final sync artifact hashes do not match the current frozen outputs"]
        return True, []

    def _provenance(
        self,
        run_id: str,
        manifest: dict[str, Any] | None,
    ) -> dict[str, object]:
        path = self.project_root / "work" / "runs" / run_id / "provenance" / "finalize.json"
        try:
            data = self._safe_json_path(path)
        except ValueError as exc:
            return {"state": "unavailable", "error": str(exc)}
        if data is None:
            return {"state": "missing"}
        schema_errors = self._schema_errors(
            data,
            schema_name="phase_provenance.schema.json",
            label="finalize provenance",
        )
        hashes = data.get("artifact_hashes")
        article_hash = None
        if isinstance(hashes, dict):
            article_hash = hashes.get(f"outputs/{run_id}/article.md")
        try:
            actual_hash = self.broker.read(run_id, ArtifactName.ARTICLE).sha256
        except ArtifactError:
            actual_hash = None
        context, context_errors = self._build_provenance_context(run_id, manifest)
        checkpoint_errors = list(context_errors)
        marker_state = "unavailable"
        if context is not None:
            try:
                marker_state = provenance_marker_state(context)
                checkpoint_errors.extend(validate_checkpoint(context, "finalize"))
            except Exception as exc:
                checkpoint_errors.append(
                    f"finalize provenance current validation failed: {type(exc).__name__}: {exc}"
                )
        return {
            "state": "available",
            "valid_for_run": (
                data.get("run_id") == run_id
                and data.get("phase") == "finalize"
                and not schema_errors
            ),
            "phase": data.get("phase"),
            "article_hash_matches": isinstance(article_hash, str) and article_hash == actual_hash,
            "marker_state": marker_state,
            "current_checkpoint_valid": not checkpoint_errors and marker_state == "expected",
            "current_validation_errors": checkpoint_errors,
            "schema_errors": schema_errors,
        }

    def _summary(self, run_id: str) -> dict[str, object]:
        path = self.project_root / "logs" / run_id / "run_summary.json"
        try:
            data = self._safe_json_path(path)
        except ValueError as exc:
            return {"state": "unavailable", "error": str(exc)}
        if data is None:
            return {"state": "missing"}
        status = data.get("status")
        phase = data.get("phase")
        valid_shape = (
            isinstance(status, str)
            and isinstance(phase, str)
            and isinstance(data.get("article_result"), str)
            and isinstance(data.get("preflight_status"), str)
        )
        return {
            "state": "available",
            "status": status,
            "phase": phase,
            "run_id_matches": data.get("run_id") == run_id,
            "valid_shape": valid_shape,
        }

    def _safe_json_path(self, path: Path) -> dict[str, Any] | None:
        try:
            root_relative = path.relative_to(self.project_root)
            # Reuse the broker's guarded read by spelling the minimal standalone check here;
            # these two work/log artifacts are intentionally not browser-download artifacts.
            current = self.project_root
            for part in root_relative.parts:
                current = current / part
                info = current.lstat()
                if current.is_symlink() or (part != root_relative.parts[-1] and not current.is_dir()):
                    raise ValueError("status artifact path is unsafe")
            if not current.is_file():
                raise ValueError("status artifact is not a regular file")
        except FileNotFoundError:
            return None
        except OSError as exc:
            raise ValueError(f"cannot inspect status artifact: {exc}") from exc
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError(f"status artifact is unreadable: {exc}") from exc
        if not isinstance(data, dict):
            raise ValueError("status artifact is not a JSON object")
        return data

    def _latest_membership(self, run_id: str) -> dict[str, object]:
        latest = self.project_root / "outputs" / "latest"
        try:
            if latest.is_symlink() or not latest.is_dir():
                return {"member": False}
            current_manifest = self._safe_json_path(
                self.project_root / "outputs" / run_id / "run_manifest.json"
            )
            latest_manifest = self._safe_json_path(latest / "run_manifest.json")
        except ValueError:
            return {"member": False}
        if not (
            current_manifest
            and latest_manifest
            and current_manifest.get("run_id") == run_id
            and latest_manifest.get("run_id") == run_id
        ):
            return {"member": False}
        raw_paths = current_manifest.get("paths")
        if not isinstance(raw_paths, dict):
            return {"member": False}

        def matching_copy(key: str, *, optional: bool) -> bool:
            value = raw_paths.get(key)
            if not isinstance(value, str) or not value:
                return optional
            try:
                source = resolve_under_root(self.project_root, value)
                source_relative = source.relative_to(self.project_root)
                if optional:
                    try:
                        source.lstat()
                    except FileNotFoundError:
                        return True
                source_data = _read_regular_under(
                    self.project_root,
                    tuple(source_relative.parts),
                )
                latest_data = _read_regular_under(
                    self.project_root,
                    ("outputs", "latest", source.name),
                )
            except (ArtifactError, OSError, ValueError):
                return False
            return source_data == latest_data

        required_match = all(
            matching_copy(key, optional=False) for key, _field in LATEST_REQUIRED_OUTPUTS
        )
        optional_match = all(
            matching_copy(key, optional=True) for key, _field in LATEST_OPTIONAL_OUTPUTS
        )
        return {"member": required_match and optional_match}


__all__ = ["RunStatus", "StatusReader"]
