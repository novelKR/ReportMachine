from __future__ import annotations

import argparse
from typing import Sequence

from .phase_contracts import ANALYSIS_MODES, DEPTH_CHOICES, PHASE_CHOICES
from .workflow import run_article_workflow


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run the ReportMachine Codex article workflow.")
    parser.add_argument("--mode", choices=["industry", "earnings", "preview"], help="Article workflow mode. Required unless the selected config profile provides it.")
    parser.add_argument(
        "--phase",
        choices=list(PHASE_CHOICES),
        default="all",
        help=(
            "Workflow phase to run. Phases run in order: prepare, scope, research-plan, research, "
            "evidence, narrative, draft, review, revise, finalize, audit. Defaults to all."
        ),
    )
    parser.add_argument(
        "--allow-unprovenanced-audit",
        action="store_true",
        help=(
            "Allow a code-only audit when the run has neither a provenance marker nor any "
            "checkpoint. The result is recorded as waived and never promoted to outputs/latest."
        ),
    )
    parser.add_argument("--config", help="TOML runner preset file. Relative paths are resolved from the project root.")
    parser.add_argument("--profile", default="default", help="Profile name under [profiles.<name>] in the TOML preset.")
    parser.add_argument("--run-id", help="Run id used for work/runs/{run_id} and outputs/{run_id}. Defaults to current UTC timestamp.")
    parser.add_argument("--project-root", help="ReportMachine project root. Defaults to REPORTMACHINE_ROOT or marker search from cwd.")
    parser.add_argument("--topic")
    parser.add_argument("--sources")
    parser.add_argument("--schema")
    parser.add_argument("--output", help="Final assistant JSON path. Defaults to outputs/{run_id}/article_result.json.")
    parser.add_argument("--article-output", help="Final article path. Defaults to outputs/{run_id}/article.md.")
    parser.add_argument("--source-ledger-output", help="Source ledger path. Defaults to outputs/{run_id}/source_ledger.csv.")
    parser.add_argument("--image-ledger-output", help="Image ledger path. Defaults to outputs/{run_id}/image_ledger.csv.")
    parser.add_argument("--codex-bin", default="codex")
    parser.add_argument(
        "--web-search",
        choices=["cached", "live", "disabled"],
        help="Codex web search mode. Defaults to cached for industry and live for earnings.",
    )
    parser.add_argument(
        "--allow-shell-network",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="Allow shell outbound network in the Codex workspace-write sandbox for this run.",
    )
    parser.add_argument(
        "--source-ingest",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="Preprocess sources into run-local ledgers, PDF text, and rendered images before Codex runs.",
    )
    parser.add_argument(
        "--source-domain",
        action="append",
        default=None,
        help="Additional official/allowed HTTPS domain for automatic source downloads. May be repeated.",
    )
    parser.add_argument(
        "--max-download-bytes",
        type=int,
        help="Maximum bytes per downloaded source. Defaults to 50 MiB.",
    )
    parser.add_argument(
        "--pdf-render-dpi",
        type=int,
        help="DPI for rendered PDF page images. Defaults to 144.",
    )
    parser.add_argument(
        "--max-pdf-pages",
        type=int,
        help="Maximum pages per PDF to extract/render. Defaults to 40.",
    )
    parser.add_argument(
        "--max-image-inputs",
        type=int,
        help="Maximum rendered images passed to Codex CLI --image. Defaults to 20.",
    )
    parser.add_argument(
        "--intake-topic",
        help="Approved conversational intake topic. Writes run-local intake inputs before execution.",
    )
    parser.add_argument(
        "--intake-core-question",
        default="",
        help="Approved central question for generated run-local intake topic.md.",
    )
    parser.add_argument(
        "--intake-recent-trigger",
        default="",
        help="Approved recent trigger note for generated run-local intake topic.md.",
    )
    parser.add_argument(
        "--intake-source-note",
        action="append",
        default=None,
        help="Approved source requirement note for generated run-local sources.csv. May be repeated.",
    )
    parser.add_argument(
        "--intake-output-notes",
        default="",
        help="Approved output notes for generated run-local intake topic.md.",
    )
    parser.add_argument(
        "--intake-mirror-inputs",
        action="store_true",
        help="Also mirror generated intake files to inputs/topic.md and inputs/sources.csv.",
    )
    parser.add_argument(
        "--intake-original-request",
        help="Verbatim user request text. Preserved for the scope gate and intent contract.",
    )
    parser.add_argument(
        "--intake-original-request-file",
        help="Path to a file holding the verbatim user request. Relative paths resolve from the project root.",
    )
    parser.add_argument(
        "--intake-approve-scope",
        action="store_true",
        help="Record user approval of the normalized scope so drafting phases may run unattended.",
    )
    parser.add_argument(
        "--intake-reset-intent-contract",
        action="store_true",
        help=(
            "Back up and replace a malformed or wrong-run intent contract with a pending "
            "contract for the current run. Scope approval remains a separate step."
        ),
    )
    parser.add_argument(
        "--intake-depth",
        choices=list(DEPTH_CHOICES),
        default=None,
        help="Analytical depth for this run. Defaults to standard. Replaces line-count targets.",
    )
    parser.add_argument(
        "--intake-analysis-mode",
        action="append",
        choices=list(ANALYSIS_MODES),
        default=None,
        help="Approved analysis mode. May be repeated, for example market_reaction plus company_fundamental.",
    )
    parser.add_argument(
        "--waive-blocked-source",
        action="append",
        default=None,
        help="Downgrade a blocked required source to a warning by source id. May be repeated.",
    )
    parser.add_argument(
        "--max-extra-research-rounds",
        type=int,
        default=None,
        help="Maximum extra targeted research rounds when p0 evidence gaps remain. Defaults to 1.",
    )
    parser.add_argument(
        "--prepare-only",
        action="store_true",
        help="Prepare directories, source ingest artifacts, and a prompt manifest without running Codex.",
    )
    parser.add_argument(
        "--json-events",
        action="store_true",
        help="Run codex exec with --json and save newline-delimited events under logs/{run_id}/.",
    )
    parser.add_argument(
        "--summary-output",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="After a successful article run, generate derivative A4 summary output under outputs/{run_id}/summary.",
    )
    parser.add_argument(
        "--publish-pdf",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="After a successful article run, render a reader-friendly A4 PDF under outputs/{run_id}/publish.",
    )
    parser.add_argument("--dry-run", action="store_true", help="Print the command and prompt without running Codex.")
    parser.add_argument("--smoke-test", action="store_true", help="Build the command and prompt without creating directories or running Codex.")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return run_article_workflow(parser, args)
