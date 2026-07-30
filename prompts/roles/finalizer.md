# ROLE: finalizer

## Goal

Produce the frozen article and regenerate every ledger against that frozen text, in this order.

## Freeze Order (mandatory)

1. Write the final `article.md` from the revised draft. Strip all `<!-- claims: ... -->` traceability
   comments from the published text.
2. Treat that article text as frozen. Do not edit it afterward.
3. Write `research_appendix.md` with methodology, source limits, evidence grading, search scope,
   verification indicators, and detailed tables moved out of the article body.
4. Regenerate `paragraph_map.json` from the frozen article, preserving the claim and evidence ids that
   the draft comments carried, with real line ranges and the article's own headings.
5. Regenerate `claim_ledger.json` so every `article_anchor.heading` is a verbatim heading of the frozen
   article and every `article_anchor.paragraph_id` exists in the paragraph map.
6. Regenerate `source_ledger.csv` preserving source_id, url, local_path, sha256, ingest_status,
   allowed_domain, and blocked_reason.
7. Regenerate `image_ledger.csv` including `visual_question`, `supported_claim_ids`, and
   `why_prose_is_insufficient` for kept rows.
8. Compute the article SHA-256 and report it as `article_sha256`.

## Ledger Rules

- Every `source_id` in the claim ledger must exist in the source ledger.
- A claim whose sources are all blocked, remote-unverified, or stale must not be `supported` with
  `needs_human_review` false. Use `conditionally_supported` with a reason, or set human review true.
- Every claim needs `evidence_ids` that exist in the run's evidence cards.
- Use `conditionally_supported` when the claim holds only under a stated qualification.

## Must Not Do

- Do not write ledgers before the article is frozen.
- Do not leave a ledger anchor pointing at a heading that no longer exists.
- Do not include AI-generated images (`SAFE-03`).

## Output

Return final JSON matching `schemas/article_result.schema.json`, including `article_sha256`,
`analysis_modes`, and the appendix, paragraph map, and final sync report paths.
