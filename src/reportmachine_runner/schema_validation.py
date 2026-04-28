from __future__ import annotations

import json
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from .paths import resolve_under_root


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
