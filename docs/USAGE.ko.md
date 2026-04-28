# ReportMachine 사용 안내

이 문서는 ReportMachine을 처음 내려받은 사용자가 Codex App 또는 Codex CLI에서 어떻게 시작해야 하는지 설명합니다. 기사 작성 품질 규칙의 원문은 `prompts/`와 `AGENTS.md`가 기준이고, 이 문서는 실행 경로와 입력/출력 사용법을 빠르게 파악하기 위한 안내서입니다.

## 1. 이 레포의 목적

ReportMachine은 한국어 산업 분석 글과 실적/IR 분석 글을 Codex로 생성하기 위한 작업공간입니다. 핵심은 세 가지입니다.

1. 기사 품질 계약을 `prompts/`에 고정합니다.
2. 입력, 소스, 중간 산출물, 최종 산출물 경로를 repo-local 구조로 제한합니다.
3. `scripts/run_article.py`가 소스 전처리, `codex exec` 실행, 산출물 검증을 한 번에 묶습니다.

일반 산업, 기술, 공급망, 규제, 시장구조 분석은 `$industry-article` 경로를 씁니다. 이미 발표된 실적 발표, IR deck, conference call, filings 기반 분석은 `$earnings-article` 경로를 씁니다. 짧은 자연어 주제에서 시작할 때는 `$article-intake`로 질문 정리와 승인 계획을 먼저 만듭니다. 작성된 원고만 점검할 때는 `$article-qa`를 씁니다.

## 2. 먼저 확인할 것

Codex 프로젝트로 이 폴더를 열 때는 trusted project 상태여야 합니다. 프로젝트가 신뢰되지 않으면 `.codex/` 설정, custom agents, project-local skills가 로드되지 않을 수 있습니다.

수동 확인:

```bash
codex exec -C . --skip-git-repo-check "Confirm loaded project config and custom agents."
```

레이아웃 확인:

```bash
python3 scripts/verify_layout.py
```

Git 없이 zip으로 다른 사람에게 소스만 전달하려면 패키징 명령을 사용합니다.

```bash
python3 scripts/package_source.py
```

이 명령은 Git HEAD 기준으로 `dist/source/ReportMachine-source-{short_sha}.zip`을 만들고, 임시 위치에 압축을 풀어 `python3 scripts/verify_layout.py --release`를 통과한 경우에만 최종 zip을 남깁니다. 미커밋 변경은 zip에 들어가지 않으므로 기본적으로 실패하며, 그 제외가 의도된 경우에만 `--allow-dirty`를 사용합니다.

zip을 받은 사용자는 Git 없이 압축을 풀고 같은 runner 명령을 사용할 수 있습니다.

```bash
unzip ReportMachine-source-{short_sha}.zip
cd ReportMachine-source-{short_sha}
python3 scripts/verify_layout.py
python3 scripts/run_article.py --mode industry --smoke-test
```

## 3. 입력 파일

기본 입력은 두 파일입니다.

```text
inputs/topic.md
inputs/sources.csv
```

`inputs/topic.md`에는 다음을 적습니다.

- `Task Type`: `industry` 또는 `earnings`
- `Topic`: 분석할 산업, 회사, 기술, 공급망, 규제, 시장구조
- `Core Question`: 글이 반드시 답해야 하는 질문
- `Recent Trigger`: 최근 뉴스, filing, 실적 발표, IR deck, 규제, 시장 이벤트
- `Required Source Notes`: 반드시 확인해야 할 URL 또는 repo-local 파일
- `Output Notes`: full report, web article, executive brief, QA review 등

`inputs/sources.csv`는 최종 주장과 이미지가 추적될 소스 목록입니다. 헤더는 고정입니다.

```text
source_id,article_type,company,period_role,period,doc_kind,issuer,date,title,url,source_tier,required,local_path,notes
```

가장 안정적인 방식은 PDF, CSV, deck 같은 자료를 repo 안에 두고 `local_path`로 지정하는 것입니다. HTTPS URL만 넣은 경우 자동 다운로드는 기본적으로 꺼져 있으며, `--allow-shell-network`와 허용 도메인 정책을 통과해야 합니다.

## 4. Codex CLI에서 실행

셸에서 `$industry-article` 또는 `$earnings-article`을 직접 호출하지 말고 OS-neutral runner를 사용합니다.

