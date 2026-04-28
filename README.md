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
