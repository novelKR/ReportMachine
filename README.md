# ReportMachine

ReportMachine is a Codex-oriented workspace for Korean industry and earnings article workflows.

## Usage Guide

For first-time setup, Codex App vs Codex CLI execution, input files, runner options, output paths, and troubleshooting, start with `docs/USAGE.ko.md`.

When asking Codex usage or onboarding questions inside this repo, use `$reportmachine-usage`. For article drafting or review, continue to use `$article-intake`, `$industry-article`, `$earnings-article`, or `$article-qa` as appropriate.

## Codex Project Trust

Open this project as a trusted Codex project before running article automation. If the project is untrusted, project-scoped `.codex/` configuration, custom agents, hooks, and rules are not loaded.

Manual check:

```bash
codex exec -C . --skip-git-repo-check "Confirm loaded project config and custom agents."
```

## Runner

Use the OS-neutral runner instead of invoking `$earnings-article` or `$industry-article` directly from a shell:

```bash
python3 scripts/run_article.py --mode industry --smoke-test
python3 scripts/run_article.py --mode earnings --smoke-test
python3 scripts/run_article.py --config reportmachine.example.toml --profile default --smoke-test
```

### Localhost Web MVP

The optional Web control plane is a local convenience layer over the same article runner:

```bash
python3 -m pip install -e ".[web]"
python3 scripts/run_web.py
```

It binds exactly to `127.0.0.1` (default `http://127.0.0.1:8765/`) and prints a one-time bootstrap capability. Enter that capability in the page to create an in-memory, `HttpOnly`, `SameSite=Strict` session; every mutation also requires the exact Host, same Origin or Referer, and a session CSRF token. CORS is not enabled. `--port` may change the port, but no non-loopback host can be selected.

The browser has a closed command contract; each executable action requires an explicit mode and validated run id. `smoke`, `prepare`, and `run_all` may accept topic text and map to `--smoke-test`, `--prepare-only`, and `--phase all`. `approve_scope` performs a compare-and-swap approval of an existing validated intent contract using its current material-scope SHA-256; a stale hash, wrong run or mode, invalid contract, or `conflicting` scope changes nothing. `allowed_phase` accepts only `scope`, `research-plan`, `research`, `evidence`, `narrative`, `draft`, `review`, `revise`, or `finalize`; `audit` is a separate code-only action. The receipt and UI preserve the server-derived requested phase. An immediate duplicate reuses the current receipt. The same `receipt.json` also retains a bounded index of the 32 most recent accepted requests, so a historical duplicate is rejected without spawning; an intentional historical rerun requires a fresh idempotency key and explicit rerun confirmation. Status, redacted supervisor-log polling, cancellation, explicit recovery acknowledgement, and fixed logical artifact downloads are also supported. Arbitrary argv, paths, profiles, network flags, environment values, uploads, or provenance waivers are not browser inputs; use the CLI when the closed Web contract does not cover an operation.

The supported mutating runner entrypoints and Web-spawned runs share one non-blocking OS writer lock keyed by the canonical checkout, not by `run_id`; standalone summary and publish runs use it too. An omitted CLI `--run-id` is resolved once before the owner record is created, so the fence and workflow always name the same run. Smoke tests and dry runs do not take the writer lock. Web readers never take that non-blocking writer lock: they reject active receipts/owners and require unchanged owner, receipt, and duplicate artifact observations before returning a stable snapshot. A POSIX pipe start barrier keeps a Web child outside the runner until its PID and birth identity are durably recorded; a supervisor crash or receipt-write failure closes the pipe and denies runner entry. Exit `75` means another mutation is busy; exit `76` means a prior owner fence requires explicit recovery. Recovery never retries the workflow:

```bash
python3 scripts/run_article.py --acknowledge-execution-recovery <lease_id>
```

