"""Validate phase artifacts against their schemas.

JSON artifacts are validated whole. JSONL artifacts are validated row by row so that a
single malformed evidence card is reported with its line number instead of failing opaquely.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .paths import resolve_under_root
from .phase_contracts import ArtifactContract
from .schema_validation import load_schema_validator, validate_json_artifact


def _load_schema(root: Path, schema: str) -> tuple[dict[str, Any] | None, list[str]]:
    path = resolve_under_root(root, schema)
    if not path.is_file():
        return None, [f"missing schema: {schema}"]
    try:
        return json.loads(path.read_text(encoding="utf-8")), []
    except json.JSONDecodeError as exc:
        return None, [f"invalid schema JSON: {schema}: {exc.msg}"]


def read_jsonl(root: Path, rel: str) -> tuple[list[Any], list[str]]:
    """Return parsed JSONL rows plus parse errors."""

    path = resolve_under_root(root, rel)
    if not path.is_file():
        return [], [f"missing artifact: {rel}"]
    rows: list[Any] = []
    errors: list[str] = []
    for line_number, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        line = raw.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError as exc:
            errors.append(f"invalid JSONL row in {rel} line {line_number}: {exc.msg}")
    return rows, errors


def duplicate_object_ids(rows: list[Any], *, id_field: str) -> tuple[str, ...]:
    """Return stable ids that occur in more than one JSONL object."""

    seen: set[str] = set()
    duplicates: set[str] = set()
    for row in rows:
        if not isinstance(row, dict):
            continue
        value = str(row.get(id_field) or "").strip()
        if not value:
            continue
        if value in seen:
            duplicates.add(value)
        seen.add(value)
    return tuple(sorted(duplicates))


def validate_run_identity(
    payload: Any,
    *,
    expected_run_id: str,
    label: str,
    artifact: str | None = None,
) -> list[str]:
    """Bind a run-scoped JSON object to the runner's current identity."""

    if not isinstance(payload, dict):
        return []
    actual_run_id = str(payload.get("run_id") or "").strip()
    if actual_run_id == expected_run_id:
        return []
    location = f": {artifact}" if artifact else ""
    return [
        f"{label} run_id mismatch: expected `{expected_run_id}`, "
        f"found `{actual_run_id or 'missing'}`{location}"
    ]


def validate_jsonl_artifact(
    *,
    root: Path,
    artifact: str,
    schema: str,
    label: str,
    require_rows: bool = True,
) -> tuple[list[Any], list[str]]:
    rows, errors = read_jsonl(root, artifact)
    if errors and not rows:
        return rows, errors
    if require_rows and not rows:
        errors.append(f"{label} has no rows: {artifact}")
        return rows, errors

    schema_payload, schema_errors = _load_schema(root, schema)
    errors.extend(schema_errors)
    if schema_payload is None:
        return rows, errors

    validator, dependency_errors = load_schema_validator(schema_payload)
    if validator is None:
        errors.extend(dependency_errors)
        return rows, errors
    for index, row in enumerate(rows, start=1):
        for error in sorted(validator.iter_errors(row), key=lambda item: item.message):
            errors.append(f"{label} row {index} schema validation failed: {error.message}")
    return rows, errors


def validate_artifact_contract(
    root: Path,
    contract: ArtifactContract,
    *,
    expected_run_id: str | None = None,
) -> list[str]:
    path = resolve_under_root(root, contract.path)
    if not path.is_file():
        return [f"missing {contract.label}: {contract.path}"]
    if contract.schema is None:
        if path.stat().st_size == 0:
            return [f"empty {contract.label}: {contract.path}"]
        return []
    if contract.jsonl:
        _, errors = validate_jsonl_artifact(
            root=root,
            artifact=contract.path,
            schema=contract.schema,
            label=contract.label,
            require_rows=False,
        )
        return errors
    payload, errors = validate_json_artifact(
        root=root,
        artifact=contract.path,
        schema=contract.schema,
        label=contract.label,
    )
    if contract.run_id_scoped and expected_run_id is not None:
        errors.extend(
            validate_run_identity(
                payload,
                expected_run_id=expected_run_id,
                label=contract.label,
                artifact=contract.path,
            )
        )
    return errors


def validate_artifact_syntax(
    root: Path,
    contract: ArtifactContract,
    *,
    expected_run_id: str | None = None,
) -> list[str]:
    """Validate existence and parseability without importing jsonschema."""

    path = resolve_under_root(root, contract.path)
    if not path.is_file():
        return [f"missing {contract.label}: {contract.path}"]
    if contract.schema is None:
        if path.stat().st_size == 0:
            return [f"empty {contract.label}: {contract.path}"]
        return []
    if contract.jsonl:
        _rows, errors = read_jsonl(root, contract.path)
        return errors
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        return [f"invalid {contract.label} JSON: {contract.path}: {exc.msg}"]
    if not isinstance(payload, dict):
        return [f"invalid {contract.label}: expected a JSON object: {contract.path}"]
    if contract.run_id_scoped and expected_run_id is not None:
        return validate_run_identity(
            payload,
            expected_run_id=expected_run_id,
            label=contract.label,
            artifact=contract.path,
        )
    return []


def validate_artifact_contracts(
    root: Path,
    contracts: tuple[ArtifactContract, ...],
    *,
    validate_schemas: bool = True,
    expected_run_id: str | None = None,
) -> list[str]:
    errors: list[str] = []
    for contract in contracts:
        validator = validate_artifact_contract if validate_schemas else validate_artifact_syntax
        errors.extend(
            validator(
                root,
                contract,
                expected_run_id=expected_run_id,
            )
        )
    return errors
