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

런타임 의존성을 먼저 설치합니다. 러너는 단계 산출물을 스키마로 검증하므로 `jsonschema`가 필요합니다.

```bash
python3 -m pip install -e .
```

이 의존성 없이도 `--phase prepare`, `--prepare-only`, `--phase draft`, `--dry-run`, `--smoke-test`는 실행할 수 있습니다. `draft`는 기존 upstream JSON/JSONL의 구문, 파일 존재, scope 승인, claim/evidence traceability를 검사하지만 JSON Schema 검증만 생략합니다. 나머지 단계와 `--phase all`은 Codex를 호출하기 전에 exit code 2와 설치 안내로 중단됩니다. Codex App에서 nested CLI 없이 이어서 작업하는 App-native fallback은 `--prepare-only`만 사용하므로 이 제약과 무관합니다.

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

팀 내부 배포 도구가 이미지 파일만 허용하고 업로드한 JPG 원본 바이트를 그대로 보존하는 특수한 경우에는 실험용 image-binder 산출물을 명시적으로 만들 수 있습니다.

```bash
python3 scripts/package_source.py --experimental-image-binder --acknowledge-image-only-internal-distribution
```

이 명령은 검증된 ZIP payload를 JPG cover 뒤에 붙여 `dist/source/ReportMachine-source-{short_sha}-image-binder.jpg`를 만듭니다. cover에는 ZIP 파일을 포함한 File Binder 기반 실험용 내부 배포 파일이라는 경고 문구가 보이게 렌더링됩니다. 한글 렌더링이 가능한 환경이면 한국어와 영어 문구를 모두 렌더링하고, 한글 렌더링이 보장되지 않으면 영어 문구만 렌더링합니다. 이 옵션은 표준 ZIP 배포를 대체하지 않으며, 전달 도구가 이미지를 리사이즈, 최적화, trailing byte 제거, 재인코딩하면 ZIP payload가 손실될 수 있습니다.

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

기본 실행은 `--phase all`이며 다음 순서로 진행합니다.

```text
prepare -> scope -> research-plan -> research -> evidence -> narrative
       -> draft -> review -> revise -> finalize -> audit
```

각 단계는 상류 산출물이 존재하고 스키마 검증을 통과해야 시작하며, 끝날 때 자신의 필수 산출물을 검증합니다. 단계마다 역할별 `codex exec`가 한 번씩 실행되고, 호출 기록은 `work/runs/{run_id}/agents/{role}/attempt_NNN.json`에, 그때 사용된 유효 프롬프트는 `work/runs/{run_id}/contracts/`에 남습니다.

`research`가 새 URL이나 local path를 발견하면 러너가 후보를 다시 ingest합니다. 허용 정책을 통과한 자료는 run-local로 복사 또는 다운로드되어 해시와 `source_id`를 받고, HTML은 script/style/comment/hidden DOM을 제거한 가시 텍스트만, PDF는 추출 도구가 처리한 페이지 텍스트만 `research/extracted_text/{source_id}.txt`에 기록됩니다. 다운로드하지 못한 후보는 `remote_unverified` 또는 `blocked`로 남으며 verified evidence로 승격할 수 없습니다. 이후 단일 단계를 재실행할 때는 이 확장된 ingest snapshot을 재사용합니다. 기본 입력을 새로 수집하려면 `--phase prepare`를 다시 실행합니다.

| 단계 | 역할 | 주요 산출물 |
|---|---|---|
| `prepare` | 없음 | intake, source ingest, preflight, manifest |
| `scope` | `scope_guardian` | `intake/intent_contract.json` |
| `research-plan` | `research_planner` | `research/research_question_tree.json` |
| `research` | 레인별 `primary_source_scout` + `counterevidence_scout` | `research/source_candidates.*.jsonl` |
| `evidence` | `evidence_verifier` → `evidence_integrator` | `evidence/evidence_cards.jsonl`, 충돌·공백 원장 |
| `narrative` | `thesis_adjudicator` → `narrative_architect` | `research/thesis_decision.json`, `research/narrative_plan.json` |
| `draft` | `article_writer` | `drafts/draft_article.md` |
| `review` | citation·red team·QA·compression (+조건부 image) | `reviews/*.json` |
| `revise` | `article_writer` | 개정 draft, `reviews/revision_notes.json` |
| `finalize` | `finalizer` | `article.md`, `research_appendix.md`, `paragraph_map.json`, 재생성 ledger |
| `audit` | 없음 (코드 전용) | `outputs/{run_id}/final_sync_report.json` |

