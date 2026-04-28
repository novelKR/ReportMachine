from __future__ import annotations

import csv
import json
import re
from pathlib import Path
from typing import Any

from .paths import resolve_under_root
from .schema_validation import validate_json_artifact


PROHIBITED_PATTERNS = [
    r"목표\s*주가",
    r"매수\s*(?:추천|의견)?",
    r"매도\s*(?:추천|의견)?",
    r"투자\s*추천",
    r"\bbuy\b",
    r"\bsell\b",
    r"\bhold\b",
    r"\btarget price\b",
]


def _read_text(root: Path, rel: str) -> str:
    path = resolve_under_root(root, rel)
    return path.read_text(encoding="utf-8") if path.is_file() else ""


def _read_json(root: Path, rel: str, default: Any) -> Any:
    path = resolve_under_root(root, rel)
    if not path.is_file():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return default


def _read_csv(root: Path, rel: str) -> list[dict[str, str]]:
    path = resolve_under_root(root, rel)
    if not path.is_file():
        return []
    with path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        return [
            {key: (value or "") for key, value in row.items() if key is not None}
            for row in reader
        ]


def _numbers(text: str) -> set[str]:
    return set(re.findall(r"(?<![A-Za-z0-9_.])\d+(?:[.,]\d+)*(?:\.\d+)?%?", text))


def _source_evidence_text(root: Path, source_ledger: str, pdf_index: str) -> str:
    parts: list[str] = []
    for row in _read_csv(root, source_ledger):
        parts.extend(str(value) for value in row.values())
    index_path = resolve_under_root(root, pdf_index)
    if index_path.is_file():
        with index_path.open(encoding="utf-8") as handle:
            for line in handle:
                try:
                    item = json.loads(line)
                except json.JSONDecodeError:
                    continue
                parts.append(str(item.get("excerpt", "")))
    return " ".join(parts)


def _audit_claim_ledger(root: Path, claim_ledger: str) -> list[str]:
    claims, errors = validate_json_artifact(
        root=root,
        artifact=claim_ledger,
        schema="schemas/claim_ledger.schema.json",
        label="claim ledger",
    )
    if claims is None:
        return errors
    if not isinstance(claims, list):
        errors.append(f"claim ledger is not a JSON array: {claim_ledger}")
        return errors
    for item in claims:
        if not isinstance(item, dict):
            errors.append("claim ledger contains a non-object item")
            continue
        claim_id = str(item.get("claim_id", "unknown"))
        support_status = item.get("support_status")
        needs_human_review = item.get("needs_human_review")
        if support_status in {"unsupported", "needs_review"} or needs_human_review is True:
            errors.append(f"claim ledger blocking claim: {claim_id} has support_status={support_status}")
    return errors


def _audit_image_captions(
    *,
    root: Path,
    article: str,
    image_ledger: str,
    source_ledger: str,
    pdf_index: str,
) -> list[str]:
    rows = _read_csv(root, image_ledger)
    evidence_text = _source_evidence_text(root, source_ledger, pdf_index)
    errors: list[str] = []
    for index, row in enumerate(rows, start=1):
        keep_or_drop = row.get("keep_or_drop", "").strip().lower()
        if keep_or_drop not in {"keep", "kept", "true", "yes"}:
            continue
        image_path = row.get("image_path", "").strip()
        if image_path and image_path not in article:
            errors.append(f"kept image is not referenced in article: {image_path}")
        if not row.get("article_anchor_before", "").strip():
            errors.append(f"kept image row {index} is missing article_anchor_before")
        if not row.get("article_anchor_after", "").strip():
            errors.append(f"kept image row {index} is missing article_anchor_after")
        caption = row.get("caption", "") or row.get("caption_candidate", "")
        data_points = " ".join(
            [
                row.get("actual_slide_title", ""),
                row.get("detected_key_datapoints", ""),
                row.get("caption_basis", ""),
                evidence_text,
            ]
        )
        for number in _numbers(caption):
            if number not in data_points:
                errors.append(f"caption number lacks visible/source support in image row {index}: {number}")
    return errors


def run_finalize_audits(
    *,
    root: Path,
    mode: str,
    article_result: str,
    article_output: str,
    claim_ledger_output: str,
    image_ledger_output: str,
    source_ledger_output: str,
    pdf_index: str,
) -> list[str]:
    errors: list[str] = []
    article = _read_text(root, article_output)
    result = _read_json(root, article_result, {})

    for pattern in PROHIBITED_PATTERNS:
        if re.search(pattern, article, flags=re.IGNORECASE):
            errors.append(f"article contains prohibited investment language: {pattern}")

    if result.get("article_type") == "preview" and not resolve_under_root(root, "prompts/PREVIEW_PROMPT.md").is_file():
        errors.append("preview result cannot pass because prompts/PREVIEW_PROMPT.md is missing")

    if mode == "earnings" and result.get("checklist_status") in {"pass", "pass_with_warnings"}:
        if "## 한눈에 보기" not in article:
            errors.append("earnings article is missing the required `## 한눈에 보기` scorecard heading")

    errors.extend(_audit_claim_ledger(root, claim_ledger_output))
    errors.extend(
        _audit_image_captions(
            root=root,
            article=article,
            image_ledger=image_ledger_output,
            source_ledger=source_ledger_output,
            pdf_index=pdf_index,
        )
    )
    return errors
