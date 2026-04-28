---
name: reportmachine-usage
description: Use for ReportMachine repo onboarding, purpose, usage, Codex App vs Codex CLI execution, runner options, input/output paths, source CSV guidance, smoke-test, prepare-only, troubleshooting, and "how do I use this repo?" questions. Do not use for drafting or reviewing article content.
---

# ReportMachine Usage Skill

## Purpose

Answer initial user questions about how to understand, configure, and run this repository without loading the full article-writing prompt stack.

Use this skill for questions such as:

- "이 Repo 목적과 사용 방법 설명해줘"
- "Codex App에서 어떻게 실행하나요?"
- "Codex CLI로 직접 실행하려면?"
- "`inputs/topic.md`와 `inputs/sources.csv`는 어떻게 쓰나요?"
- "`--smoke-test`, `--prepare-only`, `--allow-shell-network` 차이는?"
- "산출물은 어디에 생기나요?"
- "`--summary-output`은 무엇을 만드나요?"

Do not use this skill when the user asks to create, draft, or QA an article. Route those tasks to `$article-intake`, `$industry-article`, `$earnings-article`, or `$article-qa`.

## References

Read only the minimum needed:

- Start with `docs/USAGE.ko.md` for user-facing usage explanations.
- Read `AGENTS.md` only when routing, skill choice, or artifact ownership matters.
- Read `README.md` only when confirming runner commands, project trust, TOML presets, or release/archive details.
- Read `inputs/topic.md` and `inputs/sources.csv` only when explaining input templates.
- Read `scripts/run_article.py --help` or `src/reportmachine_runner/cli.py` only when the exact CLI option behavior is unclear.

## Response Rules

- Answer in Korean unless the user asks otherwise.
- Keep paths repo-relative in explanations.
- Distinguish Codex App usage from Codex CLI runner usage.
- Make clear that web search is candidate discovery only; final claims must trace to repo-local sources and ledgers.
- Mention that OpenAI API keys, File Search, and vector-store uploads are not part of the runner.
- Mention that summary images use Codex internal image generation tools only and remain separate from article outputs.
- If the user wants a runnable article workflow from a short topic, route to `$article-intake` rather than expanding usage instructions.