```bash
python3 scripts/run_article.py --mode earnings --phase prepare
python3 scripts/run_article.py --mode earnings --phase draft --run-id <run_id>
python3 scripts/run_article.py --mode earnings --phase finalize --run-id <run_id>
python3 scripts/run_article.py --mode earnings --phase audit --run-id <run_id>
```

`--phase audit`은 모델을 호출하지 않고 이미 존재하는 최종 산출물을 교차검증합니다. 정상 runner 워크플로에서는 finalize checkpoint가 일치해야 audit이 통과합니다. checkpoint를 만든 적이 없는 과거 런이나 Codex App 직접 완료 런을 코드 수준에서만 검사하려면 CLI 전용 면제를 명시해야 합니다.

```bash
python3 scripts/run_article.py --mode earnings --phase audit --run-id <run_id> \
  --allow-unprovenanced-audit
```

### 단계 재개와 provenance

각 모델 단계는 산출물 검증과 의미 검증을 모두 통과한 뒤 `work/runs/{run_id}/provenance/{phase}.json`에 완료 상태를 기록합니다. 코드 전용 `audit` 단계는 checkpoint를 만들지 않습니다. checkpoint에는 그 시점까지의 계약 산출물, 인용 검증에 쓰는 추출 텍스트, 이미지 감사 대상 렌더 이미지, earnings delta 산출물의 SHA-256이 담깁니다. finalize checkpoint에는 schema 검증을 통과한 `article_result.json`도 포함되므로, finalize 뒤 제목·경고·체크리스트 상태·출력 메타데이터를 바꾸면 audit에서 provenance 불일치로 차단됩니다.

추적 대상은 단계별 계약 하나로 정의됩니다. 누적 산출물뿐 아니라 각 단계의 role이 실제로 읽는 입력, 즉 정규화된 `topic.md`와 `sources.csv`, canonical source/rendered-image ledger, 그리고 ledger가 가리키는 extracted text와 rendered image도 포함되므로 체크포인트 이후 이 입력을 바꾸면 재개가 차단됩니다. 체크포인트 당시 없던 입력이 나중에 생긴 경우도 차단됩니다.

같은 계약이 역할별 attempt 기록에도 쓰입니다. 리뷰 역할의 intent contract, evidence verifier가 인용 대조에 쓰는 추출 텍스트, image auditor의 렌더 이미지 원장과 이미지, 이미지가 있는 런에서 finalizer가 최종 image ledger를 만들 때 쓰는 image audit 산출물, counter scout의 `sources.csv`가 attempt 입력 해시에 자동으로 포함되므로, 체크포인트와 attempt의 보호 대상이 서로 어긋나지 않습니다. 렌더 이미지 자체는 `image_slide_auditor`의 입력과 attempt hash에만 들어가며 non-auditor role에는 image input을 전달하지 않습니다.

source candidate 원장과 rendered image 원장은 파일 단위가 아니라 행 단위로 기록합니다. `fetched_at`을 제외한 모든 컬럼을 해시하므로 `ingest_status`, `sha256`, `allowed_domain`처럼 downstream이 읽거나 최종 산출물이 보존하는 값을 나중에 바꾸면 차단됩니다. 기록된 행의 삭제도 차단됩니다.

체크포인트에 없던 행은 러너가 직접 만든 것임이 증명될 때만 허용합니다. `prepare` receipt는 그 ingest가 다시 쓴 현재 행을 replace 방식으로 기록합니다. `research`와 `evidence` receipt는 기존의 모든 유효 receipt에서 현재도 같은 digest인 행만 이어받고, 현재 ingest가 실제로 썼다고 보고한 before/after delta만 추가합니다. 각 receipt에는 이 행들의 해시와 그때 읽은 입력(`sources.csv`와 해당 candidate 파일)의 해시가 함께 남습니다. 재개할 단계 이름은 호출자가 정하는 값이므로 근거로 쓰지 않습니다.

receipt는 기록된 입력이 현재 파일과 모두 일치하고 기록된 행 digest도 그대로일 때만 유효합니다. 따라서 입력을 바꿔 ingest한 뒤 입력만 원래대로 되돌려도 그때 생긴 행은 보증을 잃고 차단되며, 입력이 삭제되거나 수정된 경우도 같습니다. 기존 receipt에 없던 수동 행, 변조된 행, 현재 ingest가 쓰지 않은 무관한 행은 cumulative receipt에 흡수되지 않습니다.