That command is standalone except for optional `--project-root`, compares the exact current lease, and only removes the core owner fence after acquiring the OS lock. A Web receipt in `recovery_required` or `interrupted_unknown` still requires the Web recovery acknowledgement after the recorded process is no longer live and a stable artifact/provenance inspection completes. Cancellation moves `running` to `cancelling`; it becomes `cancelled` only with clean exit `143` evidence and no owner fence bound to that Web execution. A cancelling child that instead exits naturally with code `0`, is confirmed dead, and has no own or unreadable owner fence remains `exited`/`0`; a valid foreign owner is reported only as an independent writer. Other ambiguous outcomes remain `interrupted_unknown` or `recovery_required`. Neither recovery path auto-retries.

Execution state is outside the repository at the fixed per-OS-user path `~/.reportmachine/state/checkouts/{checkout_sha256}/`: `writer.lock`, `owner.json`, `web/receipt.json`, and `web/web.log`. On POSIX, `checkout_sha256` hashes the checkout directory's filesystem device/inode identity so supported path and case aliases share one lock; the platform-normalized canonical path is the fallback where that identity is unavailable. Production entrypoints do not accept an environment override for this root, so different launch environments cannot split the common lock. The Web launcher starts from an empty environment and receives only a controlled `PATH`, a server-generated non-secret `REPORTMACHINE_WEB_EXECUTION_ID` correlation value, its inherited start-barrier descriptor, and an allowlist of home, temporary-directory, locale, and certificate variables. The launcher consumes and removes the barrier descriptor before replacing itself with the runner; ambient proxy and credential variables are excluded. The Web log strips terminal controls and redacts common credential patterns, but this is a practical boundary rather than a guarantee that every future secret format can be detected.

Process state and article truth are deliberately separate. Exit `0` only means the process exited successfully. Article validity also requires the runner's current full finalize-checkpoint validation, including every protected artifact and ledger row, plus an exact comparison between the final-sync report's complete artifact-hash set and the current frozen outputs; JSON schemas, preflight, audit/run-summary evidence, and article hashes must also pass. Downloads accept only a validated run id and the logical names `article`, `article_result`, `source_ledger`, `image_ledger`, `claim_ledger`, `preflight`, `run_manifest`, `final_sync`, `paragraph_map`, `research_appendix`, `publish_pdf`, `publish_result`, `summary_image`, and `summary_result`; arbitrary paths, `latest`, symlinks, and non-regular files are rejected.

This MVP is intentionally not a LAN service, multi-user system, database or durable queue, SPA, SSE/WebSocket service, upload surface, or remote executor. It uses server-rendered HTML with polling on one local machine. The core OS-lock implementation includes a Windows `msvcrt` branch, but the Web runner start barrier and model-role execution are POSIX-only; the current verification did not execute the Windows lock branch.

Default outputs are written under `outputs/{run_id}/`. `outputs/latest/` is refreshed only after a successful audit with verified finalize provenance; a provenance waiver never refreshes it.

The article runner is phase-gated and drives one agent role per phase. The default `--phase all` runs:

```text
prepare -> scope -> research-plan -> research -> evidence -> narrative
       -> draft -> review -> revise -> finalize -> audit
```

Each phase requires its upstream artifacts to exist and validate against `schemas/`, and each role invocation records an envelope under `work/runs/{run_id}/agents/`. Drafting stays blocked until the run's intent contract records scope approval.

Each completed model phase records a provenance checkpoint under `work/runs/{run_id}/provenance/` once its outputs and semantics validate. The code-only `audit` phase does not create one. Resuming a single model phase re-checks the previous phase's checkpoint before any model call, so an artifact edited after its producing role ran stops the run instead of feeding a stale downstream phase. One phase contract defines what is protected: the cumulative outputs plus the inputs a phase's roles actually read, including the normalized topic and source inventory. The finalize checkpoint also protects the schema-validated `article_result.json`, so later changes to its title, warnings, checklist state, or output metadata break provenance.

