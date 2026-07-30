---
name: industry-article
description: Use for Korean industry, technology, supply-chain, regulation, or market-structure analysis articles. Do not use for published earnings or IR review tasks.
---

# Industry Article Skill

## Load The Contracts

Read these files before planning or drafting:

- `AGENTS.md`
- `prompts/PROMPT.md`
- `prompts/FORMAT.md`
- `prompts/SOURCE_POLICY.md`
- `prompts/AUTHOR_PROFILE.md`
- `prompts/core/CORE_POLICY.md`
- `prompts/core/MODE_CONTRACT.md`

## Workflow

1. Classify the task as a general industry article. Route earnings, IR, conference-call, QA-only, and preview work to their dedicated workflows.
2. Preserve the verbatim user request and require an approved `intent_contract.json` before drafting. Never approve a material scope change on the user's behalf.
3. Prefer `scripts/run_article.py --mode industry --phase all` so the runner enforces every phase, role-specific prompt, artifact schema, and attempt envelope.
4. Follow this order without collapsing phases: `prepare -> scope -> research-plan -> research -> evidence -> narrative -> draft -> review -> revise -> finalize -> audit`.
5. Use `primary_source_scout` per approved research lane and `counterevidence_scout` in isolated context. Do not expose the counterevidence scout to the draft or chosen thesis.
6. Draft only from verified evidence cards, the adjudicated thesis, and the narrative plan. Run citation logic, red-team, QA, and compression reviews before revision.
7. When rendered images exist, give them only to `image_slide_auditor`. Treat `reviews/image_audit.json` as the sole image-decision authority; do not create or depend on an image-ledger candidate.
8. Freeze the article before regenerating the paragraph map and ledgers. Treat the code-only `audit` phase as the publication gate. Require final image-ledger decisions and `caption_candidate` to match the audit report and rendered-image ledger; allow a context-derived final `caption` only when numeric, evidence, source, and render checks still pass.

## Runner Boundary

- For runner-managed CLI calls, let the runner create declared output parents. A role may write only its exact declared regular files; symlink, directory, special-file, or undeclared outputs fail closed on every catchable exit path.
- Never treat `outputs/latest` as role-writable. The runner restores and verifies an existing absent/real-directory latest tree after a catchable role failure, write-scope violation, or latest drift.
- Treat an upstream mutation violation as blocking for checkpoint, final export, audit, and latest promotion. Resume from the earliest producer only when valid lineage proves it; otherwise use `--phase all`.
- Do not claim that this boundary rolls back other upstream files or provides external staging, crash recovery, or same-run locking.

## App-Native Fallback

When nested `codex exec` is unavailable:

1. Run `scripts/run_article.py --prepare-only` for the approved run.
2. Follow `work/runs/{run_id}/prepare_manifest.json` and the required role flow in `AGENTS.md`.
3. Use the canonical role names. Treat `source_researcher` and `citation_checker` only as legacy aliases for `primary_source_scout` and `citation_logic_checker`.
4. Serialize every read-only role result to `work/runs/{run_id}/agents/{role}/attempt_NNN.json`; never claim a role ran when it did not.
5. Let only the designated writer/finalizer path write draft or final files, then run `scripts/run_article.py --mode industry --phase audit --run-id {run_id} --allow-unprovenanced-audit`.
6. Treat that flag as a code-only waiver for a run with no marker and no checkpoint files. It records `waived_no_checkpoint`, never backfills provenance, and never updates `outputs/latest/`; partial, corrupt, marked, or mismatched provenance cannot be waived.
7. Do not attribute the CLI runner's exact-output or latest-restoration guarantees to direct App-native role execution. Keep latest untouched and preserve the same single `image_audit.json` authority manually.

## Required Artifacts

- Intent: `work/runs/{run_id}/intake/original_request.md`, `intake/intent_contract.json`
- Research and evidence: `research/research_question_tree.json`, `research/source_candidates.*.jsonl`, `evidence/evidence_cards.jsonl`, `research/contradiction_ledger.json`, `research/evidence_gap_map.json`
- Narrative and review: `research/thesis_decision.json`, `research/narrative_plan.json`, `drafts/draft_article.md`, `reviews/*.json`; `reviews/image_audit.json` is canonical when images exist.
- Final: `outputs/{run_id}/article.md`, `research_appendix.md`, `paragraph_map.json`, the three ledgers, `article_result.json`, and `final_sync_report.json`

## Hard Rules

- Do not write fixed template sections just because a prior article used them.
- Do not let recent news become the whole frame; use it as a trigger or evidence.
- Do not include financial or valuation claims prohibited by `prompts/SOURCE_POLICY.md`.
- Do not invent sources, numbers, or author experiences.
- Do not use a line-count or image-count quota.
- Do not treat source discovery, a model's support label, or a successful finalize call as a substitute for the final code audit.
