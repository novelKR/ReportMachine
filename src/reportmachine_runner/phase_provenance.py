"""Runner-owned phase completion checkpoints.

A resumed phase inherits artifacts this invocation never produced. Existence, schema, and
semantic checks cannot tell a coherent upstream state from one that was edited after the
producing role ran, so a stale downstream phase would otherwise reach a model call.

After a phase's outputs and semantics validate, the runner records the exact cumulative
artifact state through that phase. A later single-phase resume recomputes the same set and
compares it before any Codex invocation. Comparing runner-owned state instead of individual
role attempts handles the cases attempt inputs cannot: roles that rewrite their own input in
place, such as `revise` overwriting the reviewed draft, and artifacts the runner itself
merges, such as the combined research candidates.

Hashing and parsing stay pure Python so the `draft` phase keeps working when `jsonschema` is
unavailable.
"""

from __future__ import annotations

import csv
import json
import os
import stat
from dataclasses import dataclass, field
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
from typing import Any, Callable, Sequence

from .paths import resolve_under_root
from .phase_contracts import (
    PHASES,
    RunArtifacts,
    phase_input_contracts,
    phase_output_contracts,
)
from .source_ingest_paths import build_source_ingest_paths


PHASE_PROVENANCE_VERSION = 5

# Phases that run a model role and therefore have a resumable completion state.
PROVENANCE_ORDER = tuple(phase for phase in PHASES if phase not in {"prepare", "audit"})

# Stages at which the runner performs source ingest and records a receipt for the rows it
# wrote. `prepare` covers the base inventory; the research and evidence phases cover the
# second ingest of research-discovered candidates.
INGEST_RECEIPT_STAGES = ("prepare", "research", "evidence")

# The column that identifies a ledger row.
SOURCE_LEDGER_ROW_KEY = "source_id"
RENDERED_LEDGER_ROW_KEY = "image_path"

# Every other column is hashed. Only fields proven to move on their own during a resume are
# excluded, so a column added later is protected by default instead of being silently
# unguarded. `fetched_at` is a wall-clock stamp; the remaining fields are either evidence
# state a role reads or attribution the final ledgers must preserve.
LEDGER_VOLATILE_FIELDS = frozenset({"fetched_at"})


@dataclass(frozen=True)
class ProvenanceContext:
    """Everything needed to resolve one run's tracked artifact set."""

    root: Path
    run_id: str
    artifacts: RunArtifacts
    include_images: bool = False
    earnings_delta: tuple[str, ...] = field(default_factory=tuple)
    topic_path: str = ""
    sources_path: str = ""
    run_manifest: str = ""

    @property
    def source_candidate_ledger(self) -> str:
        """Canonical run-local source ledger path.

        Resolved from the run directory rather than the flag-gated run contract. If this
        followed `--no-source-ingest`, passing that flag on a resumed phase would silently
        drop ledger validation for a run whose ledger is sitting right there on disk.
        """

        return build_source_ingest_paths(self.artifacts.work_run_dir).source_candidate_ledger

    @property
    def rendered_images_ledger(self) -> str:
        """Canonical run-local rendered image ledger path."""

        return build_source_ingest_paths(self.artifacts.work_run_dir).rendered_images_ledger


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def provenance_dir(artifacts: RunArtifacts) -> str:
    return f"{artifacts.work_run_dir}/provenance"


def checkpoint_rel(artifacts: RunArtifacts, phase: str) -> str:
    return f"{provenance_dir(artifacts)}/{phase}.json"


def ingest_receipt_rel(artifacts: RunArtifacts, stage: str) -> str:
    return f"{provenance_dir(artifacts)}/ingest/{stage}.json"


def previous_phase(phase: str) -> str | None:
    """Return the phase whose completion state a resume of `phase` must trust."""

    if phase not in PROVENANCE_ORDER:
        return None
    index = PROVENANCE_ORDER.index(phase)
    return PROVENANCE_ORDER[index - 1] if index > 0 else None


def _file_sha256(path: Path) -> str | None:
    if not path.is_file():
        return None
    digest = sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _read_csv_column(root: Path, rel: str, column: str) -> list[str]:
    """Collect one column from a runner ledger without importing the csv contracts."""

    if not rel:
        return []
    try:
        path = resolve_under_root(root, rel)
    except ValueError:
        return []
    if not path.is_file():
        return []
    values: list[str] = []
    with path.open(newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            value = (row.get(column) or "").strip()
            if value:
                values.append(value)
    return values


def _ledger_row_hashes(
    root: Path,
    rel: str,
    *,
    key_column: str,
) -> dict[str, str] | None:
    """Hash the evidence-bearing fields of each ledger row, keyed by its identity column.

    Rows are hashed individually rather than hashing the ledger file, because runner-owned
    ingest legitimately appends rows during a run: an extra research round adds a source and
    would otherwise break an untampered resume. Per-row hashing still pins the state of every
    row that existed when the phase completed, so flipping `ingest_status` or swapping a
    `sha256` after the fact is caught.

    Returns `None` when the ledger does not exist, so a run without one records nothing
    instead of recording an empty ledger that later appears to have lost rows.
    """

    if not rel:
        return None
    try:
        path = resolve_under_root(root, rel)
    except ValueError:
        return None
    if not path.is_file():
        return None
    hashes: dict[str, str] = {}
    with path.open(newline="", encoding="utf-8") as handle:
        for index, row in enumerate(csv.DictReader(handle), start=1):
            key = (row.get(key_column) or "").strip() or f"row_{index:04d}"
            payload = {
                column: (value or "").strip()
                for column, value in row.items()
                if column
                and column != key_column
                and column not in LEDGER_VOLATILE_FIELDS
            }
            canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True)
            hashes[key] = sha256(canonical.encode("utf-8")).hexdigest()
    return hashes


