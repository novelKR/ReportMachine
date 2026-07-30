from __future__ import annotations

import csv
import json
import shutil
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
from typing import Sequence

from .codex_exec import CodexExecConfig
from .paths import resolve_under_root
from .schema_validation import validate_json_artifact


IMAGE_LEDGER_HEADER = [
    "image_path",
    "source_doc",
    "source_id",
    "page_or_slide_number",
    "source_hash",
    "image_hash",
    "inspected_status",
    "caption_basis",
    "visual_question",
    "supported_claim_ids",
    "why_prose_is_insufficient",
    "actual_slide_title",
    "detected_key_datapoints",
    "caption",
    "caption_candidate",
    "article_anchor_before",
    "article_anchor_after",
    "keep_or_drop",
    "drop_reason",
]


def output_files_payload(
    *,
    article_output: str,
    source_ledger_output: str,
    image_ledger_output: str,
    claim_ledger_output: str,
    preflight_result_output: str,
    run_manifest_output: str,
    earnings_delta_matrix_output: str | None = None,
    earnings_delta_matrix_md: str | None = None,
) -> dict[str, str | None]:
    return {
        "article_md": article_output,
        "source_ledger_csv": source_ledger_output,
        "image_ledger_csv": image_ledger_output,
        "claim_ledger_json": claim_ledger_output,
        "preflight_result_json": preflight_result_output,
        "run_manifest_json": run_manifest_output,
        "earnings_delta_matrix_json": earnings_delta_matrix_output,
        "earnings_delta_matrix_md": earnings_delta_matrix_md,
    }


def write_prepare_manifest(
    *,
    root: Path,
    mode: str,
    config: CodexExecConfig,
    command: Sequence[str],
    topic_path: str,
    sources_path: str,
    prepare_manifest: str,
    prompt: str,
) -> None:
    manifest = {
        "version": 1,
        "mode": mode,
        "run_id": config.run_id,
        "generated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "execution": {
            "prepared_for": "app_native_fallback_or_manual_codex_cli",
            "codex_command": list(command),
            "codex_cli_required_for_command": True,
        },
        "paths": {
            "topic": topic_path,
            "sources": sources_path,
            "work_run_dir": config.work_run_dir,
            "source_ingest_manifest": config.source_ingest_manifest,
            "source_candidate_ledger": config.source_candidate_ledger,
            "pdf_index": config.pdf_index,
            "rendered_images_ledger": config.rendered_images_ledger,
            "article_output": config.article_output,
            "source_ledger_output": config.source_ledger_output,
            "image_ledger_output": config.image_ledger_output,
            "result_output": config.output,
        },
        "prompt": prompt,
    }
    target = resolve_under_root(root, prepare_manifest)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def write_json_artifact(root: Path, path: str, payload: object) -> None:
    target = resolve_under_root(root, path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def write_empty_image_ledger(root: Path, path: str) -> None:
    target = resolve_under_root(root, path)
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=IMAGE_LEDGER_HEADER)
        writer.writeheader()


def copy_or_write_source_ledger(
    *,
    root: Path,
    source_candidate_ledger: str,
    source_ledger_output: str,
) -> None:
    target = resolve_under_root(root, source_ledger_output)
    target.parent.mkdir(parents=True, exist_ok=True)
    source = resolve_under_root(root, source_candidate_ledger)
    if source.is_file():
        shutil.copy2(source, target)
        return
    with target.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=["source_id", "url", "local_path", "sha256", "ingest_status", "blocked_reason"],
        )
        writer.writeheader()


