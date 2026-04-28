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
- `inputs/topic.md`
- `inputs/sources.csv`
- `README.md` runner section

For the final article workflow, route to `$industry-article` or `$earnings-article` after intake is approved.

## Intake Workflow

1. Classify the likely mode: `industry` for technology, supply chain, regulation, or market-structure topics; `earnings` only for already published earnings, IR deck, filing, or conference-call work.
2. Ask only for missing high-impact fields: article mode, central question, recent trigger, required source material, output shape, and whether to mirror generated inputs to `inputs/`.
3. In Plan Mode, do not write files or run commands. Produce a complete `<proposed_plan>` with the chosen run id, input persistence target, execution path, and missing source risks.
4. After user approval in Default mode, write run-local inputs by using `scripts/run_article.py` intake flags or by creating equivalent files under `work/runs/{run_id}/intake/`.
5. If the user explicitly approves mirroring, also pass `--intake-mirror-inputs` or update `inputs/topic.md` and `inputs/sources.csv`.
6. If exact source URLs or repo-local source files are not provided, keep the generated `sources.csv` source-discovery row and state that quantitative claims remain blocked until primary sources are found or uncertainty qualifiers are used.

## Execution Routing

Preferred CLI path when `codex` is available:

```bash
python3 scripts/run_article.py --mode industry --run-id {run_id} \
  --intake-topic "{topic}" \
  --intake-core-question "{core_question}" \
  --intake-recent-trigger "{recent_trigger}" \
  --intake-source-note "{source_note}" \
  --intake-output-notes "{output_notes}"
```

App-native fallback when `codex` CLI is unavailable:

```bash
python3 scripts/run_article.py --prepare-only --mode industry --run-id {run_id} \
  --intake-topic "{topic}" \
  --intake-core-question "{core_question}" \
  --intake-recent-trigger "{recent_trigger}" \
  --intake-source-note "{source_note}" \
  --intake-output-notes "{output_notes}"
```

Then use `work/runs/{run_id}/prepare_manifest.json` as the current-session execution prompt and write final outputs under `outputs/{run_id}/` according to `AGENTS.md`.

## Hard Rules

- Do not invent source URLs, numbers, recent triggers, or author experiences during intake.
- Do not treat source discovery as evidence.
- Do not run the full workflow before the user approves the intake plan.
- Keep generated artifacts under `work/runs/{run_id}/`, `outputs/{run_id}/`, or `outputs/latest/`.