사전 확인:

```bash
python3 scripts/run_article.py --mode industry --smoke-test
python3 scripts/run_article.py --mode earnings --smoke-test
```

일반 산업 분석 실행:

```bash
python3 scripts/run_article.py --mode industry
```

실적/IR 분석 실행:

```bash
python3 scripts/run_article.py --mode earnings
```

러너는 내부적으로 `codex exec` 명령을 구성하고, `schemas/article_result.schema.json`에 맞는 최종 JSON을 `-o` 경로에 저장하게 합니다. 기본 web search 설정은 산업 글은 `cached`, 실적 글은 `live`입니다.

필요하면 web search를 바꿀 수 있습니다.

```bash
python3 scripts/run_article.py --mode earnings --web-search cached
python3 scripts/run_article.py --mode industry --web-search disabled
```

셸 기반 다운로드가 꼭 필요한 실행에서만 네트워크를 켭니다.

```bash
python3 scripts/run_article.py --mode earnings --allow-shell-network
```

TOML preset을 사용할 수 있습니다.

```bash
python3 scripts/run_article.py --config reportmachine.example.toml --profile default
```

개인 경로나 외부 산출물 루트가 필요하면 `reportmachine.example.toml`을 `reportmachine.local.toml`로 복사해 수정합니다. `reportmachine.local.toml`은 로컬 설정 파일로 취급합니다.

## 5. Codex App에서 실행

Codex App에서 바로 기사 생성을 요청할 때는 목적에 맞는 skill을 명시하거나 자연어로 요청합니다.

일반 산업 글:

```text
$article-intake 로 AI 데이터센터 전력 인프라 병목과 전력기기 공급망 분석 기사 준비
```

이미 입력과 소스가 준비되어 있고 바로 산업 글을 작성할 때:

```text
$industry-article 로 inputs/topic.md와 inputs/sources.csv를 기준으로 기사 작성
```

실적/IR 글:

```text
$earnings-article 로 current quarter와 prior quarter IR 자료를 비교해 실적 분석 기사 작성
```

Codex App 환경에서 nested `codex` CLI가 없거나, 인증 또는 PATH 문제 때문에 runner가 직접 `codex exec`를 실행하기 어려울 수 있습니다. 이때는 `--prepare-only`로 실행 준비만 만들고 현재 App 세션이 이어서 수행합니다.

```bash
python3 scripts/run_article.py --prepare-only --mode industry --run-id ai_power_grid_20260428 \
  --intake-topic "AI 데이터센터 전력 인프라 병목과 전력기기 공급망"
```

생성되는 manifest:

```text
work/runs/{run_id}/prepare_manifest.json
```

이 파일에는 입력 경로, 소스 전처리 결과, 최종 출력 경로, Codex가 따라야 할 prompt가 들어 있습니다. App-native fallback에서는 이 manifest와 repo-local prompt 계약을 기준으로 현재 Codex App 세션이 최종 산출물을 작성합니다.

## 6. 산출물 경로

실행별 중간 산출물:

```text
work/runs/{run_id}/research/
work/runs/{run_id}/ledgers/
work/runs/{run_id}/images/
work/runs/{run_id}/drafts/
work/runs/{run_id}/reviews/
```

최종 산출물:

```text
outputs/{run_id}/article.md
outputs/{run_id}/source_ledger.csv
outputs/{run_id}/image_ledger.csv
outputs/{run_id}/article_result.json
```

검증된 성공 실행 후에는 `outputs/latest/`가 최신 산출물로 갱신될 수 있습니다.

최종 `article_result.json`은 일반적으로 `codex exec -o`가 저장합니다. App-native fallback에서만 현재 Codex App 세션이 직접 작성할 수 있으며, 이 경우에도 `schemas/article_result.schema.json`을 만족해야 합니다.

명시적으로 요청한 경우에만 기사 완료 후 파생 A4 요약 산출물을 만들 수 있습니다.

```bash
python3 scripts/run_article.py --mode industry --summary-output
python3 scripts/run_summary.py --run-id {run_id}
```

요약 산출물은 article output이 아니라 derivative summary output입니다.

