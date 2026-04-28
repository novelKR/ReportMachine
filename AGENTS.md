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
- Latest mirror: after a successful, validated run, `outputs/latest/` may be updated from `outputs/{run_id}/`.
- Generated artifacts must stay under `work/runs/{run_id}/`, `outputs/{run_id}/`, or `outputs/latest/` unless the user explicitly asks for repo configuration edits.

## Safety And Quality Rules

- Use repo-relative paths only. Do not hard-code OS-specific paths such as `/Users/...` or `C:\Users\...`.
- Do not generate fictional source URLs, fictional numbers, or fictional author experiences.
- Do not use author anecdotes beyond `prompts/AUTHOR_PROFILE.md`.
- Do not write investment recommendations, target prices, buy/sell opinions, valuation calls, or price forecasts.
- Do not use AI-generated images in article outputs.
- Every quantitative claim must be tied to a primary or explicitly qualified source.
