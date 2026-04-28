---
name: summary-output
description: Use after a completed ReportMachine article run to generate derivative A4 summary outputs under outputs/{run_id}/summary using Codex internal image generation tools only.
---

# Summary Output Skill

## References

Read these files before generating summary outputs:

- `AGENTS.md`
- `prompts/PROMPT.md`
- `prompts/SOURCE_POLICY.md`
- `outputs/{run_id}/article.md`
- `outputs/{run_id}/article_result.json`
- `outputs/{run_id}/source_ledger.csv`
- `outputs/{run_id}/image_ledger.csv`
- `outputs/{run_id}/summary/summary_manifest.json`
- `outputs/{run_id}/summary/summary_prompt.md`

## Workflow

1. Confirm the source article run is complete.
2. If `article_result.json` has `checklist_status = "fail"`, do not generate an image. Return a schema-valid failed `summary_result.json`.
3. Use only Codex internal image generation tools for the A4 image request.
4. Write summary artifacts only under `outputs/{run_id}/summary/`.
5. Write the A4 image to `outputs/{run_id}/summary/summary_a4.png`.
6. Write `summary_claim_map.csv` rows for every visible statement, number, or source label in the image.
7. Return final JSON matching `schemas/summary_result.schema.json`.

## Hard Rules

- Summary outputs are derivative artifacts, not article outputs.
- AI-generated images remain prohibited in article outputs.
- AI-generated A4 images are allowed only inside `outputs/{run_id}/summary/`.
- Do not use OpenAI API endpoints, File Search, vector stores, external image APIs, shell downloads, or browser automation as fallback.
- Do not introduce new factual claims, quantitative values, source attributions, charts, or captions absent from `article.md` and ledgers.
- Summary outputs are not evidence sources and do not replace `source_ledger.csv` or `image_ledger.csv`.