```text
outputs/{run_id}/summary/summary_a4.png
outputs/{run_id}/summary/summary_prompt.md
outputs/{run_id}/summary/summary_claim_map.csv
outputs/{run_id}/summary/summary_manifest.json
outputs/{run_id}/summary/summary_result.json
```

`summary_a4.png`는 Codex 내부 image generation tool 요청으로 생성되는 A4 요약 이미지입니다. Runner가 OpenAI API, File Search, vector store, 외부 image API를 직접 호출하지 않습니다. `article_result.json`이 `fail`이면 요약 이미지를 만들지 않고 `summary_result.json`도 실패로 남깁니다.

요약 이미지 style은 `reportmachine.local.toml`의 `[profiles.<name>.summary]`에서 inline TOML 값으로 조정합니다. 별도 Markdown style 파일은 사용하지 않습니다.

## 7. 소스와 이미지 원칙

중요 원칙은 다음입니다.

- 웹 검색은 후보 발굴용입니다.
- 최종 정량 주장과 이미지는 repo-local sources, source ledger, image ledger로 추적되어야 합니다.
- 출처 URL, 수치, author anecdote를 지어내면 안 됩니다.
- 일반 산업 글에는 valuation 중심 금융 지표와 투자 추천 표현을 쓰지 않습니다.
- 실적 글은 실적 품질 분석에 필요한 재무 지표를 허용하지만, target price, buy/sell/hold, valuation call은 여전히 금지입니다.
- AI 생성 이미지는 기사 산출물에 사용할 수 없습니다.
- AI 생성 A4 이미지는 `outputs/{run_id}/summary/` 아래의 파생 요약 산출물에서만 허용됩니다.
- 요약 산출물은 근거 자료가 아니며 `source_ledger.csv` 또는 `image_ledger.csv`를 대체하지 않습니다.
- 렌더링된 IR slide 이미지는 실제 이미지를 열어 확인한 뒤 caption을 써야 합니다.

Runner는 OpenAI API key를 요구하지 않습니다. OpenAI File Search 또는 vector-store upload도 사용하지 않습니다. Codex 사용은 `codex exec`, 설정된 web search, 로컬 렌더 이미지 입력, 그리고 summary output에서 명시된 Codex 내부 image generation tool 요청에 한정됩니다.

## 8. 흔한 실패와 확인 지점

프로젝트 설정이 로드되지 않는 경우:

- Codex에서 trusted project로 열었는지 확인합니다.
- `.codex/config.toml`과 `.article-root`가 있는지 확인합니다.
- `python3 scripts/verify_layout.py`를 실행합니다.

소스가 전처리되지 않는 경우:

- `inputs/sources.csv` 헤더가 고정 형식인지 확인합니다.
- `local_path`가 repo-root 기준 상대 경로인지 확인합니다.
- URL 다운로드는 HTTPS, 허용 도메인, `--allow-shell-network` 조건을 모두 만족해야 합니다.

실적 글이 실패하는 경우:

- current-quarter와 prior-quarter의 release/shareholder letter 및 investor presentation/IR deck이 모두 있어야 합니다.
- 필수 자료가 없으면 full article을 만들지 않고 `checklist_status: "fail"`과 non-publishable failure note가 나오는 것이 정상입니다.

산출물 검증이 실패하는 경우:

- 에이전트가 허용 경로 밖 파일을 만들었는지 확인합니다.
- 최종 파일 네 개가 모두 존재하는지 확인합니다.
- 생성물은 `work/runs/{run_id}/`, `outputs/{run_id}/`, `outputs/latest/` 아래에 있어야 합니다.
- `--summary-output` 실행에서 이미지 생성 도구가 없거나 `summary_a4.png`가 생성되지 않으면 summary run은 실패하지만 article output은 수정하지 않습니다.

## 9. 어떤 skill을 써야 하나

사용법, 실행법, 경로 설명, troubleshooting:

```text
$reportmachine-usage
```

짧은 자연어 주제를 기사 실행 계획으로 바꾸기:

```text
$article-intake
```

일반 산업/기술/공급망/규제/시장구조 기사 작성:

```text
$industry-article
```

실적 발표, IR deck, conference call, filings 기반 기사 작성:

```text
$earnings-article
```

기존 draft를 prompt, source, image, earnings 계약 기준으로 점검:

```text
$article-qa
```

완료된 기사에서 A4 summary output을 만들기:

```text
$summary-output
```
