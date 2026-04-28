---
name: industry-article
description: Use for Korean industry, technology, supply-chain, regulation, or market-structure analysis articles. Do not use for published earnings or IR review tasks.
---

# Industry Article Skill

## References

Read these files before planning or drafting:

- `prompts/PROMPT.md`
- `prompts/FORMAT.md`
- `prompts/SOURCE_POLICY.md`
- `prompts/AUTHOR_PROFILE.md`

## Workflow

1. Classify the task as a general industry article. If it is earnings, IR, conference-call, or preview work, stop and route to the proper workflow.
2. Spawn `source_researcher` to prepare source requirements, recent triggers, and source ledger candidates.
3. Spawn `narrative_architect` to prepare one-sentence insight candidates, central flow, branches, and transition logic.
4. Spawn `citation_checker` once claims or a draft exist.
5. Use `article_writer` only after research and narrative artifacts exist.
6. Spawn `qa_reviewer` before final delivery.
7. Parent/main agent integrates the final article and final structured response.

## Required Artifacts

- `work/research/` for research notes when files are requested.
- `work/ledgers/` for source or claim ledgers when files are requested.
- `work/drafts/` for draft candidates when files are requested.
- `outputs/article.md` and `outputs/article_result.json` only for final outputs.

## Hard Rules

- Do not write fixed template sections just because a prior article used them.
- Do not let recent news become the whole frame; use it as a trigger or evidence.
- Do not include financial or valuation claims prohibited by `prompts/SOURCE_POLICY.md`.
- Do not invent sources, numbers, or author experiences.
