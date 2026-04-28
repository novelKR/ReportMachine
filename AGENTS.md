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
  - If `prompts/PREVIEW_PROMPT.md` is absent, stop and report that the preview-specific prompt contract is missing.

## Main Agent Duties

- Classify the task type before drafting.
- Spawn sub agents aggressively for automated report generation, research planning, earnings review, evidence validation, image or slide validation, and prompt compliance review.
- Keep sub agents focused on intermediate artifacts. The main agent owns final conflict resolution and final response synthesis.
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
- Intermediate artifacts: `work/research/`, `work/ledgers/`, `work/images/`, `work/drafts/`
- Final artifacts: `outputs/article.md`, `outputs/article_result.json`, `outputs/source_ledger.csv`, `outputs/image_ledger.csv`
- Generated artifacts must stay under `work/` or `outputs/` unless the user explicitly asks for repo configuration edits.

## Safety And Quality Rules

- Use repo-relative paths only. Do not hard-code OS-specific paths such as `/Users/...` or `C:\Users\...`.
- Do not generate fictional source URLs, fictional numbers, or fictional author experiences.
- Do not use author anecdotes beyond `prompts/AUTHOR_PROFILE.md`.
- Do not write investment recommendations, target prices, buy/sell opinions, valuation calls, or price forecasts.
- Do not use AI-generated images in article outputs.
- Every quantitative claim must be tied to a primary or explicitly qualified source.
