from __future__ import annotations

import argparse
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Sequence

from .codex_exec import (
    CodexExecConfig,
    build_command,
    ensure_output_directories,
    make_file_snapshot,
    mirror_latest_outputs,
    run_command,
    validate_file_snapshot,
)
from .paths import as_posix_arg, find_project_root
from .prompt import build_prompt


RUN_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")


def default_run_id() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run the ReportMachine Codex article workflow.")
    parser.add_argument("--mode", choices=["industry", "earnings"], required=True)
    parser.add_argument("--run-id", help="Run id used for work/runs/{run_id} and outputs/{run_id}. Defaults to current UTC timestamp.")
    parser.add_argument("--project-root", help="ReportMachine project root. Defaults to REPORTMACHINE_ROOT or marker search from cwd.")
    parser.add_argument("--topic", default="inputs/topic.md")
    parser.add_argument("--sources", default="inputs/sources.csv")
    parser.add_argument("--schema", default="schemas/article_result.schema.json")
    parser.add_argument("--output", help="Final assistant JSON path. Defaults to outputs/{run_id}/article_result.json.")
    parser.add_argument("--article-output", help="Final article path. Defaults to outputs/{run_id}/article.md.")
    parser.add_argument("--source-ledger-output", help="Source ledger path. Defaults to outputs/{run_id}/source_ledger.csv.")
    parser.add_argument("--image-ledger-output", help="Image ledger path. Defaults to outputs/{run_id}/image_ledger.csv.")
    parser.add_argument("--codex-bin", default="codex")
    parser.add_argument("--allow-shell-network", action="store_true", help="Allow shell outbound network in the Codex workspace-write sandbox for this run.")
    parser.add_argument("--dry-run", action="store_true", help="Print the command and prompt without running Codex.")
    parser.add_argument("--smoke-test", action="store_true", help="Build the command and prompt without creating directories or running Codex.")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    root = Path(args.project_root).expanduser().resolve() if args.project_root else find_project_root()
    run_id = args.run_id or default_run_id()
    if not RUN_ID_PATTERN.fullmatch(run_id):
        parser.error("--run-id must start with an ASCII letter or digit and may also contain underscore, hyphen, and dot")

    work_run_dir = f"work/runs/{run_id}"
    draft_output = f"{work_run_dir}/drafts/draft_article.md"
    image_notes_dir = f"{work_run_dir}/images"
    image_ledger_candidate = f"{work_run_dir}/ledgers/image_ledger.candidate.json"

    output = args.output or f"outputs/{run_id}/article_result.json"
    article_output = args.article_output or f"outputs/{run_id}/article.md"
    source_ledger_output = args.source_ledger_output or f"outputs/{run_id}/source_ledger.csv"
    image_ledger_output = args.image_ledger_output or f"outputs/{run_id}/image_ledger.csv"

    prompt = build_prompt(
        mode=args.mode,
        run_id=run_id,
        topic_path=as_posix_arg(args.topic),
        sources_path=as_posix_arg(args.sources),
        work_run_dir=as_posix_arg(work_run_dir),
        draft_output=as_posix_arg(draft_output),
        image_notes_dir=as_posix_arg(image_notes_dir),
        image_ledger_candidate=as_posix_arg(image_ledger_candidate),
        article_output=as_posix_arg(article_output),
        source_ledger_output=as_posix_arg(source_ledger_output),
        image_ledger_output=as_posix_arg(image_ledger_output),
        result_output=as_posix_arg(output),
    )

    config = CodexExecConfig(
        root=root,
        codex_bin=args.codex_bin,
        run_id=run_id,
        schema=as_posix_arg(args.schema),
        output=as_posix_arg(output),
        article_output=as_posix_arg(article_output),
        source_ledger_output=as_posix_arg(source_ledger_output),
        image_ledger_output=as_posix_arg(image_ledger_output),
        work_run_dir=as_posix_arg(work_run_dir),
        draft_output=as_posix_arg(draft_output),
        image_notes_dir=as_posix_arg(image_notes_dir),
        image_ledger_candidate=as_posix_arg(image_ledger_candidate),
        latest_dir="outputs/latest",
        live_web_search=True,
        allow_shell_network=args.allow_shell_network,
        prompt=prompt,
    )
    command = build_command(config)

    if args.dry_run or args.smoke_test:
        print("cwd:", root)
        print("run_id:", run_id)
        print("command:", command)
        print("prompt:")
        print(prompt)
        if args.smoke_test:
            print("smoke-test: command and prompt built without execution")
        return 0

    ensure_output_directories(config)
    snapshot = make_file_snapshot(config)
    status = run_command(command, cwd=root)
    if status != 0:
        return status

    validation_errors = validate_file_snapshot(config, snapshot)
    if validation_errors:
        print("Post-run validation failed:", file=sys.stderr)
        for error in validation_errors:
            print(f" - {error}", file=sys.stderr)
        return 2

    mirror_latest_outputs(config)
    return 0