따라서 추가 조사가 만든 행은 `evidence` 재개에서 통과하지만, receipt 없이 손으로 넣은 행은 어떤 단계를 재개하더라도 차단됩니다. receipt가 있어도 그 뒤 행을 수정하면 해시가 달라져 차단됩니다. 단계 재실행은 현재 단계와 downstream checkpoint만 삭제하고 receipt 파일은 보존합니다. 보존된 receipt는 입력 hash가 달라지면 자동으로 효력을 잃으며, ingest가 없는 재실행에서는 입력과 행이 그대로인 기존 유효 receipt만 계속 사용할 수 있습니다.

체크포인트 당시 원장이 아예 없었던 경우도 같은 규칙을 따릅니다. 검증이 기록된 원장과 현재 원장을 모두 순회하므로, 나중에 만들어진 원장 전체가 receipt 없이 통과하는 일은 없습니다. 원장 경로는 `--no-source-ingest` 여부와 무관하게 런 디렉터리에서 결정되므로 이 플래그로 검증을 우회할 수 없습니다.

단일 단계를 재개하면 러너는 Codex를 호출하기 전에 직전 단계의 checkpoint를 현재 파일과 대조합니다. checkpoint가 없거나 손상됐거나 run id가 다르거나 기록된 산출물이 바뀐 경우 exit code 2로 중단됩니다. 스키마와 의미 검증만으로는 "역할이 만든 뒤 조용히 편집된 상태"를 구분할 수 없기 때문입니다.

checkpoint는 소급 생성하지 않습니다. 이전에 만든 런을 단일 단계로 이어서 실행하려면 `--phase all`이나 필요한 가장 이른 모델 단계부터 다시 실행해야 합니다. `--allow-unprovenanced-audit`도 checkpoint를 만들거나 과거 실행을 소급 봉인하지 않습니다.

런이 첫 checkpoint를 기록하면 `outputs/{run_id}/run_manifest.json`에 `provenance_marker`가 남고 이후 재개에서도 보존됩니다. 이 marker는 provenance 디렉터리 밖에 있으므로, 디렉터리를 통째로 지워도 해당 런이 checkpoint를 기록했다는 사실은 남습니다.

`--phase audit`은 기본적으로 일치하는 finalize checkpoint를 요구합니다. marker와 checkpoint 파일이 모두 없는 과거 런이나 App-native 런도 기본 실행에서는 다른 코드 검증과 fresh `final_sync_report.json` 생성을 마친 뒤 exit code 2, 전체 상태 `audit_failed`로 실패하며 `outputs/latest/`를 갱신하지 않습니다.

이런 런을 의도적으로 코드 수준에서만 감사할 때 `--allow-unprovenanced-audit`를 붙일 수 있습니다. 이 옵션은 `--phase audit`에서만 유효한 CLI 옵션이며 TOML preset에는 넣을 수 없습니다. 나머지 검사가 모두 통과하면 exit code 0이 되고, stdout과 `logs/{run_id}/run_summary.json`에 `provenance_status: waived_no_checkpoint`가 남습니다. run summary의 전체 상태는 `audit_passed_with_provenance_waiver`입니다. 면제 성공도 `outputs/latest/`를 갱신하지 않습니다. 정상 finalize provenance까지 검증된 audit은 run summary 전체 상태 `audit_passed`를 기록합니다.

면제는 실제 경로 기준으로 marker와 **모든** checkpoint 파일이 없을 때만 적용됩니다. marker가 있거나 checkpoint 파일이 하나라도 있으면, JSON 파싱에 실패한 파일·object가 아닌 JSON·일부 단계만 남은 상태·finalize checkpoint 누락·run/version/phase/hash 불일치를 모두 면제할 수 없고 exit code 2로 실패합니다. 따라서 플래그가 부분적이거나 손상된 provenance를 legacy 런으로 오인하게 만들지 않습니다.

checkpoint와 receipt 형식은 버전이 매겨져 있습니다. 현재 버전은 5이며 이전 형식(v1~v4)으로 기록된 checkpoint와 receipt는 재개를 차단하므로 해당 단계부터 다시 실행해야 합니다.

### provenance 기록 자체의 보호

