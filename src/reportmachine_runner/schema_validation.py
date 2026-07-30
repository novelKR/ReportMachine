from __future__ import annotations

import importlib.util
import json
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from .paths import resolve_under_root


SCHEMA_VALIDATION_PACKAGE = "jsonschema"
SCHEMA_VALIDATION_INSTALL_HINT = (
    f"missing required runtime dependency: {SCHEMA_VALIDATION_PACKAGE}. "
    "Schema validation of pipeline artifacts cannot run without it. "
    "Install project dependencies first: python3 -m pip install -e ."
)
SCHEMA_VALIDATION_UNAVAILABLE_PREFIX = "schema validation unavailable"


def schema_validation_dependency_error() -> str | None:
    """Return an actionable message when schema validation cannot run.

    Callers should probe this before any phase that requires schema validation,
    so the run fails with an actionable message before Codex spends budget. Draft
    has an explicit parse-only compatibility path. The summary workflow intentionally skips this probe because its
    validation is hand-written CSV and JSON checking that never imports the
    schema-validation package.
    """

    if importlib.util.find_spec(SCHEMA_VALIDATION_PACKAGE) is None:
        return SCHEMA_VALIDATION_INSTALL_HINT
    return None


def load_schema_validator(schema_payload: Any) -> tuple[Any | None, list[str]]:
    """Build a validator, or report that the schema dependency is unusable.

    ``schema_validation_dependency_error`` only probes for the package. Import can
    still fail on a broken or partially installed dependency, so validation must
    fail closed here instead of raising and aborting the caller's error handling.
    """

    try:
        from jsonschema import Draft202012Validator
    except ImportError as exc:
        return None, [
            f"{SCHEMA_VALIDATION_UNAVAILABLE_PREFIX}: {exc}. {SCHEMA_VALIDATION_INSTALL_HINT}"
        ]
    return Draft202012Validator(schema_payload), []


def _format_json_path(parts: Iterable[object]) -> str:
    path = "$"
    for part in parts:
        if isinstance(part, int):
            path += f"[{part}]"
        else:
            path += f".{part}"
    return path


def validate_json_artifact(
    *,
    root: Path,
    artifact: str,
    schema: str,
    label: str,
) -> tuple[Any | None, list[str]]:
    artifact_path = resolve_under_root(root, artifact)
    if not artifact_path.is_file():
        return None, [f"missing {label}: {artifact}"]
    try:
        payload = json.loads(artifact_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        return None, [f"invalid {label} JSON: {artifact}: {exc.msg}"]

    schema_path = resolve_under_root(root, schema)
    if not schema_path.is_file():
        return payload, [f"missing {label} schema: {schema}"]
    try:
        schema_payload = json.loads(schema_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        return payload, [f"invalid {label} schema JSON: {schema}: {exc.msg}"]

    validator, dependency_errors = load_schema_validator(schema_payload)
    if validator is None:
        return payload, dependency_errors
    errors = sorted(
        validator.iter_errors(payload),
        key=lambda error: (
            tuple(str(part) for part in error.absolute_path),
            error.message,
        ),
    )
    return payload, [
        f"{label} schema validation failed at {_format_json_path(error.absolute_path)}: {error.message}"
        for error in errors
    ]