The runner ledgers are tracked per row, covering every column except the `fetched_at` timestamp. Editing or removing a recorded row fails. A row the checkpoint never recorded is accepted only when an ingest receipt under `work/runs/{run_id}/provenance/ingest/` proves the runner produced it. `prepare` replaces its receipt with the rows its ingest rewrote; `research` and `evidence` carry forward only still-valid identical rows and add only the before/after delta the current ingest actually wrote. Pre-existing unreceipted, tampered, or unrelated rows are never absorbed. Re-running a phase invalidates its checkpoint and downstream checkpoints, but preserves receipts; a preserved receipt counts as evidence only while its recorded input hashes and row digests still match.

The same input contract backs both the checkpoint and each role's attempt envelope, so a role that reads the intent contract, the extracted source text, the rendered image ledger, the audited image decisions, or the source inventory records it as an input.

The provenance records and the run manifest are frozen while any role runs: changing, creating, or deleting one fails the phase, so a role cannot forge the evidence that judges it. Before a CLI role starts, the runner creates the parent directories for its declared outputs. The role may then write only those exact paths, and every declared output must be absent or a regular file before the call and a regular file afterward. A symlink, directory, or special-file output is rejected; broad run, output, log, and latest directory prefixes never grant role write access. The runner performs the write-scope check and closes the attempt record on every catchable outcome, including success, nonzero exit, timeout, exception, keyboard interrupt, and cancellation.

Pipeline roles run only on POSIX hosts. Each role gets a dedicated session and process group, and the runner terminates and verifies the whole group before inspecting or restoring repository state; a non-POSIX role request fails preflight with status 2 and a failed attempt record. Event and stderr logs remain runner-owned append-only files: their pre-call bytes and file identity must survive, while ordinary stdout/stderr appends are allowed. Non-role summary and publish commands retain their existing execution path.

`outputs/latest` is never role-writable. Before a role call its root must be absent or a real directory; a root symlink, regular file, or special file is a pre-call hard failure. For a real directory, the runner snapshots its entry types, regular-file bytes, and symlink target text without following symlinks. A catchable role failure, write-scope violation, or latest drift triggers restore and re-verification in the cleanup path. Restore failure exits with status 2 and prevents the phase checkpoint, final export, successful audit, and latest promotion.

The phase guard also protects cumulative tracked artifacts, the canonical source and rendered-image ledgers, and the extracted text or rendered images derived from those ledgers. Only the runner's candidate-merge and source-ingest operations receive narrowly scoped mutation allowances. An upstream violation fails the phase before a checkpoint or later success gate can be recorded. Ordinary phase-owned artifacts retain precise earliest-producer guidance. A canonical ledger or ledger-derived file mutation always requires `--phase all`, because receipt row lineage does not prove a safe partial-recovery point; missing, invalid, or ambiguous lineage is handled the same way.

These role-level checks assume no uncontrolled reader of in-flight artifacts and a catchable process exit. Supported top-level mutations now share the checkout-scoped OS lock described above; Web inspection does not acquire it and instead fails closed on active or changing owner/receipt/artifact observations. The role boundary itself still does not provide full rollback for upstream edits, execution outside a real-repository staging area, multi-file commit journaling, `kill -9` or host-crash recovery, or hostile-role isolation for writes outside the repository, network activity, and secrets. `outputs/latest` is the only tree restored by this boundary.

Checkpoints are not backfilled: a run created before them must be resumed with `--phase all`, or from the earliest model phase you need. Once a run records its first checkpoint, the run manifest keeps a `provenance_marker` that outlives the provenance directory. By default, `--phase audit` requires a matching finalize checkpoint; a run with neither marker nor checkpoint files fails with exit code 2 after the runner still writes a fresh `final_sync_report.json`, and it cannot refresh `outputs/latest/`.