checkpoint, ingest receipt, run manifest는 role이 실행되는 동안 고정됩니다. role이 이 파일을 수정·생성·삭제하면 해당 단계가 실패합니다. 이 기록들이 run 디렉터리 안에 있어 원래는 role이 쓸 수 있었고, 그대로 두면 role이 자신을 판정하는 근거를 위조할 수 있기 때문입니다.

CLI role을 호출하기 전에 러너가 선언된 output의 parent directory를 만듭니다. Role은 선언된 exact path만 출력할 수 있고, 호출 전에는 absent 또는 regular file, 호출 후에는 regular file이어야 합니다. Symlink, directory, FIFO·socket·device 같은 special file은 output으로 거부합니다. `work/runs/{run_id}/`, `outputs/{run_id}/`, `logs/{run_id}/`, `outputs/latest/` 같은 넓은 prefix는 role 쓰기 권한이 아닙니다.

이 exact allowlist 검사는 exit 0과 nonzero뿐 아니라 timeout, 일반 exception, `KeyboardInterrupt`, cancellation을 포함한 모든 catchable 종료 경로에서 수행되고 attempt도 마감됩니다. 위반은 fail closed이며 해당 checkpoint를 쓰지 않습니다.

Pipeline role 실행은 POSIX host만 지원합니다. 각 role은 독립 session/process group에서 실행되고, 러너는 정상·nonzero·catchable 종료 모두에서 descendant 전체를 종료·확인한 뒤에만 경계 검사나 `outputs/latest` 복원을 시작합니다. Non-POSIX role 요청은 process를 띄우기 전에 status 2와 failed attempt로 끝납니다. Summary·publish 같은 non-role 명령의 실행 방식은 바뀌지 않습니다. `events_log`와 `stderr_log`는 role output이 아니라 runner-owned append-only 파일이며, 호출 전 bytes prefix와 동일 file identity를 보존하는 정상 append만 허용됩니다.

또한 각 단계는 자기가 소유한 산출물만 쓸 수 있습니다. Handler가 성공·실패·예외 중 어느 경로로 끝나도 phase guard가 누적 추적 대상, canonical ledger, ledger-derived extracted text와 rendered image를 검사합니다. 단계 실행 중 상위 단계의 산출물이 바뀌면 checkpoint, final export, audit 성공, latest 승격을 금지합니다. Candidate merge와 source ingest만 exact files, 최소 runner directory, deletion policy로 제한된 국소 runner mutation 구간에서 허용되며, 성공한 구간만 guard baseline에 반영됩니다.

일반 phase-owned 산출물의 상위 변조 오류는 계약에 기록된 가장 이른 producer 단계를 정확히 안내합니다. 반면 canonical source/render ledger나 그 원장이 가리키는 extracted text/rendered image가 변조되면 receipt row lineage가 부분 재개의 안전성을 증명하지 못하므로 항상 `--phase all`을 안내합니다. Lineage가 없거나 유효하지 않거나 모호한 경우도 동일합니다.

`outputs/latest`는 role 쓰기 범위가 아닙니다. 호출 전 root는 absent 또는 symlink가 아닌 실제 directory여야 하며, root 자체가 symlink, regular file, special file이면 복원을 시도할 대상이 아니라 호출 전 hard failure입니다. 실제 directory인 경우 러너는 내부 entry set과 type, regular-file bytes, symlink target text를 보존하고 symlink를 따라가지 않습니다. Catchable role failure, exact-allowlist 위반, latest drift가 생기면 cleanup에서 기존 absent/tree 상태로 복원하고 다시 검증합니다. 복원 또는 재검증 실패는 status 2이며 checkpoint, final export, audit 성공, latest 승격을 모두 금지합니다.

이 경계는 단일 runner, 실행 중 산출물을 읽는 동시 reader 없음, catchable process 종료를 전제로 한 사후 검출 장치입니다. `outputs/latest` 외 상위 파일의 전체 rollback, real repo 밖 atomic staging, multi-file commit journal, `kill -9`·host crash recovery, same-run locking, 적대적 role의 repo 밖 쓰기·network·secret 격리는 제공하지 않습니다.

### 의도 고정과 범위 승인

기사 작성 전에 사용자 원문과 정규화된 범위의 차이를 승인해야 합니다. 승인 없이는 `draft` 이후 단계가 차단됩니다.

