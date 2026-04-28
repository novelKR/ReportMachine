from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path

from .paths import resolve_under_root


INPUT_SOURCES_HEADER = [
    "source_id",
    "article_type",
    "company",
    "period_role",
    "period",
    "doc_kind",
    "issuer",
    "date",
    "title",
    "url",
    "source_tier",
    "required",
    "local_path",
    "notes",
]

SOURCE_DISCOVERY_REQUIRED_ID = "source_discovery_required"
SOURCE_DISCOVERY_REQUIRED_NOTE = (
    "source discovery required; do not publish quantitative claims until primary "
    "sources are added or explicit uncertainty qualifiers are used"
)


@dataclass(frozen=True)
class ArticleBrief:
    mode: str
    topic: str
    core_question: str = ""
    recent_trigger: str = ""
    required_source_notes: tuple[str, ...] = ()
    output_notes: str = ""


@dataclass(frozen=True)
class IntakePaths:
    topic: str
    sources: str
    mirrored_topic: str | None = None
    mirrored_sources: str | None = None


def intake_topic_path(run_id: str) -> str:
    return f"work/runs/{run_id}/intake/topic.md"


def intake_sources_path(run_id: str) -> str:
    return f"work/runs/{run_id}/intake/sources.csv"


def render_topic_markdown(brief: ArticleBrief) -> str:
    source_notes = "\n".join(
        f"- {note}" for note in brief.required_source_notes if note.strip()
    )
    if not source_notes:
        source_notes = f"- {SOURCE_DISCOVERY_REQUIRED_NOTE}"

    return "\n".join(
        [
            "# ReportMachine Topic Input",
            "",
            "Generated from an approved conversational intake brief.",
            "",
            "## Task Type",
            "",
            brief.mode,
            "",
            "## Topic",
            "",
            brief.topic.strip(),
            "",
            "## Core Question",
            "",
            brief.core_question.strip() or "Confirm the central analytical question before drafting.",
            "",
            "## Recent Trigger",
            "",
            brief.recent_trigger.strip()
            or "Find a recent original trigger before drafting; do not invent one.",
            "",
            "## Required Source Notes",
            "",
            source_notes,
            "",
            "## Output Notes",
            "",
            brief.output_notes.strip() or "full report",
            "",
        ]
    )


def source_discovery_required_row(brief: ArticleBrief) -> dict[str, str]:
    notes = "; ".join(note.strip() for note in brief.required_source_notes if note.strip())
    if notes:
        notes = f"{SOURCE_DISCOVERY_REQUIRED_NOTE}; requested source notes: {notes}"
    else:
        notes = SOURCE_DISCOVERY_REQUIRED_NOTE

    return {
        "source_id": SOURCE_DISCOVERY_REQUIRED_ID,
        "article_type": brief.mode,
        "company": "",
        "period_role": "",
        "period": "",
        "doc_kind": "source_discovery",
        "issuer": "",
        "date": "",
        "title": "Source discovery required before publishable drafting",
        "url": "",
        "source_tier": "",
        "required": "true",
        "local_path": "",
        "notes": notes,
    }


def write_sources_csv(path: Path, rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=INPUT_SOURCES_HEADER)
        writer.writeheader()
        writer.writerows(rows)


def write_intake_files(
    *,
    root: Path,
    run_id: str,
    brief: ArticleBrief,
    mirror_inputs: bool = False,
) -> IntakePaths:
    topic_rel = intake_topic_path(run_id)
    sources_rel = intake_sources_path(run_id)
    topic_path = resolve_under_root(root, topic_rel)
    sources_path = resolve_under_root(root, sources_rel)

    topic_path.parent.mkdir(parents=True, exist_ok=True)
    topic_path.write_text(render_topic_markdown(brief), encoding="utf-8")
    write_sources_csv(sources_path, [source_discovery_required_row(brief)])

    mirrored_topic = None
    mirrored_sources = None
    if mirror_inputs:
        mirrored_topic = "inputs/topic.md"
        mirrored_sources = "inputs/sources.csv"
        mirrored_topic_path = resolve_under_root(root, mirrored_topic)
        mirrored_topic_path.parent.mkdir(parents=True, exist_ok=True)
        mirrored_topic_path.write_text(
            topic_path.read_text(encoding="utf-8"),
            encoding="utf-8",
        )
        write_sources_csv(
            resolve_under_root(root, mirrored_sources),
            [source_discovery_required_row(brief)],
        )

    return IntakePaths(
        topic=topic_rel,
        sources=sources_rel,
        mirrored_topic=mirrored_topic,
        mirrored_sources=mirrored_sources,
    )
