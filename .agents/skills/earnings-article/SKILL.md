---
name: earnings-article
description: Use for Korean articles based on published company earnings releases, investor presentations, IR decks, or conference-call transcripts.
---

# Earnings Article Skill

## Load The Contracts

Read these files before planning or drafting:

- `AGENTS.md`
- `prompts/PROMPT.md`
- `prompts/FORMAT.md`
- `prompts/EARNINGS_PROMPT.md`
- `prompts/SOURCE_POLICY.md`
- `prompts/AUTHOR_PROFILE.md`
- `prompts/core/CORE_POLICY.md`
- `prompts/core/MODE_CONTRACT.md`

If `prompts/EARNINGS_PROMPT.md` conflicts with a general rule, the earnings-specific prompt wins for earnings analysis. Investment recommendation and valuation prohibitions still apply.

## Workflow

1. Confirm the work is based on already published earnings, IR, or conference-call material.
2. Preserve the verbatim request and require user-approved scope. Never let an earnings-quality frame silently remove another requested subject or comparison target.
3. Require usable current-quarter and prior-quarter release/deck materials at preflight. Treat missing required material as a blocking research gate.
4. Prefer `scripts/run_article.py --mode earnings --phase all` and follow every phase in `AGENTS.md` without collapsing calls.
5. Run `earnings_delta_analyst` during evidence integration after primary and counterevidence discovery. Compare periods side by side and retain the delta artifacts through finalization.
6. Use `image_slide_auditor` only when rendered images exist; it is the only role that receives rendered image inputs. Inspect actual pages, allow zero retained images, use `reviews/image_audit.json` as the sole image-decision authority, and never create or depend on an image-ledger candidate.
7. Draft only from verified evidence cards, the earnings delta, the adjudicated thesis, and the narrative plan. Run citation logic, red-team, QA, and compression reviews before revision.
8. Freeze the article before regenerating paragraph map and ledgers, then require the code-only audit to pass. Final image-ledger decisions and `caption_candidate` must match the audit report and rendered-image ledger; a context-derived final `caption` may differ only while numeric, evidence, source, and render checks pass.

## Runner Boundary

- For runner-managed CLI calls, let the runner create declared output parents. A role may write only its exact declared regular files; symlink, directory, special-file, or undeclared outputs fail closed on every catchable exit path.
- Never treat `outputs/latest` as role-writable. The runner restores and verifies an existing absent/real-directory latest tree after a catchable role failure, write-scope violation, or latest drift.
- Treat an upstream mutation violation as blocking for checkpoint, final export, audit, and latest promotion. Resume from the earliest producer only when valid lineage proves it; otherwise use `--phase all`.
- Do not claim that this boundary rolls back other upstream files or provides external staging, crash recovery, or same-run locking.

## App-Native Fallback

When nested `codex exec` is unavailable:

1. Run `scripts/run_article.py --prepare-only` for the approved earnings run.
2. Follow `work/runs/{run_id}/prepare_manifest.json` and the exact required role flow in `AGENTS.md`.
3. Use `primary_source_scout` and `citation_logic_checker`; the old `source_researcher` and `citation_checker` names are compatibility aliases only.
4. Serialize read-only role results to `work/runs/{run_id}/agents/{role}/attempt_NNN.json` and keep counterevidence isolated from the draft and selected thesis.
5. After the parent writes validated final artifacts, run `scripts/run_article.py --mode earnings --phase audit --run-id {run_id} --allow-unprovenanced-audit`.
6. Treat that flag as a code-only waiver for a run with no marker and no checkpoint files. It records `waived_no_checkpoint`, never backfills provenance, and never updates `outputs/latest/`; partial, corrupt, marked, or mismatched provenance cannot be waived.
7. Do not attribute the CLI runner's exact-output or latest-restoration guarantees to direct App-native role execution. Keep latest untouched and preserve the same single `image_audit.json` authority manually.

## Required Intermediate Artifacts

- Current-quarter materials list.
- Prior-quarter materials list.
- Delta table.
- At least two disappeared-highlight exploration results, or an explicit fewer-than-two count with wording/order/unit/risk-language fallback analysis.
- IR frame and counter-reading.
- Five-axis scorecard draft and rationale.
- Canonical rendered-image ledger and `reviews/image_audit.json` when applicable; no image-ledger candidate.
- Research question tree, source candidates, evidence cards, contradiction ledger, evidence gap map, thesis decision, and narrative plan.
- Citation logic, red-team, QA, compression, revision notes, and image audit when applicable.
- File artifacts must stay under `work/runs/{run_id}/...` until final outputs are written under `outputs/{run_id}/...`.
- Final outputs must include the frozen article, research appendix, paragraph map, regenerated ledgers, structured result, and final sync report.

## Hard Rules

- Do not conclude from current-quarter materials alone.
- Missing required current-quarter or prior-quarter release/deck materials are a fail/research gate, not a confidence-downgraded article path.
- Do not invent disappeared highlights; if fewer than two are found, state the count and analyze wording, ordering, unit, and risk-language changes.
- Do not infer slide contents from slide number alone.
- Do not convert earnings analysis into valuation advice.
- Do not infer support from an issuer label or a ledger status without the evidence and final code audit.