```bash
python3 scripts/run_article.py --mode industry --run-id <run_id> \
  --intake-original-request "사용자 원문을 그대로" \
  --intake-analysis-mode market_reaction \
  --intake-analysis-mode company_fundamental \
  --intake-depth standard \
  --intake-approve-scope
```

- `--intake-original-request` 또는 `--intake-original-request-file`로 원문을 보존합니다. 생략하면 intent contract에 경고가 남습니다.
- `--intake-analysis-mode`는 반복 지정할 수 있습니다. `market_reaction`을 승인하면 실제 등락·조정 폭 같은 관찰 사실을 쓸 수 있지만, 목표가·매수매도·밸류에이션 판단은 모든 모드에서 계속 금지됩니다.
- `--intake-depth`는 `brief|standard|deep`이며 줄 수 목표를 대체합니다. 분량 목표와 이미지 최소 개수는 폐지됐습니다.
- `scope_status`가 `conflicting`이면 `--intake-approve-scope`로도 통과할 수 없습니다.
- 기존 intent contract가 JSON으로 손상됐거나 다른 `run_id`를 가리키면 러너는 어떤 ingest나 Codex 호출보다 먼저 실패합니다. 의도적으로 재생성할 때만 `--intake-reset-intent-contract`를 사용합니다. 기존 contract와 original request는 `intake/*.mismatched_NNN.*`로 백업되고 새 contract는 `pending`으로 시작하므로, 범위 승인은 별도 실행에서 다시 적용해야 합니다.

### 차단된 필수 출처

필수 출처가 `blocked`, `remote_unverified`, `stale` 상태면 preflight가 실패합니다. 사용자가 감수하고 진행하려면 출처 ID를 명시적으로 면제해야 합니다.

```bash
python3 scripts/run_article.py --mode industry --run-id <run_id> --waive-blocked-source S016
```

면제된 출처만 사용하는 클레임은 `supported` + `needs_human_review=false`로 남길 수 없고, `conditionally_supported`나 human review 표시가 필요합니다.

러너는 내부적으로 `codex exec` 명령을 구성하고, `schemas/article_result.schema.json`에 맞는 최종 JSON을 `-o` 경로에 저장하게 합니다. 기본 web search 설정은 산업 글은 `cached`, 실적 글은 `live`입니다. 실적 preflight는 current/previous release 계열과 deck 계열 자료 네 가지가 모두 source ingest 이후 사용 가능해야 pass입니다.

필요하면 web search를 바꿀 수 있습니다.

```bash
python3 scripts/run_article.py --mode earnings --web-search cached
python3 scripts/run_article.py --mode industry --web-search disabled
```

셸 기반 다운로드가 꼭 필요한 실행에서만 네트워크를 켭니다.

```bash
python3 scripts/run_article.py --mode earnings --allow-shell-network
```

Codex 이벤트 로그가 필요하면 `--json-events`를 사용합니다.

```bash
python3 scripts/run_article.py --mode earnings --json-events
```

생성되는 로그:

```text
logs/{run_id}/codex_events.jsonl
logs/{run_id}/stderr.log
logs/{run_id}/run_summary.json
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

App-native 실행은 model-phase checkpoint를 만들지 않으므로 최종 산출물 작성 뒤 코드 전용 audit 면제를 명시합니다.

```bash
python3 scripts/run_article.py --mode industry --phase audit --run-id ai_power_grid_20260428 \
  --allow-unprovenanced-audit
