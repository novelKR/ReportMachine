---
name: article-qa
description: Use to review ReportMachine article drafts against prompt, source, format, earnings, image, and author-profile contracts without rewriting the article.
---

# Article QA Skill

## References

Read these files before reviewing:

- `prompts/PROMPT.md`
- `prompts/FORMAT.md`
- `prompts/SOURCE_POLICY.md`
- `prompts/AUTHOR_PROFILE.md`
- `prompts/EARNINGS_PROMPT.md` when the task is earnings-related.

## Workflow

1. Identify the article type: `industry`, `earnings`, or `preview`.
2. For preview articles, fail if `prompts/PREVIEW_PROMPT.md` is absent.
3. Spawn `citation_checker` for source and quantitative-claim review.
4. Spawn `qa_reviewer` for full prompt-contract review.
5. Parent/main agent summarizes findings and only rewrites if explicitly asked.

## Review Focus

- One central narrative and non-obvious insight.
- No section-listing failure.
- Recent event used as trigger or evidence, not as the whole frame.
- Source URLs and uncertainty labels for quantitative claims.
- Author-profile compliance.
- Image and caption policy compliance.
- Earnings scorecard, prior-quarter delta, IR counter-reading, and counter-balance section when applicable.