def ledger_row_hashes(context: ProvenanceContext, phase: str) -> dict[str, dict[str, str]]:
    """Row-level state of the runner ledgers a phase's completion depends on."""

    if phase == "scope":
        return {}
    return _all_ledger_row_hashes(context)


def _all_ledger_row_hashes(context: ProvenanceContext) -> dict[str, dict[str, str]]:
    """Row-level state of every runner ledger this run tracks."""

    tracked: dict[str, dict[str, str]] = {}
    source_rows = _ledger_row_hashes(
        context.root,
        context.source_candidate_ledger,
        key_column=SOURCE_LEDGER_ROW_KEY,
    )
    if source_rows is not None:
        tracked[context.source_candidate_ledger] = source_rows
    # The canonical rendered ledger stays provenance-bearing even on a no-image resume.
    # `include_images` controls the review output contract, not whether a ledger sitting on
    # disk may be edited without detection.
    rendered_rows = _ledger_row_hashes(
        context.root,
        context.rendered_images_ledger,
        key_column=RENDERED_LEDGER_ROW_KEY,
    )
    if rendered_rows is not None:
        tracked[context.rendered_images_ledger] = rendered_rows
    return tracked


def rendered_images_available(root: Path, ledger_rel: str, *, fallback: bool) -> bool:
    """Report whether this run has an inspectable rendered image.

    The image audit is conditional, so this answer decides which review artifacts are
    under contract. Both the phase runner and the provenance checkpoint resolve it here so
    a checkpoint can never be written against a different artifact set than the one the
    run validates.
    """

    if not ledger_rel:
        return fallback
    try:
        ledger_path = resolve_under_root(root, ledger_rel)
    except ValueError:
        return False
    if not ledger_path.is_file():
        return False
    with ledger_path.open(newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            image_path = (row.get("image_path") or "").strip()
            if (row.get("status") or "").strip() != "ok" or not image_path:
                continue
            try:
                if resolve_under_root(root, image_path).is_file():
                    return True
            except ValueError:
                continue
    return False


def cumulative_contracts(context: ProvenanceContext, phase: str) -> tuple:
    """Every artifact contract in force once `phase` has completed."""

    if phase not in PROVENANCE_ORDER:
        return ()
    contracts: list = []
    for candidate in PROVENANCE_ORDER[: PROVENANCE_ORDER.index(phase) + 1]:
        contracts.extend(
            phase_output_contracts(
                candidate,
                context.artifacts,
                include_images=context.include_images,
            )
        )
    return tuple(contracts)


def contract_paths(context: ProvenanceContext, phase: str) -> tuple[str, ...]:
    """Paths the phase contracts require once `phase` has completed.

    This set is fixed by the contract table, so a path appearing or disappearing here is a
    real structural change and is checked strictly.

    The runner ledgers are declared inputs but are excluded here: they are tracked row by row
    instead, so that a legitimate runner-owned append does not read as a whole-file edit and
    a volatile column such as `fetched_at` does not fail an untampered run.
    """

    ledgers = {context.source_candidate_ledger, context.rendered_images_ledger}
    paths = [
        contract.path
        for contract in phase_input_contracts(
            phase,
            context.artifacts,
            topic_path=context.topic_path,
            sources_path=context.sources_path,
            source_candidate_ledger=context.source_candidate_ledger,
        )
        if contract.path not in ledgers
    ]
    paths.extend(contract.path for contract in cumulative_contracts(context, phase))
    return tuple(sorted({rel for rel in paths if rel}))


def observed_paths(context: ProvenanceContext, phase: str) -> tuple[str, ...]:
    """Files whose paths are named inside the run's ledgers rather than by a contract.

    This covers only ledger-derived material: the extracted source text a quote audit checks
    against, the rendered images an image audit inspects, and the earnings delta matrices.
    Declared phase inputs and outputs belong to `contract_paths()`.

    Their contents matter, so a recorded file that is edited or deleted still fails. Their
    membership is not fixed, because runner-owned ingest adds material to a run, so a newly
    appearing file here is not treated as tampering.
    """

    if phase == "scope":
        return ()
    paths: list[str] = list(
        _read_csv_column(
            context.root,
            context.source_candidate_ledger,
            "extracted_text_path",
        )
    )
    paths.extend(
        _read_csv_column(context.root, context.rendered_images_ledger, "image_path")
    )
    if phase in {"evidence", "narrative", "draft", "review", "revise", "finalize"}:
        paths.extend(context.earnings_delta)
    return tuple(sorted({rel for rel in paths if rel}))


def tracked_paths(context: ProvenanceContext, phase: str) -> tuple[str, ...]:
    """Every artifact path a phase's completion state covers."""

    combined = set(contract_paths(context, phase)) | set(observed_paths(context, phase))
    return tuple(sorted(combined))


def _contract_fingerprint(context: ProvenanceContext, phase: str) -> str:
    """Fingerprint the contract definitions so a code-level change fails closed."""

    payload = [
        [contract.path, contract.label, contract.schema or "", bool(contract.jsonl)]
        for contract in cumulative_contracts(context, phase)
    ]
    canonical = json.dumps({"phase": phase, "contracts": payload}, sort_keys=True)
    return sha256(canonical.encode("utf-8")).hexdigest()


def _artifact_hashes(context: ProvenanceContext, phase: str) -> dict[str, str | None]:
    """Hash every tracked path, recording `None` for one that does not exist yet.

    A contract path keeps its `None` entry so a later appearance is still detectable. An
    absent ledger-discovered path is dropped instead, because runner-owned ingest adds that
    kind of material during a run and its absence carries no meaning.
    """

    required = set(contract_paths(context, phase))
    hashes: dict[str, str | None] = {}
    for rel in tracked_paths(context, phase):
        try:
            digest = _file_sha256(resolve_under_root(context.root, rel))
        except ValueError:
            digest = None
        if digest is None and rel not in required:
            continue
        hashes[rel] = digest
    return hashes


def write_phase_checkpoint(context: ProvenanceContext, phase: str) -> None:
    """Record a phase's completion state. Callers must validate the phase first."""

    if phase not in PROVENANCE_ORDER:
        return
    rel = checkpoint_rel(context.artifacts, phase)
    target = resolve_under_root(context.root, rel)
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "version": PHASE_PROVENANCE_VERSION,
        "run_id": context.run_id,
        "phase": phase,
        "completed_at": _now_iso(),
        "contract_sha256": _contract_fingerprint(context, phase),
        "artifact_hashes": _artifact_hashes(context, phase),
        "ledger_row_hashes": ledger_row_hashes(context, phase),
    }
    # Write through a temporary file so an interrupted run cannot leave a half-written
    # checkpoint that would later be read as a trustworthy completion record.
    temporary = target.with_suffix(".json.tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temporary.replace(target)
    _record_provenance_marker(context)


def provenance_marker_payload() -> dict:
    """The marker recorded once a run starts producing checkpoints."""

    return {"expected": True, "version": PHASE_PROVENANCE_VERSION}


def _record_provenance_marker(context: ProvenanceContext) -> None:
    """Note in the run manifest that this run produces checkpoints.

    Without a marker outside the provenance directory, deleting that directory makes a
    provenance-enabled run indistinguishable from a genuinely legacy one, and the audit would
    wave it through. The marker is written when the first checkpoint lands rather than when
    the manifest is created, so a `--prepare-only` run that never records provenance is not
    marked as expecting it.
    """

    if not context.run_manifest:
        return
    try:
        path = resolve_under_root(context.root, context.run_manifest)
    except ValueError:
        return
    if not path.is_file():
        return
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return
    if not isinstance(payload, dict):
        return
    if payload.get("provenance_marker") == provenance_marker_payload():
        return
    payload["provenance_marker"] = provenance_marker_payload()
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def provenance_marker_state(context: ProvenanceContext) -> str:
    """Classify whether the run manifest proves marker presence or absence.

    A waiver requires a readable object manifest in which the marker key is genuinely absent.
    Missing, malformed, non-object, or malformed-marker manifests cannot establish absence.
    """

    if not context.run_manifest:
        return "missing"
    try:
        path = resolve_under_root(context.root, context.run_manifest)
    except ValueError:
        return "invalid"
    if not path.is_file():
        return "missing"
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return "invalid"
    if not isinstance(payload, dict):
        return "invalid"
    if "provenance_marker" not in payload:
        return "absent"
    marker = payload.get("provenance_marker")
    if marker == provenance_marker_payload():
        return "expected"
    return "invalid"


def provenance_expected(context: ProvenanceContext) -> bool:
    """Report whether the run manifest records that this run produces checkpoints."""

    return provenance_marker_state(context) == "expected"


def invalidate_from_phase(context: ProvenanceContext, phase: str) -> None:
    """Drop this phase's checkpoint and every downstream one before it re-runs.

    Ingest receipts are intentionally retained. Their input hashes make them cease to count
    automatically when a rerun changes an input, while retaining a still-valid receipt is
    necessary to carry runner-owned rows into a later cumulative receipt.
    """

    if phase not in PROVENANCE_ORDER:
        return
    for candidate in PROVENANCE_ORDER[PROVENANCE_ORDER.index(phase) :]:
        try:
            path = resolve_under_root(context.root, checkpoint_rel(context.artifacts, candidate))
        except ValueError:
            continue
        path.unlink(missing_ok=True)


def capture_ledger_rows(context: ProvenanceContext) -> dict[str, dict[str, str]]:
    """Snapshot ledger row state so an ingest can be expressed as a delta."""

    return _all_ledger_row_hashes(context)


@dataclass(frozen=True)
class IngestReceiptBaseline:
    """Ledger and receipt lineage captured before runner-owned input mutation.

    Candidate merge rewrites an input named by the prior receipt. Capturing valid receipt
    rows before that rewrite lets a successful later ingest rebind those same rows to the
    new input hashes without blessing unrelated rows that happened to be in the ledger.
    """

    before_rows: dict[str, dict[str, str]]
    valid_receipt_rows: dict[str, dict[str, str]]
    receipt_stages: dict[str, dict[str, tuple[str, ...]]]


def _ledger_row_delta(
    before: dict[str, dict[str, str]],
    after: dict[str, dict[str, str]],
) -> dict[str, dict[str, str]]:
    """Rows an ingest actually added or changed.

    Recording the whole ledger instead would let one unrelated ingest vouch for a row that
    was inserted by hand and merely happened to be sitting in the file at the time.
    """

    delta: dict[str, dict[str, str]] = {}
    for ledger_rel, rows in after.items():
        prior = before.get(ledger_rel, {})
        changed = {
            key: digest for key, digest in rows.items() if prior.get(key) != digest
        }
        if changed:
            delta[ledger_rel] = changed
    return delta


def _ingest_receipt_stage_rows(
    context: ProvenanceContext,
    *,
    require_current_match: bool,
    require_inputs_hold: bool = True,
) -> dict[str, dict[str, dict[str, str]]]:
    """Load structurally valid receipt rows, optionally requiring current inputs.

    The receipt filename is part of the contract: a payload copied from another stage does
    not acquire the destination stage's authority. Only the two canonical runner ledgers are
    accepted, and callers may additionally require every row digest to match the ledger now.
    """

    current = _all_ledger_row_hashes(context) if require_current_match else {}
    canonical_ledgers = {
        context.source_candidate_ledger,
        context.rendered_images_ledger,
    }
    valid: dict[str, dict[str, dict[str, str]]] = {}
    for stage in INGEST_RECEIPT_STAGES:
        try:
            path = resolve_under_root(
                context.root,
                ingest_receipt_rel(context.artifacts, stage),
            )
        except ValueError:
            continue
        if not path.is_file():
            continue
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(payload, dict):
            continue
        if payload.get("version") != PHASE_PROVENANCE_VERSION:
            continue
        if str(payload.get("run_id") or "").strip() != context.run_id:
            continue
        if str(payload.get("stage") or "").strip() != stage:
            continue
        if (
            require_inputs_hold
            and not _receipt_inputs_hold(context, payload.get("input_hashes"))
        ):
            continue
        rows = payload.get("ledger_row_hashes")
        if not isinstance(rows, dict):
            continue
        stage_rows: dict[str, dict[str, str]] = {}
        for raw_ledger_rel, ledger_rows in rows.items():
            ledger_rel = str(raw_ledger_rel)
            if ledger_rel not in canonical_ledgers or not isinstance(ledger_rows, dict):
                continue
            accepted: dict[str, str] = {}
            for raw_key, digest in ledger_rows.items():
                key = str(raw_key)
                if not key or not isinstance(digest, str) or not digest:
                    continue
                if (
                    require_current_match
                    and current.get(ledger_rel, {}).get(key) != digest
                ):
                    continue
                accepted[key] = digest
            if accepted:
                stage_rows[ledger_rel] = accepted
        valid[stage] = stage_rows
    return valid


def capture_ingest_receipt_baseline(context: ProvenanceContext) -> IngestReceiptBaseline:
    """Capture current ledgers and every still-valid, identical receipted row."""

    before = _all_ledger_row_hashes(context)
    stage_rows = _ingest_receipt_stage_rows(
        context,
        require_current_match=True,
    )
    merged: dict[str, dict[str, str]] = {}
    stages: dict[str, dict[str, list[str]]] = {}
    for stage in INGEST_RECEIPT_STAGES:
        for ledger_rel, rows in stage_rows.get(stage, {}).items():
            target = merged.setdefault(ledger_rel, {})
            target_stages = stages.setdefault(ledger_rel, {})
            for key, digest in rows.items():
                # Every accepted digest already matches the one current row, so repeated
                # cumulative receipts agree by construction.
                target[key] = digest
                target_stages.setdefault(key, []).append(stage)
    return IngestReceiptBaseline(
        before_rows={rel: dict(rows) for rel, rows in before.items()},
        valid_receipt_rows={rel: dict(rows) for rel, rows in merged.items()},
        receipt_stages={
            rel: {key: tuple(values) for key, values in rows.items()}
            for rel, rows in stages.items()
        },
    )


def receipt_row_producer(
    context: ProvenanceContext,
    ledger_rel: str,
    row_key: str,
    *,
    row_digest: str | None = None,
) -> str | None:
    """Return the earliest provable ingest stage for one row, or ``None``.

    No origin is persisted. The answer is reconstructed conservatively from still-valid v5
    stage receipts; callers must fall back to ``--phase all`` when this returns ``None``.
    This row-lineage helper does not establish a safe partial-recovery point after a
    canonical ledger or one of its referenced files is mutated; those guard violations
    require ``--phase all`` regardless of the receipt stage.
    """

    if row_digest is None:
        row_digest = _all_ledger_row_hashes(context).get(ledger_rel, {}).get(row_key)
    if not row_digest:
        return None
    stage_rows = _ingest_receipt_stage_rows(
        context,
        require_current_match=False,
    )
    matches = [
        stage
        for stage in INGEST_RECEIPT_STAGES
        if stage_rows.get(stage, {}).get(ledger_rel, {}).get(row_key) == row_digest
    ]
    if not matches:
        return None
    producer = matches[0]
    # A later cumulative receipt does not encode whether it added or carried a row. If an
    # earlier envelope records the same digest but its inputs no longer hold, the current
    # v5 files cannot prove which stage produced it; fail closed instead of relabeling it as
    # a later-stage row.
    structural_rows = _ingest_receipt_stage_rows(
        context,
        require_current_match=False,
        require_inputs_hold=False,
    )
    for stage in INGEST_RECEIPT_STAGES[: INGEST_RECEIPT_STAGES.index(producer)]:
        if structural_rows.get(stage, {}).get(ledger_rel, {}).get(row_key) == row_digest:
            return None
    return producer


def write_ingest_receipt(
    context: ProvenanceContext,
    stage: str,
    *,
    before_rows: dict[str, dict[str, str]] | None = None,
    baseline: IngestReceiptBaseline | None = None,
    input_paths: Sequence[str],
    written_source_ids: Sequence[str] | None = None,
    written_image_paths: Sequence[str] | None = None,
) -> None:
    """Prove that the runner turned a specific input state into specific ledger rows.

    A resumed phase may find ledger rows its upstream checkpoint never recorded, but only
    when the runner produced them from inputs that still hold. Recording the resulting rows
    alone is not enough: an attacker could swap an input, let the runner ingest under it, then
    restore the input and keep the row. So the receipt pins both the delta this ingest
    produced and the hashes of the inputs it was produced from, and the rows count as
    accounted for only while those inputs still match.
    """

    if stage not in INGEST_RECEIPT_STAGES:
        return
    rel = ingest_receipt_rel(context.artifacts, stage)
    try:
        target = resolve_under_root(context.root, rel)
    except ValueError:
        return
    target.parent.mkdir(parents=True, exist_ok=True)
    if baseline is None:
        # Compatibility for callers that only need the original delta receipt behavior.
        # Production ingest callers should capture a baseline before merge and pass the
        # result's explicit written keys.
        baseline = IngestReceiptBaseline(
            before_rows={
                rel: dict(rows) for rel, rows in (before_rows or {}).items()
            },
            valid_receipt_rows={},
            receipt_stages={},
        )
    after_rows = _all_ledger_row_hashes(context)
    explicit_written_keys = (
        written_source_ids is not None or written_image_paths is not None
    )
    written_keys = {
        context.source_candidate_ledger: {
            str(key) for key in (written_source_ids or ()) if str(key)
        },
        context.rendered_images_ledger: {
            str(key) for key in (written_image_paths or ()) if str(key)
        },
    }

    if explicit_written_keys:
        owned_delta: dict[str, dict[str, str]] = {}
        for ledger_rel, keys in written_keys.items():
            changed = {
                key: after_rows.get(ledger_rel, {})[key]
                for key in keys
                if key in after_rows.get(ledger_rel, {})
                and baseline.before_rows.get(ledger_rel, {}).get(key)
                != after_rows.get(ledger_rel, {}).get(key)
            }
            if changed:
                owned_delta[ledger_rel] = changed
    else:
        # Legacy test/helper callers have no invocation result to identify written keys.
        owned_delta = _ledger_row_delta(baseline.before_rows, after_rows)

    if stage == "prepare" and explicit_written_keys:
        # Base ingest is a full runner rebuild of both canonical ledgers. Recording every
        # post-ingest row also covers failed-render rows whose empty image_path is represented
        # by the ledger parser's stable row_NNNN fallback key.
        receipt_rows = {
            rel: dict(rows) for rel, rows in after_rows.items() if rows
        }
    elif stage in {"research", "evidence"}:
        receipt_rows: dict[str, dict[str, str]] = {}
        for ledger_rel, rows in baseline.valid_receipt_rows.items():
            carried = {
                key: digest
                for key, digest in rows.items()
                if baseline.before_rows.get(ledger_rel, {}).get(key) == digest
                and after_rows.get(ledger_rel, {}).get(key) == digest
            }
            if carried:
                receipt_rows[ledger_rel] = carried
        for ledger_rel, rows in owned_delta.items():
            receipt_rows.setdefault(ledger_rel, {}).update(rows)
    else:
        receipt_rows = owned_delta

    input_hashes: dict[str, str | None] = {}
    for input_rel in input_paths:
        if input_rel:
            input_hashes[input_rel] = artifact_sha256(context, input_rel)
    payload = {
        "version": PHASE_PROVENANCE_VERSION,
        "run_id": context.run_id,
        "stage": stage,
        "recorded_at": _now_iso(),
        "input_hashes": input_hashes,
        "ledger_row_hashes": receipt_rows,
    }
    temporary = target.with_suffix(".json.tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temporary.replace(target)


def _ingest_receipt_rows(context: ProvenanceContext) -> dict[str, dict[str, str]]:
    """Merge every still-valid receipt into one ledger-to-row-hash view.

    A receipt is only evidence while the inputs it names are unchanged. Comparing against the
    current files rather than an upstream checkpoint is deliberate: extra research legitimately
    rewrites the candidate files inside the evidence phase, and the path checks already bind
    the current state to the checkpoint, so agreement here is transitive.
    """

    return capture_ingest_receipt_baseline(context).valid_receipt_rows


def _receipt_inputs_hold(context: ProvenanceContext, input_hashes: Any) -> bool:
    """Report whether every input a receipt was produced from is still identical."""

    if not isinstance(input_hashes, dict) or not input_hashes:
        # A receipt that names no inputs cannot prove what produced its rows.
        return False
    for input_rel, expected in input_hashes.items():
        current = artifact_sha256(context, str(input_rel))
        if expected is None:
            if current is not None:
                return False
            continue
        if not isinstance(expected, str) or not expected or current != expected:
            return False
    return True


def rebind_runner_owned_artifact(
    context: ProvenanceContext,
    rel: str,
    *,
    previous_sha256: str | None,
) -> None:
    """Re-record one artifact the runner itself rewrote outside any phase.

    The runner normalizes its own intent contract at startup, for example by migrating a
    legacy shape or applying a CLI approval. That rewrite is runner-owned, so it must not
    read as tampering.

    `previous_sha256` is the hash captured immediately before the rewrite. A checkpoint is
    updated only when it recorded exactly that hash, which proves the runner rewrote the same
    bytes the phase had completed with. If the file had already drifted, the checkpoint is
    left alone so validation still reports the edit instead of the rebind laundering it.
    """

    if not rel or previous_sha256 is None:
        return
    try:
        current = _file_sha256(resolve_under_root(context.root, rel))
    except ValueError:
        return
    if current is None or current == previous_sha256:
        return
    for phase in PROVENANCE_ORDER:
        payload = read_checkpoint(context, phase)
        if payload is None:
            continue
        recorded = payload.get("artifact_hashes")
        if not isinstance(recorded, dict) or rel not in recorded:
            continue
        if recorded.get(rel) != previous_sha256:
            # Either already up to date, or the recorded state does not match what the
            # runner just rewrote. Neither case may be overwritten.
            continue
        recorded[rel] = current
        try:
            target = resolve_under_root(context.root, checkpoint_rel(context.artifacts, phase))
        except ValueError:
            continue
        target.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )


def read_checkpoint(context: ProvenanceContext, phase: str) -> dict | None:
    try:
        path = resolve_under_root(context.root, checkpoint_rel(context.artifacts, phase))
    except ValueError:
        return None
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def has_any_checkpoint(context: ProvenanceContext) -> bool:
    """Report whether any canonical checkpoint artifact exists.

    Existence, not successful parsing, defines the boundary. A corrupt or non-object checkpoint
    is evidence that the run attempted to record provenance and must fail validation rather than
    being treated as a checkpoint-free historical run.
    """

    for phase in PROVENANCE_ORDER:
        try:
            path = resolve_under_root(
                context.root,
                checkpoint_rel(context.artifacts, phase),
            )
        except ValueError:
            continue
        if path.exists():
            return True
    return False


def artifact_sha256(context: ProvenanceContext, rel: str) -> str | None:
    """Hash one run-relative artifact, for callers that must capture pre-rewrite state."""

    if not rel:
        return None
    try:
        return _file_sha256(resolve_under_root(context.root, rel))
    except ValueError:
        return None


def validate_checkpoint(
    context: ProvenanceContext,
    phase: str,
) -> list[str]:
    """Compare a recorded phase completion state with the artifacts on disk now."""

    if phase not in PROVENANCE_ORDER:
        return []
    rel = checkpoint_rel(context.artifacts, phase)
    payload = read_checkpoint(context, phase)
    if payload is None:
        return [
            f"missing or unreadable {phase} phase provenance checkpoint: {rel}. "
            "This run was produced before provenance was recorded, or the checkpoint was "
            "removed. Re-run with --phase all, or from the earliest phase you need."
        ]
    errors: list[str] = []
    if payload.get("version") != PHASE_PROVENANCE_VERSION:
        errors.append(
            f"{phase} phase provenance checkpoint version is unsupported: "
            f"{payload.get('version')!r}"
        )
    actual_run_id = str(payload.get("run_id") or "").strip()
    if actual_run_id != context.run_id:
        errors.append(
            f"{phase} phase provenance checkpoint run_id mismatch: expected "
            f"`{context.run_id}`, found `{actual_run_id or 'missing'}`"
        )
    if str(payload.get("phase") or "").strip() != phase:
        errors.append(
            f"{phase} phase provenance checkpoint records a different phase: "
            f"{payload.get('phase')!r}"
        )
    if payload.get("contract_sha256") != _contract_fingerprint(context, phase):
        errors.append(
            f"{phase} phase provenance checkpoint was written against different artifact "
            "contracts; re-run the earlier phases to regenerate it"
        )
    recorded = payload.get("artifact_hashes")
    if not isinstance(recorded, dict):
        errors.append(f"{phase} phase provenance checkpoint has no artifact hashes: {rel}")
        return errors
    recorded_rows = payload.get("ledger_row_hashes")
    if not isinstance(recorded_rows, dict):
        errors.append(f"{phase} phase provenance checkpoint has no ledger row hashes: {rel}")
        return errors
    if errors:
        return errors

    current = _artifact_hashes(context, phase)
    required = set(contract_paths(context, phase))
    for dropped in sorted(set(recorded) - set(current)):
        if recorded.get(dropped) is None:
            # It was absent when the phase completed and is still not tracked now.
            continue
        # A recorded file that no longer resolves is a real loss of evidence, so it fails
        # whether the contracts or a ledger put it in scope.
        errors.append(
            f"{phase} phase artifact is no longer tracked by the current run: {dropped}"
        )
    for added in sorted(required - set(recorded)):
        # Only contract-required paths have a fixed membership. Runner-owned ingest may add
        # new extracted text or rendered images to a run without that being tampering.
        errors.append(
            f"{phase} phase artifact appeared after the phase completed: {added}"
        )
    for path in sorted(set(recorded) & set(current)):
        expected = recorded.get(path)
        actual = current.get(path)
        if expected is None:
            # The artifact did not exist when the phase completed. Still absent matches what
            # was recorded; newly present means material appeared that no phase produced.
            if actual is not None:
                errors.append(
                    f"{phase} phase artifact appeared after the phase completed: {path}"
                )
            continue
        if not isinstance(expected, str) or not expected:
            errors.append(
                f"{phase} phase provenance checkpoint has no usable hash for {path}"
            )
        elif actual is None:
            errors.append(f"{phase} phase artifact is missing: {path}")
        elif actual != expected:
            errors.append(
                f"{phase} phase artifact changed after the phase completed: {path}"
            )
    errors.extend(
        _ledger_row_errors(
            context,
            phase,
            recorded_rows,
        )
    )
    return errors


def _ledger_row_errors(
    context: ProvenanceContext,
    phase: str,
    recorded_rows: dict,
) -> list[str]:
    """Compare each recorded ledger row with the ledger as it stands now.

    The evidence verifier reads ingest status straight from the source ledger, so flipping a
    row to `blocked` or swapping its hash after the phase completed changes what downstream
    roles were told without touching any contract artifact.

    The row set is closed. A row the checkpoint never recorded is accepted only when an ingest
    receipt shows the runner itself wrote that exact row, which also covers a ledger that did
    not exist when the phase completed. The resumed phase name is not evidence of anything:
    the caller chooses it.
    """

    errors: list[str] = []
    current_rows = ledger_row_hashes(context, phase)
    receipt_rows = _ingest_receipt_rows(context)
    # Walk the union so a ledger absent from the checkpoint cannot skip validation entirely.
    for ledger_rel in sorted(set(recorded_rows) | set(current_rows)):
        recorded = recorded_rows.get(ledger_rel)
        if recorded is None:
            recorded = {}
        elif not isinstance(recorded, dict):
            errors.append(
                f"{phase} phase provenance checkpoint has unusable rows for {ledger_rel}"
            )
            continue
        current = current_rows.get(ledger_rel)
        if current is None:
            errors.append(
                f"{phase} phase ledger is no longer available: {ledger_rel}"
            )
            continue
        for key in sorted(recorded):
            expected = recorded.get(key)
            if not isinstance(expected, str) or not expected:
                errors.append(
                    f"{phase} phase provenance checkpoint has no usable hash for "
                    f"{ledger_rel} row {key}"
                )
            elif key not in current:
                errors.append(
                    f"{phase} phase ledger row was removed after the phase completed: "
                    f"{ledger_rel} row {key}"
                )
            elif current[key] != expected:
                errors.append(
                    f"{phase} phase ledger row changed after the phase completed: "
                    f"{ledger_rel} row {key}"
                )
        for key in sorted(set(current) - set(recorded)):
            if receipt_rows.get(ledger_rel, {}).get(key) == current[key]:
                # Runner-owned ingest wrote this exact row, so it is accounted for.
                continue
            errors.append(
                f"{phase} phase ledger row appeared after the phase completed without a "
                f"runner ingest receipt: {ledger_rel} row {key}"
            )
    return errors


def validate_upstream_provenance(context: ProvenanceContext, phase: str) -> list[str]:
    """Validate the completion state a resumed `phase` depends on."""

    upstream = previous_phase(phase)
    if upstream is None:
        return []
    return validate_checkpoint(context, upstream)


def upstream_guard_paths(
    context: ProvenanceContext,
    phase: str,
    *,
    owned_paths: Sequence[str],
) -> tuple[str, ...]:
    """Upstream artifacts a phase must not rewrite while it runs.

    A phase's checkpoint is written from whatever is on disk once its roles finish, so a role
    that edits an artifact belonging to an earlier phase would have that edit recorded as the
    legitimate completion state. Freezing those paths across the phase keeps the checkpoint a
    record of what the phase produced rather than of whatever last touched the run.

    `owned_paths` are the phase's own declared outputs. Runner-owned mutation is never a
    phase-wide exclusion; a `PhaseMutationGuard` may rebase narrowly after validating one
    candidate merge or source-ingest operation.
    """

    upstream = previous_phase(phase)
    excluded = {rel for rel in owned_paths if rel}
    # `scope` has no model-produced predecessor, but its normalized inputs still precede
    # the role and must not be rewritten before the first checkpoint is recorded.
    protected_phase = upstream or phase
    protected = set(tracked_paths(context, protected_phase))
    protected.update(
        {
            context.source_candidate_ledger,
            context.rendered_images_ledger,
        }
    )
    # Resolve both ledgers directly, regardless of the current image-review flag. A role
    # cannot rewrite a ledger or one of its named files and then have the phase checkpoint
    # canonize the changed bytes.
    protected.update(
        _read_csv_column(
            context.root,
            context.source_candidate_ledger,
            "extracted_text_path",
        )
    )
    protected.update(
        _read_csv_column(
            context.root,
            context.rendered_images_ledger,
            "image_path",
        )
    )
    return tuple(sorted(rel for rel in protected if rel and rel not in excluded))


def _lexical_path(root: Path, rel: str) -> Path | None:
    """Resolve a repository-relative path without following its final symlink."""

    value = Path(rel)
    if not rel or value.is_absolute() or ".." in value.parts:
        return None
    return root.expanduser().resolve().joinpath(value)


def _guard_path_state(root: Path, rel: str) -> str | None:
    """Capture entry type and content without following symlinks."""

    path = _lexical_path(root, rel)
    if path is None:
        return "invalid"
    try:
        mode = path.lstat().st_mode
    except FileNotFoundError:
        return None
    except OSError as exc:
        return f"error:{exc.errno}"
    if stat.S_ISLNK(mode):
        try:
            return f"symlink:{os.readlink(path)}"
        except OSError as exc:
            return f"symlink-error:{exc.errno}"
    if stat.S_ISREG(mode):
        digest = sha256()
        try:
            with path.open("rb") as handle:
                for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                    digest.update(chunk)
        except OSError as exc:
            return f"file-error:{exc.errno}"
        return f"file:{digest.hexdigest()}"
    if stat.S_ISDIR(mode):
        return "directory"
    return f"special:{stat.S_IFMT(mode):o}"


def _prefix_entry_paths(root: Path, prefix: str) -> set[str]:
    """List a runner-owned prefix without following directory symlinks."""

    start = _lexical_path(root, prefix)
    if start is None:
        return {prefix}
    entries = {Path(prefix).as_posix().rstrip("/")}

    def walk(path: Path, rel: str) -> None:
        try:
            with os.scandir(path) as iterator:
                children = sorted(iterator, key=lambda item: item.name)
        except (FileNotFoundError, NotADirectoryError, OSError):
            return
        for child in children:
            child_rel = f"{rel}/{child.name}" if rel else child.name
            entries.add(child_rel)
            try:
                is_directory = child.is_dir(follow_symlinks=False)
            except OSError:
                is_directory = False
            if is_directory:
                walk(Path(child.path), child_rel)

    try:
        is_directory = stat.S_ISDIR(start.lstat().st_mode)
    except OSError:
        is_directory = False
    if is_directory:
        walk(start, Path(prefix).as_posix().rstrip("/"))
    return {rel for rel in entries if rel}


def _capture_guard_states(
    context: ProvenanceContext,
    paths: Sequence[str],
) -> dict[str, str | None]:
    return {
        rel: _guard_path_state(context.root, rel)
        for rel in sorted({str(path) for path in paths if str(path)})
    }


def capture_guard_hashes(
    context: ProvenanceContext,
    paths: Sequence[str],
) -> dict[str, str | None]:
    return _capture_guard_states(context, paths)


def guard_violations(
    context: ProvenanceContext,
    phase: str,
    before: dict[str, str | None],
) -> list[str]:
    """Report upstream artifacts this phase changed while it ran."""

    errors: list[str] = []
    for rel, expected in sorted(before.items()):
        if _guard_path_state(context.root, rel) != expected:
            errors.append(
                f"{phase} phase changed an upstream artifact it does not own: {rel}"
            )
    return errors


class RunnerMutationViolation(RuntimeError):
    """A runner-owned callback changed a path outside its narrow capability."""

    def __init__(self, label: str, errors: Sequence[str]) -> None:
        self.label = label
        self.errors = tuple(str(error) for error in errors)
        super().__init__("; ".join(self.errors) or label)


@dataclass
class PhaseMutationGuard:
    """Freeze upstream artifacts and rebase only verified runner-owned mutations."""

    context: ProvenanceContext
    phase: str
    owned_paths: tuple[str, ...]
    _baseline: dict[str, str | None]

    @classmethod
    def create(
        cls,
        context: ProvenanceContext,
        phase: str,
        *,
        owned_paths: Sequence[str],
    ) -> PhaseMutationGuard:
        owned = tuple(str(path) for path in owned_paths if str(path))
        paths = upstream_guard_paths(context, phase, owned_paths=owned)
        return cls(
            context=context,
            phase=phase,
            owned_paths=owned,
            _baseline=capture_guard_hashes(context, paths),
        )

    def violations(self) -> list[str]:
        return guard_violations(
            self.context,
            self.phase,
            self._baseline,
        )

    def _rebase(self) -> None:
        paths = upstream_guard_paths(
            self.context,
            self.phase,
            owned_paths=self.owned_paths,
        )
        self._baseline = capture_guard_hashes(self.context, paths)

    def run_runner_mutation(
        self,
        label: str,
        callback: Callable[[], Any],
        *,
        exact_files: Sequence[str],
        allowed_prefixes: Sequence[str] = (),
        allow_deletions: bool = False,
        error_getter: Callable[[Any], Sequence[str]] | None = None,
    ) -> Any:
        """Run one exact runner mutation and rebase only after verified success.

        `error_getter` lets ingest-style callbacks return an ordinary result carrying
        errors. Such a result is returned unchanged, but its changes are not rebased.
        Exceptions likewise never rebase. A path-policy violation raises
        `RunnerMutationViolation` and takes precedence over the callback result/exception.
        """

        existing_errors = self.violations()
        if existing_errors:
            raise RunnerMutationViolation(label, existing_errors)

        exact = {str(path).rstrip("/") for path in exact_files if str(path)}
        prefixes = {
            str(path).rstrip("/") for path in allowed_prefixes if str(path).rstrip("/")
        }
        # Observe the complete run-local workspace, while granting writes only through the
        # exact/prefix capability below. This catches an accidental new sibling file rather
        # than limiting the audit to paths that were already provenance-tracked.
        observation_prefixes = {
            self.context.artifacts.work_run_dir.rstrip("/"),
            self.context.artifacts.outputs_dir.rstrip("/"),
        }
        before_paths = set(self._baseline) | exact | prefixes
        for prefix in prefixes | observation_prefixes:
            before_paths.update(_prefix_entry_paths(self.context.root, prefix))
        before = _capture_guard_states(self.context, tuple(before_paths))

        result: Any = None
        callback_error: BaseException | None = None
        try:
            result = callback()
        except BaseException as exc:  # cancellation/interrupt still require mutation audit
            callback_error = exc

        after_paths = set(before)
        after_paths.update(
            upstream_guard_paths(
                self.context,
                self.phase,
                owned_paths=self.owned_paths,
            )
        )
        for prefix in prefixes | observation_prefixes:
            after_paths.update(_prefix_entry_paths(self.context.root, prefix))
        after = _capture_guard_states(self.context, tuple(after_paths))

        def is_allowed(rel: str) -> bool:
            return rel in exact or any(
                rel == prefix or rel.startswith(f"{prefix}/")
                for prefix in prefixes
            )

        policy_errors: list[str] = []
        for rel in sorted(set(before) | set(after)):
            old_state = before.get(rel)
            new_state = after.get(rel)
            if old_state == new_state:
                continue
            if not is_allowed(rel):
                policy_errors.append(
                    f"runner mutation `{label}` changed a path outside its capability: {rel}"
                )
                continue
            if old_state is not None and new_state is None and not allow_deletions:
                policy_errors.append(
                    f"runner mutation `{label}` deleted a path without permission: {rel}"
                )
        if policy_errors:
            violation = RunnerMutationViolation(label, policy_errors)
            if callback_error is not None:
                raise violation from callback_error
            raise violation
        if callback_error is not None:
            raise callback_error

        result_errors = tuple(error_getter(result)) if error_getter is not None else ()
        if not result_errors:
            self._rebase()
        return result
