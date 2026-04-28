# ReportMachine

ReportMachine is a Codex-oriented workspace for Korean industry and earnings article workflows.

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

## Release Archive

Before creating a release archive, verify that generated local artifacts are not present:

```bash
python3 scripts/verify_layout.py --release
git archive --format=zip --output ReportMachine-clean.zip HEAD
```

Use `git archive` for release zips so ignored local files such as `build/`, `dist/`, `.tmp/`, caches, and generated outputs are not packaged.