def write_empty_earnings_delta(root: Path, json_path: str, md_path: str, reason: str) -> None:
    write_json_artifact(
        root,
        json_path,
        {
            "delta_table": [],
            "disappeared_highlights": [],
            "wording_shifts": [],
            "guidance_assumption_changes": [],
            "ir_frame": "",
            "counter_reading": "",
            "scorecard_candidate": {
                "total": 0,
                "headline": 0,
                "quality": 0,
                "leading_indicators": 0,
                "transparency": 0,
                "execution": 0,
                "summary": reason,
            },
            "next_quarter_verification_points": [],
        },
    )
    target = resolve_under_root(root, md_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(f"# Earnings delta matrix unavailable\n\n{reason}\n", encoding="utf-8")


def write_preflight_failure_outputs(
    *,
    root: Path,
    mode: str,
    preflight_payload: dict,
    output: str,
    article_output: str,
    source_ledger_output: str,
    image_ledger_output: str,
    claim_ledger_output: str,
    preflight_result_output: str,
    run_manifest_output: str,
    earnings_delta_matrix_output: str,
    earnings_delta_matrix_md: str,
    source_candidate_ledger: str,
) -> None:
    warnings = list(preflight_payload.get("blocking_errors") or [])
    article = [
        "# Preflight failure",
        "",
        "This is a non-publishable failure note. ReportMachine stopped before drafting because required materials were missing or unusable.",
        "",
        *[f"- {item}" for item in warnings],
        "",
    ]
    article_path = resolve_under_root(root, article_output)
    article_path.write_text("\n".join(article), encoding="utf-8")
    # `article_sha256` is unconditionally required, so hash the failure note itself
    # instead of emitting a placeholder that no artifact matches.
    article_digest = sha256(article_path.read_bytes()).hexdigest()
    copy_or_write_source_ledger(
        root=root,
        source_candidate_ledger=source_candidate_ledger,
        source_ledger_output=source_ledger_output,
    )
    write_empty_image_ledger(root, image_ledger_output)
    write_json_artifact(root, claim_ledger_output, [])
    if mode == "earnings":
        write_empty_earnings_delta(
            root,
            earnings_delta_matrix_output,
            earnings_delta_matrix_md,
            "Preflight failed before earnings delta analysis.",
        )
    write_json_artifact(
        root,
        output,
        {
            "article_type": mode,
            "article_sha256": article_digest,
            "title": "Preflight failed",
            "subtitle": "Required source materials were missing or unusable.",
            "one_sentence_insight": "",
            "scorecard": None,
            "output_files": output_files_payload(
                article_output=article_output,
                source_ledger_output=source_ledger_output,
                image_ledger_output=image_ledger_output,
                claim_ledger_output=claim_ledger_output,
                preflight_result_output=preflight_result_output,
                run_manifest_output=run_manifest_output,
                earnings_delta_matrix_output=earnings_delta_matrix_output if mode == "earnings" else None,
                earnings_delta_matrix_md=earnings_delta_matrix_md if mode == "earnings" else None,
            ),
            "critical_warnings": warnings,
            "checklist_status": "fail",
        },
    )


def article_result_status(root: Path, output: str) -> str:
    path = resolve_under_root(root, output)
    if not path.is_file():
        return ""
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return ""
    return str(payload.get("checklist_status", ""))


def write_run_summary_log(
    *,
    root: Path,
    run_id: str,
    phase: str,
    status: str,
    preflight_status: str,
    article_result: str,
    events_log: str,
    stderr_log: str,
    provenance_status: str | None = None,
) -> None:
    payload = {
        "run_id": run_id,
        "phase": phase,
        "status": status,
        "preflight_status": preflight_status,
        "article_result": article_result,
        "events_log": events_log,
        "stderr_log": stderr_log,
    }
    if provenance_status is not None:
        payload["provenance_status"] = provenance_status
    write_json_artifact(
        root,
        f"logs/{run_id}/run_summary.json",
        payload,
    )


def validate_earnings_delta_outputs(config: CodexExecConfig, mode: str) -> list[str]:
    if mode != "earnings":
        return []
    if article_result_status(config.root, config.output) == "fail":
        return []
    errors: list[str] = []
    for value in [config.earnings_delta_matrix_output, config.earnings_delta_matrix_md]:
        if not resolve_under_root(config.root, value).is_file():
            errors.append(f"missing configured earnings delta output: {value}")
    if errors:
        return errors
    _, schema_errors = validate_json_artifact(
        root=config.root,
        artifact=config.earnings_delta_matrix_output,
        schema="schemas/earnings_delta.schema.json",
        label="earnings delta",
    )
    errors.extend(schema_errors)
    return errors