For an intentional code-only audit of a historical or App-native run that never had provenance, add the CLI-only `--allow-unprovenanced-audit` flag. If every other audit check passes, the run records `provenance_status: waived_no_checkpoint` and overall status `audit_passed_with_provenance_waiver`, but it still cannot refresh `outputs/latest/`. The waiver applies only when both the marker and every checkpoint file are absent. A marker or even one checkpoint file makes partial, corrupt, non-object, missing-finalize, or mismatched provenance non-waivable and the audit fails. The flag never backfills a checkpoint.

Research-discovered sources go through a second runner-owned ingest before evidence verification. The runner assigns source ids and statuses, downloads or copies allowed material into the run, hashes it, and writes visible HTML or extracted PDF text under `work/runs/{run_id}/research/extracted_text/`. Blocked or remote-unverified candidates cannot silently become verified evidence. Separately invoked later phases reuse this enriched ingest snapshot; rerun `prepare` when the base source inventory itself must be refreshed.

`work/runs/{run_id}/reviews/image_audit.json` is the sole canonical image-decision artifact; an image-ledger candidate is not part of the contract. Only `image_slide_auditor` receives rendered image inputs. Revision and finalization consume the audit report as a file input, and the final image ledger is cross-checked against both that report and the canonical rendered-image ledger. Its shared decision fields and `caption_candidate` must match the report. A final `caption` may be adapted to the frozen article context, but it must preserve the canonical candidate and pass the existing numeric, evidence, source-metadata, and render-metadata checks.

You can stop after the local gate, run a single phase, or audit an existing run without any model call:

```bash
python3 scripts/run_article.py --mode earnings --phase prepare
python3 scripts/run_article.py --mode earnings --phase draft --run-id <run_id>
python3 scripts/run_article.py --mode earnings --phase finalize --run-id <run_id>
python3 scripts/run_article.py --mode earnings --phase audit --run-id <run_id>
```

For a historical run that genuinely has no marker or checkpoint files, request the code-only waiver explicitly:

```bash
python3 scripts/run_article.py --mode earnings --phase audit --run-id <run_id> --allow-unprovenanced-audit
```

Unattended runs need explicit scope approval and, optionally, analysis modes and depth:

```bash
python3 scripts/run_article.py --mode industry --run-id <run_id> \
  --intake-original-request "verbatim user request" \
  --intake-analysis-mode market_reaction \
  --intake-analysis-mode company_fundamental \
  --intake-depth standard \
  --intake-approve-scope
```

`prepare` runs intake, source ingest, strict preflight, and manifest creation without drafting. Earnings preflight requires usable current and previous release/deck materials before Codex is called. The runner writes `outputs/{run_id}/preflight_result.json`, `outputs/{run_id}/run_manifest.json`, and later `outputs/{run_id}/claim_ledger.json`. Earnings runs also require `outputs/{run_id}/earnings_delta_matrix.json` and `.md` before final success.

If an existing intent contract is malformed or belongs to another run, execution fails before ingest. Use `--intake-reset-intent-contract` only to back up those intent artifacts and create a pending contract for the current run; scope approval remains separate.

Earnings runs default to live Codex web search. Industry runs default to cached Codex web search.
Override when needed:

```bash
python3 scripts/run_article.py --mode earnings --web-search cached --smoke-test
python3 scripts/run_article.py --mode industry --web-search disabled --smoke-test
```

Shell network access remains disabled by default. Enable it only for runs that need shell-based downloads:

```bash
python3 scripts/run_article.py --mode earnings --allow-shell-network
```

To capture newline-delimited Codex events for troubleshooting or regression runs:

```bash
python3 scripts/run_article.py --mode earnings --json-events
```

This writes `logs/{run_id}/codex_events.jsonl`, `logs/{run_id}/stderr.log`, and `logs/{run_id}/run_summary.json`.

Derivative A4 summary output is opt-in and runs only after article outputs are complete:

```bash
python3 scripts/run_article.py --mode industry --summary-output
python3 scripts/run_summary.py --run-id ai_power_grid_20260428T2027KST
```

