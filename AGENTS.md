# ReportMachine Agent Guide

ReportMachine is a Codex-oriented workspace for generating Korean industry analysis articles from repository-local prompt contracts.

## Prompt Routing

- General industry, technology, supply-chain, regulation, or market-structure analysis:
  - Use `$industry-article`.
  - Follow `prompts/PROMPT.md`, `prompts/FORMAT.md`, `prompts/SOURCE_POLICY.md`, and `prompts/AUTHOR_PROFILE.md`.
- Published earnings, IR deck, or conference-call based analysis:
  - Use `$earnings-article`.
  - Follow `prompts/PROMPT.md`, `prompts/FORMAT.md`, `prompts/EARNINGS_PROMPT.md`, `prompts/SOURCE_POLICY.md`, and `prompts/AUTHOR_PROFILE.md`.
  - If prompt rules conflict, `prompts/EARNINGS_PROMPT.md` overrides the general prompt for earnings-specific requirements.
- QA-only requests:
  - Use `$article-qa`.
- Conversational article intake requests:
  - Use `$article-intake`.
  - Use it when the user provides a short natural-language topic and wants Codex to ask the missing questions, produce a plan, and after approval prepare/run the article workflow.
  - Default to run-local generated inputs under `work/runs/{run_id}/intake/`; mirror to `inputs/topic.md` and `inputs/sources.csv` only when the user explicitly approves that persistence target.
- Repo usage, onboarding, setup, execution, and troubleshooting questions:
  - Use `$reportmachine-usage`.
  - Follow `docs/USAGE.ko.md` first, then `README.md` or `AGENTS.md` only when exact runner or routing details are needed.
  - Do not use this route for article drafting, article QA, or conversational topic intake; route those to the article skills above.
- Derivative A4 summary outputs after article completion:
  - Use `$summary-output`.
  - Summary outputs are not article outputs, evidence sources, or replacements for `source_ledger.csv` or `image_ledger.csv`.
  - The image generation request must use Codex internal image generation tools only; the runner must not call OpenAI API endpoints, File Search, vector stores, or external image APIs.
- Preview articles:
  - Do not treat preview requests as ordinary industry analysis.
  - If `prompts/PREVIEW_PROMPT.md` is absent, return a schema-valid failure JSON with `article_type = "preview"`, `checklist_status = "fail"`, and a `critical_warnings` entry that the preview-specific prompt contract is missing.

## Main Agent Duties

- Classify the task type before drafting.
- Spawn sub agents aggressively for automated report generation, research planning, earnings review, evidence validation, image or slide validation, and prompt compliance review.
- Keep sub agents focused on intermediate analysis. Read-only sub agents must return results to the parent agent as messages rather than writing files. The main agent owns final conflict resolution and final response synthesis.
- Do not let multiple agents write the same final file concurrently.

## Required Sub Agent Flow

- General article:
  - `source_researcher`
  - `narrative_architect`
  - `citation_checker`
  - `qa_reviewer`
- Earnings article:
  - `source_researcher`
  - `earnings_delta_analyst`
  - `image_slide_auditor`
  - `narrative_architect`
  - `citation_checker`
  - `qa_reviewer`
- Draft writing:
  - Use `article_writer` only after research, evidence, and narrative artifacts exist.

## Artifact Paths

- Inputs: `inputs/topic.md`, `inputs/sources.csv`
- Intermediate artifacts: `work/runs/{run_id}/research/`, `work/runs/{run_id}/ledgers/`, `work/runs/{run_id}/images/`, `work/runs/{run_id}/drafts/`, `work/runs/{run_id}/reviews/`
- Final artifacts: `outputs/{run_id}/article.md`, `outputs/{run_id}/source_ledger.csv`, `outputs/{run_id}/image_ledger.csv`
- Structured result: `outputs/{run_id}/article_result.json` is written only by `codex exec -o` from the final assistant message, not directly by an agent.
- App-native fallback exception: after `scripts/run_article.py --prepare-only`, if `codex` CLI is unavailable and the current Codex App session is completing the workflow directly, the parent agent may write `outputs/{run_id}/article_result.json` itself. It must match `schemas/article_result.schema.json` and preserve the same output-file contract.
- Derivative summary artifacts: `outputs/{run_id}/summary/summary_a4.png`, `outputs/{run_id}/summary/summary_prompt.md`, `outputs/{run_id}/summary/summary_claim_map.csv`, `outputs/{run_id}/summary/summary_manifest.json`, `outputs/{run_id}/summary/summary_result.json`
- Structured summary result: `outputs/{run_id}/summary/summary_result.json` must match `schemas/summary_result.schema.json`.
- Latest mirror: after a successful, validated run, `outputs/latest/` may be updated from `outputs/{run_id}/`.
- Generated artifacts must stay under `work/runs/{run_id}/`, `outputs/{run_id}/`, or `outputs/latest/` unless the user explicitly asks for repo configuration edits.
- External artifact roots configured by runner TOML presets are runner-only import/export targets. Agents must not write external paths directly.

## Safety And Quality Rules

- Use repo-relative paths only. Do not hard-code OS-specific paths such as `/Users/...` or `C:\Users\...`.
- Do not generate fictional source URLs, fictional numbers, or fictional author experiences.
- Do not use author anecdotes beyond `prompts/AUTHOR_PROFILE.md`.
- Do not write investment recommendations, target prices, buy/sell opinions, valuation calls, or price forecasts.
- Do not use AI-generated images in article outputs.
- AI-generated A4 images are allowed only as derivative summary outputs under `outputs/{run_id}/summary/` and must not introduce new facts, numbers, source attributions, charts, or captions absent from the completed article and ledgers.
- Every quantitative claim must be tied to a primary or explicitly qualified source.
