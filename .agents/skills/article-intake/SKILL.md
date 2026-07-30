---
name: article-intake
description: Use when a user gives a short natural-language ReportMachine article topic and wants Codex to ask clarifying questions, produce a plan, then after approval prepare run-local topic and source inputs and run or prepare the article workflow.
---

# Article Intake Skill

## Purpose

Turn a short chat request such as "AI 데이터센터 전력 인프라 병목과 전력기기 공급망으로 기사 작성" into an approved ReportMachine run.

## References

Read these before planning or execution:

- `AGENTS.md`
- `docs/USAGE.ko.md`
- `prompts/core/MODE_CONTRACT.md`
- `schemas/intent_contract.schema.json`
- `inputs/topic.md` and `inputs/sources.csv`

For the final article workflow, route to `$industry-article` or `$earnings-article` after intake is approved.

## Intake Workflow

1. Classify the likely mode: `industry` for technology, supply chain, regulation, or market-structure topics; `earnings` only for already published earnings, IR deck, filing, or conference-call work.
2. Preserve the user's original request verbatim. Extract the central question, every required subject and comparison target, requested time range, recent trigger, and requested conclusion shape.
3. Ask only for missing high-impact choices: mode, analysis modes, depth, required source material, output shape, and whether to mirror generated inputs to `inputs/`.
4. In Plan Mode, do not write files or run commands. Choose a new, collision-resistant run id and produce a complete `<proposed_plan>` with the verbatim request, normalized scope, proposed exclusions with reasons, input persistence target, execution path, and source risks. Never reuse an existing run directory unless the user explicitly asks to resume it.
5. After explicit approval in Default mode, use the runner intake flags to write run-local inputs and the intent contract. Pass `--intake-approve-scope` only for the scope the user actually approved.
6. If the user approves mirroring, pass `--intake-mirror-inputs`; otherwise keep all generated intake files run-local.
7. If exact source URLs or repo-local source files are absent, retain the source-discovery row and state that discovery is not evidence. Do not approve unsupported quantitative claims.
8. Run `--phase all` only after approval. If the scope guardian reports a new material delta, stop and return that delta to the user.

## Execution Routing

Preferred CLI path when `codex` is available:

```bash
python3 scripts/run_article.py --mode {mode} --run-id {run_id} \
  --intake-original-request "{verbatim_request}" \
  --intake-topic "{topic}" \
  --intake-core-question "{core_question}" \
  --intake-recent-trigger "{recent_trigger}" \
  --intake-source-note "{source_note}" \
  --intake-output-notes "{output_notes}" \
  --intake-analysis-mode {analysis_mode} \
  --intake-depth {depth} \
  --intake-approve-scope
```

App-native fallback when `codex` CLI is unavailable:

```bash
python3 scripts/run_article.py --prepare-only --mode {mode} --run-id {run_id} \
  --intake-original-request "{verbatim_request}" \
  --intake-topic "{topic}" \
  --intake-core-question "{core_question}" \
  --intake-recent-trigger "{recent_trigger}" \
  --intake-source-note "{source_note}" \
  --intake-output-notes "{output_notes}" \
  --intake-analysis-mode {analysis_mode} \
  --intake-depth {depth} \
  --intake-approve-scope
```

Repeat `--intake-analysis-mode` for every approved mode. For multiline requests or text containing shell-sensitive characters, prefer `--intake-original-request-file {repo_relative_path}` and preserve its bytes unchanged.

Then use `work/runs/{run_id}/prepare_manifest.json`, execute the app-native role flow from `AGENTS.md`, serialize read-only role results, and finish with `scripts/run_article.py --mode {mode} --phase audit --run-id {run_id} --allow-unprovenanced-audit`.

The explicit waiver is only for this checkpoint-free App-native path. It records `provenance_status: waived_no_checkpoint`, never updates `outputs/latest/`, and never backfills provenance. Do not use it to excuse a marker, a partial or corrupt checkpoint set, or any finalize checkpoint mismatch.

The runner's exact-output allowlist, role-attempt cleanup, and `outputs/latest` restoration apply to runner-managed CLI role calls, not to direct App-native role execution. In the App-native flow, keep `outputs/latest` untouched, use `reviews/image_audit.json` as the only image-decision artifact, never create an image-ledger candidate, and provide rendered image inputs only to `image_slide_auditor`.

## Hard Rules

- Do not invent source URLs, numbers, recent triggers, or author experiences during intake.
- Do not treat source discovery as evidence.
- Do not run the full workflow before the user approves the intake plan.
- Do not silently drop a subject, comparison target, time range, or requested analysis mode from the original request.
- Do not approve a material scope change on the user's behalf.
- Keep generated artifacts under `work/runs/{run_id}/`, `outputs/{run_id}/`, or `outputs/latest/`.
- Do not treat those broad directories as CLI role output permissions. The runner creates declared output parents and permits only exact regular-file outputs.