Summary artifacts are written under `outputs/{run_id}/summary/` and are not article outputs, evidence sources, or replacements for `source_ledger.csv` or `image_ledger.csv`. The A4 image request uses Codex internal image generation tools only; the runner does not call OpenAI API endpoints, File Search, vector stores, or external image APIs.

## A4 PDF Publishing

Reader-friendly A4 PDF publishing is opt-in and runs only after article outputs pass finalize validation:

```bash
python3 scripts/run_article.py --mode industry --publish-pdf
python3 scripts/run_publish.py --run-id ai_power_grid_20260428T2027KST
```

Publish artifacts are written under `outputs/{run_id}/publish/`:

```text
outputs/{run_id}/publish/article.pdf
outputs/{run_id}/publish/publish_figure_map.csv
outputs/{run_id}/publish/publish_manifest.json
outputs/{run_id}/publish/publish_result.json
```

The renderer is a deterministic local renderer. It performs no Codex calls, no web search, and no external API requests, so the same inputs always produce the same PDF. Images stay at their article reference positions and are numbered as `그림 N`; captions come from `image_ledger.csv` so the PDF introduces no facts absent from the article and ledgers. Inline citation links stay clickable and are also printed as numbered link footnotes for paper readers.

Publishing requires the optional renderer dependency and a Hangul TrueType font:

```bash
python3 -m pip install -e ".[publish]"
```

A bold Hangul face is optional; when none is available the renderer maps the bold slot onto the regular face and separates heading levels by size and colour. Set an explicit font when auto-discovery fails:

```toml
[profiles.default.publish]
enabled = true
font_regular = "/usr/share/fonts/truetype/nanum/NanumGothic.ttf"
```

PDF publishing never modifies `article.md`, the ledgers, or `article_result.json`. Publish status is reported only in `publish_result.json`.

## Conversational Intake

For short chat-style requests, use `$article-intake` first. The intake skill asks the missing questions, produces an approval plan, and after approval creates run-local inputs under `work/runs/{run_id}/intake/`.

Example approved intake execution:

```bash
python3 scripts/run_article.py --mode industry --run-id ai_power_grid_20260428 \
  --intake-topic "AI 데이터센터 전력 인프라 병목과 전력기기 공급망" \
  --intake-core-question "AI 서버 수요가 늘어날 때 병목이 왜 전력망 장비로 이동하는가?" \
  --intake-recent-trigger "최근 데이터센터 전력 수요 전망과 전력기기 공급망 리드타임 변화" \
  --intake-source-note "IEA, DOE, utility planning, and official company technical or IR materials" \
  --intake-output-notes "full report"
```

By default, generated intake files are run-local and do not overwrite `inputs/topic.md` or `inputs/sources.csv`. Add `--intake-mirror-inputs` only when the user explicitly wants the approved brief mirrored into the tracked input templates.

For Codex App-only environments where the `codex` CLI is unavailable, prepare the same run without nested CLI execution:

```bash
python3 scripts/run_article.py --prepare-only --mode industry --run-id ai_power_grid_20260428 \
  --intake-topic "AI 데이터센터 전력 인프라 병목과 전력기기 공급망"
```

`--prepare-only` is the compatibility alias for `--phase prepare` plus App-native fallback prompt generation. It creates directories, source-ingest artifacts, preflight output, run manifest, and `work/runs/{run_id}/prepare_manifest.json` without running `codex exec`. The current Codex App session can then use that manifest and the repo-local prompt contracts as the App-native fallback. Nested CLI execution still requires `codex` to be installed, authenticated, available on `PATH`, and trusted for this project.

After the App-native session writes and validates the final artifacts, run its explicit code-only audit waiver:

```bash
python3 scripts/run_article.py --mode industry --phase audit --run-id ai_power_grid_20260428 --allow-unprovenanced-audit
```

