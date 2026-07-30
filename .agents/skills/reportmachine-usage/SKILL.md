---
name: reportmachine-usage
description: Use for ReportMachine repo onboarding, purpose, usage, Codex App vs Codex CLI execution, runner options, input/output paths, source CSV guidance, smoke-test, prepare-only, troubleshooting, and "how do I use this repo?" questions. Do not use for drafting or reviewing article content.
---

# ReportMachine Usage Skill

## Purpose

Answer initial user questions about how to understand, configure, and run this repository without loading the full article-writing prompt stack.

Use this skill for questions such as:

- "이 Repo 목적과 사용 방법 설명해줘"
- "Codex App에서 어떻게 실행하나요?"
- "Codex CLI로 직접 실행하려면?"
- "`inputs/topic.md`와 `inputs/sources.csv`는 어떻게 쓰나요?"
- "`--smoke-test`, `--prepare-only`, `--allow-shell-network` 차이는?"
- "`--phase` 단계와 scope 승인은 어떻게 동작하나요?"
- "기존 런을 모델 호출 없이 어떻게 감사하나요?"
- "산출물은 어디에 생기나요?"
- "`--summary-output`은 무엇을 만드나요?"

Do not use this skill when the user asks to create, draft, or QA an article. Route those tasks to `$article-intake`, `$industry-article`, `$earnings-article`, or `$article-qa`.

## References

Read only the minimum needed:

- Start with `docs/USAGE.ko.md` for user-facing usage explanations.
- Read `AGENTS.md` only when routing, skill choice, or artifact ownership matters.
- Read `README.md` only when confirming runner commands, project trust, TOML presets, or release/archive details.
- Read `inputs/topic.md` and `inputs/sources.csv` only when explaining input templates.
- Read `scripts/run_article.py --help` or `src/reportmachine_runner/cli.py` only when the exact CLI option behavior is unclear.
- Read `src/reportmachine_runner/phase_contracts.py` only when exact phase ordering or upstream artifact gates are unclear.

## Response Rules

- Answer in Korean unless the user asks otherwise.
- Keep paths repo-relative in explanations.
- Distinguish Codex App usage from Codex CLI runner usage.
- Explain that `--phase all` runs `prepare -> scope -> research-plan -> research -> evidence -> narrative -> draft -> review -> revise -> finalize -> audit`, and that a single later phase fails when required upstream artifacts are absent.
- Explain that each completed model phase records a checkpoint, while the code-only audit neither creates nor backfills one. The finalize checkpoint includes schema-validated `article_result.json` in its protected contract.
- Explain that runner-managed CLI roles may write only their declared exact regular files and that the runner, not the role, creates output parents. Symlink, directory, special-file, or undeclared outputs fail closed, and write-scope checks plus attempt recording run on every catchable exit path.
- Explain that `outputs/latest` is runner-owned. Before a role call its root must be absent or a real directory; catchable role failure, write violation, or latest drift restores and verifies that existing state. A root symlink/file/special entry or a failed restore is a hard failure. Clarify the single-runner/no-concurrent-reader/catchable-exit assumption and that this does not provide rollback of other upstream files, external atomic staging, a multi-file commit journal, crash recovery, same-run locking, or hostile-role isolation for out-of-repo writes, network activity, and secrets.
- Explain that an upstream tracked-artifact, canonical-ledger, extracted-text, or rendered-image violation blocks checkpoint, final export, audit success, and latest promotion. Recommend the earliest producer only when valid lineage proves it; otherwise recommend `--phase all`.
- Explain that audit requires matching finalize provenance by default. Historical or App-native code-only audit needs the CLI-only `--allow-unprovenanced-audit`; it records `waived_no_checkpoint`, never updates `outputs/latest/`, and works only when no marker or checkpoint file exists. Partial, corrupt, non-object, marked, missing-finalize, or mismatched provenance cannot be waived.
- Explain that the verbatim request and user-approved intent contract are required for scope fidelity; `scope_status=conflicting` cannot be overridden by the approval flag.
- Distinguish `--smoke-test`, `--dry-run`, `--prepare-only`, a completed model phase, and a successful code-only audit. None of the first four alone proves a publishable run.
- For status questions, inspect `preflight_result.json`, `article_result.json`, `final_sync_report.json`, and `logs/{run_id}/run_summary.json` separately.
- Explain that research-discovered URLs undergo a second runner-owned ingest and receive source ids, hashes, statuses, and extracted text before evidence verification; blocked or remote-unverified candidates remain unusable as verified facts.
- Explain that `reviews/image_audit.json` is the sole canonical image-decision artifact, not an image-ledger candidate. Only `image_slide_auditor` receives rendered image inputs; final image-ledger shared decisions and `caption_candidate` are audited against the report and rendered ledger, while a context-derived final `caption` may differ only if the numeric, evidence, source, and render checks pass.
- Make clear that web search is candidate discovery only; final claims must trace to repo-local sources and ledgers.
- Mention that OpenAI API keys, File Search, and vector-store uploads are not part of the runner.
- Mention that summary images use Codex internal image generation tools only and remain separate from article outputs.
- If the user wants a runnable article workflow from a short topic, route to `$article-intake` rather than expanding usage instructions.