```

이 면제는 provenance를 소급 생성하지 않으며, 성공해도 `outputs/latest/`를 갱신하지 않습니다. marker나 checkpoint 파일이 하나라도 생긴 런에는 사용할 수 없습니다.

## 6. 산출물 경로

실행별 중간 산출물:

```text
work/runs/{run_id}/intake/original_request.md
work/runs/{run_id}/intake/intent_contract.json
work/runs/{run_id}/research/
work/runs/{run_id}/research/lanes/
work/runs/{run_id}/research/extracted_text/
work/runs/{run_id}/evidence/evidence_cards.jsonl
work/runs/{run_id}/ledgers/
work/runs/{run_id}/images/
work/runs/{run_id}/drafts/
work/runs/{run_id}/reviews/
work/runs/{run_id}/agents/{role}/attempt_NNN.json
work/runs/{run_id}/provenance/{phase}.json
work/runs/{run_id}/provenance/ingest/{stage}.json
work/runs/{run_id}/contracts/effective_prompt_{phase}_{role}.md
```

최종 산출물:

```text
outputs/{run_id}/article.md
outputs/{run_id}/research_appendix.md
outputs/{run_id}/paragraph_map.json
outputs/{run_id}/source_ledger.csv
outputs/{run_id}/image_ledger.csv
outputs/{run_id}/claim_ledger.json
outputs/{run_id}/preflight_result.json
outputs/{run_id}/run_manifest.json
outputs/{run_id}/article_result.json
outputs/{run_id}/final_sync_report.json
```

### 최종 교차감사가 실제로 검사하는 것

`audit` 단계는 ledger가 스스로 `supported`라고 선언한 값을 신뢰하지 않고 다시 계산합니다. 다음은 모두 실패 조건입니다.

- claim의 `source_id`가 source ledger에 없음
- `blocked`/`remote_unverified`/`stale` 출처만 쓰는 claim이 `supported` + `needs_human_review=false`
- evidence card 또는 research appendix가 없거나, ledger·paragraph map이 동결 기사보다 먼저 생성됨
- `remote_manual_verified` 출처를 쓰면서 human review 미표시
- claim 앵커 heading이 최종 기사에 그대로 존재하지 않음, 또는 `paragraph_id`가 paragraph map에 없음
- claim이 존재하지 않는 `evidence_id`를 참조
- evidence card의 `exact_quote`가 추출된 원문 텍스트에 없음
- `reviews/image_audit.json`과 final image ledger 사이의 이미지 set, shared decision field, `caption_candidate` 불일치
- rendered-image ledger의 source/page/hash metadata 불일치, keep 이미지 파일 부재, 해시 불일치, 기사 미참조, `visual_question`·`supported_claim_ids` 누락
- final `caption`이 canonical `caption_candidate`를 덮어쓰거나, 감사되지 않은 수치·사실·출처를 추가함
- `article_result.json`의 `article_sha256`이 실제 기사 파일과 불일치하거나 누락
- paragraph map의 줄 범위가 기사 길이를 초과

추출 텍스트가 없어 인용을 코드로 확인할 수 없는 출처는 실패가 아니라 경고로 남고, human review 대상이 됩니다.

`work/runs/{run_id}/reviews/image_audit.json`이 유일한 canonical image decision artifact이며 별도 image-ledger candidate는 계약이 아닙니다. Final image ledger의 shared decision fields와 `caption_candidate`는 이 report와 일치해야 합니다. Final `caption`은 frozen article 문맥에 맞춰 다르게 파생할 수 있지만 canonical candidate를 변경하지 않고 기존 숫자·근거·source/render metadata 검증을 통과해야 합니다. 렌더 이미지가 없는 run은 image audit report 없이 빈 final image ledger로 진행할 수 있습니다.

실적 글이 성공 상태로 끝나려면 다음 산출물도 필요합니다.

```text
outputs/{run_id}/earnings_delta_matrix.json
outputs/{run_id}/earnings_delta_matrix.md
```

finalize provenance까지 검증된 성공 실행 후에만 `outputs/latest/`가 최신 산출물로 갱신될 수 있습니다. `waived_no_checkpoint` audit은 exit code 0이어도 latest 승격 대상이 아닙니다.

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

## 6.1 읽기 편한 A4 PDF 출판

완료된 기사를 A4 세로 조판 PDF로 출판할 수 있습니다. 명시적으로 요청한 경우에만 실행됩니다.

```bash
python3 scripts/run_article.py --mode industry --publish-pdf
python3 scripts/run_publish.py --run-id {run_id}
```

생성되는 산출물:

```text
outputs/{run_id}/publish/article.pdf
outputs/{run_id}/publish/publish_figure_map.csv
outputs/{run_id}/publish/publish_manifest.json
outputs/{run_id}/publish/publish_result.json
```

이 렌더러는 Codex 호출, 웹 검색, 외부 API 요청을 하지 않는 결정론적 로컬 렌더러입니다. 같은 입력이면 항상 같은 PDF가 나옵니다.

조판 규칙은 다음과 같습니다.

- 표지에 제목, 부제, `one_sentence_insight`, run_id, 생성 시각을 넣고 H2/H3 기준 목차를 만듭니다.
- 이미지는 본문이 참조한 위치에 그대로 두고 `그림 N` 번호를 붙입니다. 캡션은 `image_ledger.csv`의 `caption` → `caption_candidate` → 대체 텍스트 순으로 채우고 `source_id`와 페이지를 덧붙입니다.
- 이미지와 캡션은 같은 페이지에 유지되고, 본문 폭에 맞춰 비율을 지키며 최대 높이는 본문 높이의 60%입니다.
- 본문 인용 링크는 클릭 가능한 상태로 두고, 문서 끝 `링크 각주` 절에 전체 URL을 인쇄합니다.

PDF 출판에는 선택적 렌더러 의존성과 한글 TrueType 폰트가 필요합니다.

```bash
python3 -m pip install -e ".[publish]"
```

bold 한글 폰트는 선택 사항입니다. 이식 가능한 시스템 경로에 bold 한글 TTF가 없는 경우가 많으므로, 해상되지 않으면 regular 폰트에 매핑하고 제목 위계는 크기와 색으로 구분합니다. 자동 탐색이 실패하면 폰트를 직접 지정합니다.

```toml
[profiles.default.publish]
enabled = true
font_regular = "/usr/share/fonts/truetype/nanum/NanumGothic.ttf"
```

PDF는 파생 출판 산출물입니다. `article.md`, ledger, `article_result.json`을 수정하지 않으며 근거 자료를 대체하지도 않습니다. 상태는 `publish_result.json`에만 기록합니다. `article_result.json`이 `fail`이면 PDF를 만들지 않고 실패 상태의 `publish_result.json`만 남깁니다.

## 7. 소스와 이미지 원칙

중요 원칙은 다음입니다.

- 웹 검색은 후보 발굴용입니다.
- 최종 정량 주장과 이미지는 repo-local sources, source ledger, image ledger로 추적되어야 합니다.
- 핵심 주장은 `claim_ledger.json`에서 source_id와 source_quote_or_datapoint에 연결되어야 합니다.
- 출처 URL, 수치, author anecdote를 지어내면 안 됩니다.
- 일반 산업 글에는 valuation 중심 금융 지표와 투자 추천 표현을 쓰지 않습니다.
- 실적 글은 실적 품질 분석에 필요한 재무 지표를 허용하지만, target price, buy/sell/hold, valuation call은 여전히 금지입니다.
- AI 생성 이미지는 기사 산출물에 사용할 수 없습니다.
- AI 생성 A4 이미지는 `outputs/{run_id}/summary/` 아래의 파생 요약 산출물에서만 허용됩니다.
- 요약 산출물은 근거 자료가 아니며 `source_ledger.csv` 또는 `image_ledger.csv`를 대체하지 않습니다.
- 렌더링된 IR slide 이미지는 `image_slide_auditor`만 role image input으로 받아 실제 이미지를 확인합니다. Non-auditor role은 rendered image input을 받지 않습니다.
- `image_audit.json`이 이미지 keep/drop, anchor, claim support, canonical `caption_candidate`의 단일 authority입니다. Candidate ledger를 만들거나 참조하지 않습니다.
- Final image ledger는 audit report와 rendered-image ledger를 교차검증하며 `actual_slide_title`, `detected_key_datapoints`, `article_anchor_before`, `article_anchor_after`, `keep_or_drop`, `drop_reason`을 포함해야 합니다. Kept image는 본문에서 실제 참조되어야 합니다.

Runner는 OpenAI API key를 요구하지 않습니다. OpenAI File Search 또는 vector-store upload도 사용하지 않습니다. Codex 사용은 `codex exec`, 설정된 web search, image auditor에만 전달되는 로컬 렌더 이미지 입력, 그리고 summary output에서 명시된 Codex 내부 image generation tool 요청에 한정됩니다.

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
- preflight fail은 Codex를 호출하지 않고 deterministic `article_result.json`, `preflight_result.json`, `run_manifest.json`을 남깁니다.

산출물 검증이 실패하는 경우:

- Role이 선언된 exact regular-file output 외의 파일을 만들거나 바꿨는지 확인합니다. Parent directory는 role이 아니라 러너가 호출 전에 만듭니다.
- `outputs/latest` root가 symlink·file·special entry인지, 또는 role 실행 중 latest tree가 바뀌었다가 복원 검증에 실패했는지 확인합니다.
- 상위 canonical ledger나 derived source/image 변조 오류는 receipt 유무와 관계없이 부분 재개 대신 안내대로 `--phase all`을 실행합니다.
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
