from __future__ import annotations

import argparse
from pathlib import Path
from typing import Sequence

from .codex_exec import CodexExecConfig, build_command, ensure_output_directories, run_command
from .paths import as_posix_arg, find_project_root
from .prompt import build_prompt


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run the ReportMachine Codex article workflow.")
    parser.add_argument("--mode", choices=["industry", "earnings"], required=True)
    parser.add_argument("--project-root", help="ReportMachine project root. Defaults to REPORTMACHINE_ROOT or marker search from cwd.")
    parser.add_argument("--topic", default="inputs/topic.md")
    parser.add_argument("--sources", default="inputs/sources.csv")
    parser.add_argument("--schema", default="schemas/article_result.schema.json")
    parser.add_argument("--output", default="outputs/article_result.json")
    parser.add_argument("--article-output", default="outputs/article.md")
    parser.add_argument("--source-ledger-output", default="outputs/source_ledger.csv")
    parser.add_argument("--image-ledger-output", default="outputs/image_ledger.csv")
    parser.add_argument("--codex-bin", default="codex")
    parser.add_argument("--dry-run", action="store_true", help="Print the command and prompt without running Codex.")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    root = Path(args.project_root).expanduser().resolve() if args.project_root else find_project_root()

    prompt = build_prompt(
        mode=args.mode,
        topic_path=as_posix_arg(args.topic),
        sources_path=as_posix_arg(args.sources),
        article_output=as_posix_arg(args.article_output),
        source_ledger_output=as_posix_arg(args.source_ledger_output),
        image_ledger_output=as_posix_arg(args.image_ledger_output),
        result_output=as_posix_arg(args.output),
    )

    config = CodexExecConfig(
        root=root,
        codex_bin=args.codex_bin,
        schema=as_posix_arg(args.schema),
        output=as_posix_arg(args.output),
        article_output=as_posix_arg(args.article_output),
        source_ledger_output=as_posix_arg(args.source_ledger_output),
        image_ledger_output=as_posix_arg(args.image_ledger_output),
        prompt=prompt,
    )
    command = build_command(config)

    if args.dry_run:
        print("cwd:", root)
        print("command:", command)
        print("prompt:")
        print(prompt)
        return 0

    ensure_output_directories(config)
    return run_command(command, cwd=root)
