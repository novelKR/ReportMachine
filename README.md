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

Default outputs are written under `outputs/{run_id}/`. After a successful validated run, `outputs/latest/` is refreshed from that run.

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

`--prepare-only` creates directories, source-ingest artifacts, and `work/runs/{run_id}/prepare_manifest.json` without running `codex exec`. The current Codex App session can then use that manifest and the repo-local prompt contracts as the App-native fallback. Nested CLI execution still requires `codex` to be installed, authenticated, available on `PATH`, and trusted for this project.

Before Codex runs, the runner preprocesses `inputs/sources.csv` into repo-local source artifacts under `work/runs/{run_id}/`. Local files are copied into `research/sources/`; allowed HTTPS sources are downloaded only when shell network access is explicitly enabled. Downloaded or registered sources are hashed, PDF text/index files are written under `research/pdf_text/` and `research/pdf_index.jsonl`, and rendered PDF page images are inventoried under `images/rendered/` when Poppler tools such as `pdftotext` and `pdftoppm` are available.

Automatic downloads are limited to official or explicitly allowed domains. Add project-specific IR domains in `reportmachine.local.toml`:

```toml
[profiles.earnings.source_ingest]
allowed_domains = ["investor.example.com"]
```

The default remains repo-local only. OpenAI API key is not required for the runner, and OpenAI File Search/vector-store upload is not used by the runner. Codex usage is limited to the CLI subscription-quota surface: `codex exec`, configured `web_search`, and rendered local images passed with `--image`.

## TOML Presets And External Artifacts

Use `reportmachine.example.toml` as the tracked preset template. Copy it to `reportmachine.local.toml` for machine-specific paths:

```bash
python3 scripts/run_article.py --config reportmachine.local.toml --profile default
```

Profile values provide defaults for mode, input paths, schema, web search, shell network access, and run-id prefix. CLI flags override TOML values.

External artifact roots are runner-only. Codex agents continue to write only repo-local run paths such as `work/runs/{run_id}/` and `outputs/{run_id}/`. When `profiles.<name>.artifacts.external_root` is set, the runner can import previous artifacts from that external root, periodically snapshot the repo-local run cache, export final files to `runs/{run_id}/` and `latest/`, and optionally remove the successful run cache.

## Release Archive

Before creating a release archive, verify that generated local artifacts are not present:

```bash
python3 scripts/verify_layout.py --release
git archive --format=zip --output ReportMachine-clean.zip HEAD
```

Use `git archive` for release zips so ignored local files such as `build/`, `dist/`, `.tmp/`, caches, and generated outputs are not packaged.
