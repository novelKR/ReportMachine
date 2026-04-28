from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

from .paths import resolve_under_root


@dataclass(frozen=True)
class CodexExecConfig:
    root: Path
    codex_bin: str
    schema: str
    output: str
    article_output: str
    source_ledger_output: str
    image_ledger_output: str
    prompt: str


def ensure_output_directories(config: CodexExecConfig) -> None:
    for value in [
        config.output,
        config.article_output,
        config.source_ledger_output,
        config.image_ledger_output,
    ]:
        resolve_under_root(config.root, value).parent.mkdir(parents=True, exist_ok=True)


def build_command(config: CodexExecConfig) -> list[str]:
    return [
        config.codex_bin,
        "exec",
        "--skip-git-repo-check",
        "--full-auto",
        "--output-schema",
        config.schema,
        "-o",
        config.output,
        config.prompt,
    ]


def run_command(command: Sequence[str], *, cwd: Path) -> int:
    env = os.environ.copy()
    return subprocess.run(list(command), cwd=cwd, env=env, text=True, check=False).returncode
