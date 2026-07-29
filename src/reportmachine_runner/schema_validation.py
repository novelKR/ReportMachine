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
    "Schema validation of final artifacts cannot run without it. "
    "Install project dependencies first: python3 -m pip install -e ."
)


def schema_validation_dependency_error() -> str | None:
    """Return an actionable message when schema validation cannot run.

    Callers should probe this only before article finalize-phase work, which is
    where JSON artifacts are validated against schemas. The summary workflow
    intentionally skips this probe because its validation is hand-written CSV
    and JSON checking that never imports the schema-validation package.
    """

    if importlib.util.find_spec(SCHEMA_VALIDATION_PACKAGE) is None:
        return SCHEMA_VALIDATION_INSTALL_HINT
    return None


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

    from jsonschema import Draft202012Validator

    validator = Draft202012Validator(schema_payload)
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
