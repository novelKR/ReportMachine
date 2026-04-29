from __future__ import annotations

import csv
import json

from .summary_contracts import (
    SUMMARY_CLAIM_MAP_HEADER,
    SUMMARY_CLAIM_MAP_REQUIRED_FIELDS,
    SUMMARY_CLAIM_MAP_SOURCE_ARTIFACTS,
    SUMMARY_IMAGE_GENERATION_SURFACE,
    SummaryRunConfig,
    _output_files,
    _read_json,
    _repo_path,
)


def validate_summary_result(
    config: SummaryRunConfig,
    *,
    expected_hashes: dict[str, str] | None = None,
) -> list[str]:
    errors: list[str] = []
    result_path = _repo_path(config.root, config.paths.summary_result)
    if not result_path.is_file():
        return [f"missing summary result: {config.paths.summary_result}"]

    try:
        payload = _read_json(result_path)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        return [f"invalid summary result JSON: {exc}"]

    if payload.get("image_generation_surface") != SUMMARY_IMAGE_GENERATION_SURFACE:
        errors.append("summary result must use codex_internal_tool image_generation_surface")
    if payload.get("source_article") != config.sources.article:
        errors.append("summary result source_article does not match configured article path")
    if payload.get("source_result") != config.sources.article_result:
        errors.append("summary result source_result does not match configured article result path")
    if expected_hashes is not None:
        if payload.get("source_article_sha256") != expected_hashes.get("article_sha256"):
            errors.append("summary result source_article_sha256 does not match article hash")
        if payload.get("source_result_sha256") != expected_hashes.get("article_result_sha256"):
            errors.append("summary result source_result_sha256 does not match article result hash")

    output_files = payload.get("output_files")
    if not isinstance(output_files, dict):
        errors.append("summary result output_files must be an object")
    else:
        for key, expected in _output_files(config).items():
            actual = output_files.get(key)
            if actual != expected:
                errors.append(f"summary result output_files.{key} must be {expected}")

    for label, value in [
        ("summary_prompt", config.paths.summary_prompt),
        ("summary_claim_map", config.paths.summary_claim_map),
        ("summary_manifest", config.paths.summary_manifest),
    ]:
        if not _repo_path(config.root, value).is_file():
            errors.append(f"missing {label}: {value}")

    status = payload.get("checklist_status")
    if status in {"pass", "pass_with_warnings"}:
        if not _repo_path(config.root, config.paths.summary_image).is_file():
            errors.append(f"missing generated summary image: {config.paths.summary_image}")
        if _repo_path(config.root, config.paths.summary_claim_map).is_file():
            errors.extend(validate_summary_claim_map(config))
    return errors


def validate_summary_claim_map(config: SummaryRunConfig) -> list[str]:
    claim_map = _repo_path(config.root, config.paths.summary_claim_map)
    if not claim_map.is_file():
        return [f"missing summary_claim_map: {config.paths.summary_claim_map}"]

    errors: list[str] = []
    data_row_found = False
    try:
        with claim_map.open(newline="", encoding="utf-8") as handle:
            reader = csv.reader(handle)
            try:
                header = next(reader)
            except StopIteration:
                return [
                    "summary claim map header must match contract: "
                    f"{config.paths.summary_claim_map}"
                ]
            if header != SUMMARY_CLAIM_MAP_HEADER:
                return [
                    "summary claim map header must match contract: "
                    f"{config.paths.summary_claim_map}"
                ]
            for row_number, row in enumerate(reader, start=2):
                if not any(cell.strip() for cell in row):
                    continue
                data_row_found = True
                row_values = {
                    field: row[index].strip() if index < len(row) else ""
                    for index, field in enumerate(SUMMARY_CLAIM_MAP_HEADER)
                }
                if len(row) != len(SUMMARY_CLAIM_MAP_HEADER):
                    errors.append(
                        f"summary claim map row {row_number} must have "
                        f"{len(SUMMARY_CLAIM_MAP_HEADER)} columns: "
                        f"{config.paths.summary_claim_map}"
                    )
                missing = [
                    field
                    for field in SUMMARY_CLAIM_MAP_REQUIRED_FIELDS
                    if not row_values[field]
                ]
                if missing:
                    errors.append(
                        f"summary claim map row {row_number} missing required fields "
                        f"{', '.join(missing)}: {config.paths.summary_claim_map}"
                    )
                source_artifact = row_values["source_artifact"]
                if (
                    source_artifact
                    and source_artifact not in SUMMARY_CLAIM_MAP_SOURCE_ARTIFACTS
                ):
                    allowed = ", ".join(SUMMARY_CLAIM_MAP_SOURCE_ARTIFACTS)
                    errors.append(
                        f"summary claim map row {row_number} source_artifact must be "
                        f"one of {allowed}: {config.paths.summary_claim_map}"
                    )
    except OSError as exc:
        return [f"invalid summary claim map: {exc}"]

    if not data_row_found:
        return [
            "summary claim map must include at least one data row: "
            f"{config.paths.summary_claim_map}"
        ]
    return errors