This does not reconstruct model-phase provenance and never promotes the run to `outputs/latest/`. If a marker or any checkpoint file exists, the waiver is rejected instead of hiding partial or corrupt provenance.

Before Codex runs, the runner preprocesses `inputs/sources.csv` into repo-local source artifacts under `work/runs/{run_id}/`. Local files are copied into `research/sources/`; allowed HTTPS sources are downloaded only when shell network access is explicitly enabled. Downloaded or registered sources are hashed, PDF text/index files are written under `research/pdf_text/` and `research/pdf_index.jsonl`, and rendered PDF page images are inventoried under `images/rendered/` when Poppler tools such as `pdftotext` and `pdftoppm` are available.

Final image ledgers must include caption audit fields: `actual_slide_title`, `detected_key_datapoints`, `article_anchor_before`, `article_anchor_after`, `keep_or_drop`, and `drop_reason`. Final claim ledgers must connect core claims to source IDs and datapoints; unsupported or human-review claims block finalization.

Automatic downloads are limited to official or explicitly allowed domains. Add project-specific IR domains in `reportmachine.local.toml`:

```toml
[profiles.earnings.source_ingest]
allowed_domains = ["investor.example.com"]
```

The default remains repo-local only. OpenAI API key is not required for the runner, and OpenAI File Search/vector-store upload is not used by the runner. Codex usage is limited to the CLI subscription-quota surface: `codex exec`, configured `web_search`, and rendered local images passed with `--image` only to `image_slide_auditor`.

Summary image style is configured inline in TOML under `[profiles.<name>.summary]`, for example `style_name`, `style_prompt`, `negative_prompt`, `paper_size`, `orientation`, and `max_visual_claims`. Separate Markdown style files are not used.

## TOML Presets And External Artifacts

Use `reportmachine.example.toml` as the tracked preset template. Copy it to `reportmachine.local.toml` for machine-specific paths:

```bash
python3 scripts/run_article.py --config reportmachine.local.toml --profile default
```

Profile values provide defaults for mode, input paths, schema, web search, shell network access, and run-id prefix. CLI flags override TOML values.

External artifact roots are runner-only. Codex agents continue to write only repo-local run paths such as `work/runs/{run_id}/` and `outputs/{run_id}/`. When `profiles.<name>.artifacts.external_root` is set, the runner can import previous artifacts from that external root, periodically snapshot the repo-local run cache, export final files to `runs/{run_id}/` and `latest/`, and optionally remove the successful run cache.

## Release Archive

Use the source packaging helper when sharing ReportMachine without a Git remote:

```bash
python3 scripts/package_source.py
```

The helper packages Git HEAD into `dist/source/ReportMachine-source-{short_sha}.zip`, extracts the zip into a temporary directory, and runs `python3 scripts/verify_layout.py --release` against the extracted source before publishing the final archive. It fails by default when the working tree has uncommitted changes because those changes are not included in a Git HEAD archive. Pass `--allow-dirty` only when that exclusion is intentional.

Manual `git archive` remains available for low-level use, but the helper is preferred because it validates that ignored local files such as `build/`, `dist/`, `.tmp/`, caches, PyInstaller `*.spec` files, local TOML presets, and generated outputs are not packaged.

For internal tools that only allow image transfer and preserve original JPG bytes, an explicit experimental image-binder output is available:

```bash
python3 scripts/package_source.py --experimental-image-binder --acknowledge-image-only-internal-distribution
```

This writes `dist/source/ReportMachine-source-{short_sha}-image-binder.jpg`. The JPG cover visibly states that it contains a ZIP file and uses a file-binder based experimental internal distribution format. When Korean text rendering is available, the cover includes Korean and English notices; otherwise it renders the English notice only. This option is not a replacement for the standard ZIP archive, and it will fail after transfer if the image tool resizes, optimizes, strips trailing bytes, or re-encodes the JPG.
