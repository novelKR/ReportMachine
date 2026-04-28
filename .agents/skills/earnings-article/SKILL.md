---
name: earnings-article
description: Use for Korean articles based on published company earnings releases, investor presentations, IR decks, or conference-call transcripts.
---

# Earnings Article Skill

## References

Read these files before planning or drafting:

- `prompts/PROMPT.md`
- `prompts/FORMAT.md`
- `prompts/EARNINGS_PROMPT.md`
- `prompts/SOURCE_POLICY.md`
- `prompts/AUTHOR_PROFILE.md`

If `prompts/EARNINGS_PROMPT.md` conflicts with a general rule, the earnings-specific prompt wins for earnings analysis. Investment recommendation and valuation prohibitions still apply.

## Workflow

1. Confirm the work is based on already published earnings, IR, or conference-call material.
2. Spawn `source_researcher` to collect current-quarter and prior-quarter materials.
3. Spawn `earnings_delta_analyst` to compare quarter-over-quarter materials and produce scorecard evidence.
4. Spawn `image_slide_auditor` if images, charts, PDFs, slides, or captions are involved.
5. Spawn `narrative_architect` to connect the earnings evidence into one central narrative.
6. Use `article_writer` only after the prior artifacts exist.
7. Spawn `citation_checker` and `qa_reviewer` before final delivery.
8. Parent/main agent integrates the final article and final structured response.

## Required Intermediate Artifacts

- Current-quarter materials list.
- Prior-quarter materials list.
- Delta table.
- Disappeared-highlight exploration result.
- IR frame and counter-reading.
- Five-axis scorecard draft and rationale.
- Image or slide ledger when applicable.

## Hard Rules

- Do not conclude from current-quarter materials alone.
- Do not invent disappeared highlights; if none are found, state that and analyze wording, ordering, unit, and risk-language changes.
- Do not infer slide contents from slide number alone.
- Do not convert earnings analysis into valuation advice.
