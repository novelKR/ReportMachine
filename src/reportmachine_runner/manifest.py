from __future__ import annotations

import csv
import json
import subprocess
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
from typing import Any

from .paths import resolve_under_root
from .agent_attempts import collect_agent_executions
from .roles import CORE_PROMPT_FILES, ROLE_CARDS


PROMPT_FILES_BY_MODE = {
    "industry": [
        "prompts/PROMPT.md",
        "prompts/FORMAT.md",
        "prompts/SOURCE_POLICY.md",
        "prompts/AUTHOR_PROFILE.md",
    ],
    "earnings": [
        "prompts/PROMPT.md",
        "prompts/FORMAT.md",
        "prompts/EARNINGS_PROMPT.md",
        "prompts/SOURCE_POLICY.md",
        "prompts/AUTHOR_PROFILE.md",
    ],
    "preview": [
        "prompts/PROMPT.md",
        "prompts/FORMAT.md",
        "prompts/PREVIEW_PROMPT.md",
        "prompts/SOURCE_POLICY.md",
        "prompts/AUTHOR_PROFILE.md",
    ],
}

SCHEMA_FILES = [
    "schemas/article_result.schema.json",
    "schemas/preflight_result.schema.json",
    "schemas/claim_ledger.schema.json",
    "schemas/earnings_delta.schema.json",
    "schemas/intent_contract.schema.json",
    "schemas/evidence_card.schema.json",
    "schemas/research_question_tree.schema.json",
    "schemas/thesis_decision.schema.json",
    "schemas/narrative_plan.schema.json",
    "schemas/paragraph_map.schema.json",
    "schemas/final_sync_report.schema.json",
    "schemas/citation_logic_report.schema.json",
    "schemas/red_team_report.schema.json",
    "schemas/qa_report.schema.json",
    "schemas/compression_report.schema.json",
    "schemas/image_audit_report.schema.json",
    "schemas/revision_notes.schema.json",
]

AGENT_CONFIG_GLOB = ".codex/agents"


def _agent_config_files(root: Path) -> list[str]:
    directory = root / AGENT_CONFIG_GLOB
    if not directory.is_dir():
        return []
    return sorted(
        path.relative_to(root).as_posix() for path in directory.glob("*.toml")
    )


def effective_prompt_hashes(root: Path, contracts_dir: str | None) -> dict[str, str | None]:
    if not contracts_dir:
        return {}
    try:
        directory = resolve_under_root(root, contracts_dir)
    except ValueError:
        return {}
    if not directory.is_dir():
        return {}
    hashes: dict[str, str | None] = {}
    for path in sorted(directory.glob("effective_prompt_*.md")):
        hashes[path.relative_to(root.resolve()).as_posix()] = file_sha256(path)
    return hashes


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def file_sha256(path: Path) -> str | None:
    if not path.is_file():
        return None
    digest = sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return f"sha256:{digest.hexdigest()}"


def _hash_repo_files(root: Path, rels: list[str]) -> dict[str, str | None]:
    hashes: dict[str, str | None] = {}
    for rel in rels:
        try:
            hashes[rel] = file_sha256(resolve_under_root(root, rel))
        except ValueError:
            hashes[rel] = None
    return hashes


def _read_source_hashes(root: Path, source_candidate_ledger: str) -> dict[str, str]:
    path = resolve_under_root(root, source_candidate_ledger)
    if not path.is_file():
        return {}
    hashes: dict[str, str] = {}
    with path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            source_id = (row.get("source_id") or "").strip()
            digest = (row.get("sha256") or "").strip()
            if source_id and digest:
                hashes[source_id] = f"sha256:{digest}"
    return hashes


def _git_output(root: Path, args: list[str]) -> str:
    process = subprocess.run(
        ["git", *args],
        cwd=root,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    return process.stdout.strip() if process.returncode == 0 else ""


def _workspace_snapshot_hash(root: Path) -> str:
    rels = [
        "AGENTS.md",
        "README.md",
        "pyproject.toml",
        "scripts/run_article.py",
        "scripts/verify_layout.py",
    ] + PROMPT_FILES_BY_MODE["earnings"] + SCHEMA_FILES
    digest = sha256()
    for rel in sorted(set(rels)):
        path = root / rel
        if not path.is_file():
            continue
        digest.update(rel.encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return f"sha256:{digest.hexdigest()}"


def _codex_version(codex_bin: str, root: Path) -> dict[str, Any]:
    try:
        process = subprocess.run(
            [codex_bin, "--version"],
            cwd=root,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"available": False, "version": "", "error": str(exc)}
    output = (process.stdout or process.stderr).strip()
    return {
        "available": process.returncode == 0,
        "version": output,
        "error": "" if process.returncode == 0 else output,
    }


def build_run_manifest(
    *,
    root: Path,
    mode: str,
    run_id: str,
    phase: str,
    codex_bin: str,
    schema: str,
    web_search: str,
    allow_shell_network: bool,
    source_ingest_enabled: bool,
    source_candidate_ledger: str,
    paths: dict[str, str | None],
    agents_dir: str | None = None,
    contracts_dir: str | None = None,
    phase_history: list[dict[str, Any]] | None = None,
    analysis_modes: tuple[str, ...] = (),
    depth: str = "",
) -> dict[str, Any]:
    git_commit = _git_output(root, ["rev-parse", "HEAD"])
    prompt_files = PROMPT_FILES_BY_MODE.get(mode, PROMPT_FILES_BY_MODE["industry"])
    prompt_files = list(prompt_files) + list(CORE_PROMPT_FILES)
    return {
        "version": 1,
        "run_id": run_id,
        "article_type": mode,
        "phase": phase,
        "analysis_modes": list(analysis_modes),
        "depth": depth,
        "generated_at": _now_iso(),
        "git": {
            "commit": git_commit,
            "workspace_snapshot_sha256": "" if git_commit else _workspace_snapshot_hash(root),
        },
        "codex_cli": _codex_version(codex_bin, root),
        "prompt_hashes": _hash_repo_files(root, prompt_files),
        "role_card_hashes": _hash_repo_files(root, list(ROLE_CARDS)),
        "agent_config_hashes": _hash_repo_files(root, _agent_config_files(root)),
        "effective_prompt_hashes": effective_prompt_hashes(root, contracts_dir),
        "schema_hashes": _hash_repo_files(root, sorted(set(SCHEMA_FILES + [schema]))),
        "source_hashes": _read_source_hashes(root, source_candidate_ledger),
        "agent_executions": collect_agent_executions(root, agents_dir) if agents_dir else [],
        "phase_history": phase_history or [],
        "codex_config_effective": {
            "web_search": web_search,
            "sandbox_mode": "workspace-write",
            "shell_network": allow_shell_network,
            "source_ingest": source_ingest_enabled,
        },
        "paths": paths,
    }


def write_run_manifest(root: Path, output_path: str, manifest: dict[str, Any]) -> None:
    target = resolve_under_root(root, output_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
