# EARNINGS_PROMPT.md - Earnings And IR Article Contract

This file adds requirements for articles based on published company earnings releases, investor presentations, IR decks, filings, or conference-call transcripts.

`PROMPT.md` still controls general persona, tone, narrative, citation, image, and length rules. `FORMAT.md` controls the output-format floor. This file adds earnings-specific verification and overrides general rules only where needed for earnings analysis.

Investment recommendation, target price, valuation conclusion, buy/sell/hold language, share-price forecast, and portfolio-allocation instruction remain forbidden in every article type.

## Korean Output Contract

The final article remains natural Korean in honorific style. Keep these Korean literals where applicable:

- Scorecard heading: `## 한눈에 보기`
- Counterbalance language such as "진정으로 강해진 것들"
- Neutral IR translations: "상단으로 수렴 중", "큰 백로그", "구체 금액 미공시", "회사 내부 집계"

---

## 1. Storytelling And Structural Tools Must Coexist

`PROMPT.md` §3.10 applies to earnings articles: trigger -> technical or structural build-up -> woven branches -> closing.

At the same time, earnings articles must include:

- Scorecard: `§5-A.7`.
- Delta analysis: `§5-A.1` through `§5-A.5`.
- Counter-reading and counterbalance: `§5-A.3`, `§5-A.4`, and `§5-A.6`.
- Slide-image verification when decks are used: §3.

Do not misread "avoid boxed section listing" as permission to omit the scorecard or counterbalance. Purposeful tools such as summary cards, comparison tables, and counter sections strengthen storytelling when they serve the central narrative.

Preferred integration:

- Place the scorecard immediately before the intro as the reader's first judgment tool.
- Fold delta evidence into the body rather than isolating it as a mechanical "Q4 vs Q1" box unless a table genuinely helps.
- Place the counterbalance in the later body so critique does not become one-directional.

---

## 2. Positive-Bias Control For Earnings Materials

Earnings releases, investor presentations, and conference-call scripts are structurally positive documents. They are written to highlight strength, soften weakness, and preserve management's frame.

The article must not become a Korean summary of IR materials. It must separate what the company is trying to persuade investors to believe from what the evidence actually supports.

### 5-A.1 Do Not Conclude From One Quarter Alone

Before writing, collect the required current-quarter and prior-quarter earnings materials:

- Earnings release or shareholder letter.
- Investor presentation or IR deck.
- Conference-call transcript, if available.
- Relevant filings or regulatory materials.

Build a side-by-side comparison before drafting. YoY comparisons are often company-selected; QoQ or sequential comparison is where the analyst must do more work.

If a required current-quarter or prior-quarter earnings release/shareholder letter or investor presentation/IR deck cannot be obtained, do not draft a full earnings article. Return to research, or return a schema-valid failure with `checklist_status: "fail"` and a `critical_warnings` entry naming the missing material. The configured `article.md` output should be a non-publishable failure note, not a degraded article.

Transcripts and filings remain collected when available. If a transcript or filing appears to exist but cannot be obtained, identify it in missing materials and do not fill the gap with invented memory or assumptions.

### 5-A.2 Look First For Absence, Slowing, And Silence

Negative evidence in earnings materials often appears as omission or changed wording rather than explicit bad news. Check for:

- At least two prior-quarter highlights that disappear this quarter.
- The same theme shifting from annual wording to sequential wording.
- Sub-metrics that decelerate while headline growth still looks strong.
- The same cause explanation repeated with a larger or smaller claimed effect.
- Guidance range maintained while assumptions worsen.
- Risk-language movement, ordering changes, or reduced specificity.

Explore at least two disappeared prior-quarter highlights when prior-quarter materials are available. If fewer than two disappeared highlights are found, state the count explicitly and shift the analysis to wording strength, order, unit, and risk-language changes.

### 5-A.3 Identify And Separate The IR Frame

Name the company's IR frame in the body. Common frames include:

- "backlog absorbs macro shock"
- "service or aftermarket revenue smooths the cycle"
- "guidance trends to the high end"
- "structural growth"
- "margin pressure is designed investment"

The frame may be true. The article does not have to reject it. But it must not inherit it uncritically. Pair the frame with a counter-reading built from the same materials.

### 5-A.4 If Results Are Strong, Say So

Critical analysis must not become automatic negativity. If the quarter is genuinely strong, say so clearly.

Required balancing methods:

- Include a section or passage such as "진정으로 강해진 것들" when the article has a critical layer.
- If two interpretations remain plausible, present both and defer certainty.
- Leave concrete next-quarter verification points so the reader can test which interpretation is winning.

### 5-A.5 Translate IR Language Neutrally

IR adjectives often become more optimistic in Korean translation. Remove unnecessary adjectives and adverbs.

Use neutral Korean:

- "trending to high-end of range" -> "상단으로 수렴 중", not "상단으로 간다".
- "robust backlog" -> "큰 백로그", not "튼튼한 백로그".
- "customer-driven investments" -> explain the supplier-side investment implied by customer requests; do not simply amplify the IR frame as "고객 주도 투자".

### 5-A.6 Earnings Checklist

- [ ] Required current-quarter and prior-quarter materials were both collected; if not, drafting stopped with `checklist_status: "fail"` and `critical_warnings`.
- [ ] At least two disappeared prior-quarter highlights were explored, or fewer than two were explicitly stated with wording/order/unit/risk-language checks.
- [ ] Wording-unit shifts such as annual -> sequential were checked.
- [ ] Guidance assumptions were compared, not only guidance range.
- [ ] Repeated cause explanations with changed effect size were checked.
- [ ] The IR frame was explicitly named.
- [ ] A counter-reading from the same materials was provided.
- [ ] A counterbalance such as "진정으로 강해진 것들" prevents critique-only tone.
- [ ] Self-correction was checked in both optimistic and skeptical directions.

### 5-A.7 Required Top Scorecard

Every earnings article must place this scorecard block immediately before the intro:

```markdown
## 한눈에 보기

**종합 점수: XX / 100**
헤드라인 XX · Quality XX · 선행지표 XX · 투명성 XX · 실행 XX  *(각 20점 만점)*

**평가**: (2-4 문장으로 핵심 요약. 본문을 안 읽어도 이 분기가 어떤 분기였는지 파악 가능해야 함. 강약점을 모두 포함하고 한 방향으로 치우치지 않을 것.)

---
```

Five axes, each out of 20:

1. 헤드라인 강도 - revenue, profit, growth, orders, beat/miss, or surface strength when sourced.
2. Earnings quality - how clean the headline is after one-offs, accounting effects, tax benefits, asset sales, working-capital timing, M&A remeasurement, or non-recurring add-backs.
3. 선행지표 - backlog, pipeline, book-to-bill, guidance, demand indicators, or future-direction evidence.
4. 투명성 - specificity, absence of silence, absence of excessive euphemism, consistency of disclosed items, and reduced narrative recycling.
5. 실행력 - evidence against prior management commitments such as margin targets, CapEx plans, M&A milestones, credit-rating goals, project timing, or product execution.

Score interpretation:

- 90+ = unusually strong quarter across nearly all axes.
- 80-89 = strong quarter with limited caution points.
- 70-79 = good but with one or two visibly weak axes.
- 60-69 = mixed; interpretation requires real work.
- Under 60 = weak or unclear quarter across several axes.

The score measures the quarter's relative evidence quality, not stock upside. It is not a company-to-company ranking tool. Same-company quarter-to-quarter comparison is usually more meaningful.

### 5-A.8 Denominator Checks For "Exceeded Full Year" Claims

When management says a quarter exceeded a prior full year, verify the denominator.

Check:

1. Is the numerator a disclosed current-quarter number?
2. Is the denominator disclosed on the same sub-line?
3. If the denominator is not disclosed for the same sub-line, label it as "회사 내부 집계" or "구체 금액 미공시".
4. State which sub-line's prior full-year base is being compared.

Common traps:

- Current quarter sub-category orders compared against ambiguous prior full-year orders.
- New-area revenue compared against a prior-year category whose definition changed.
- "single quarter exceeded prior full year" claims where the prior-year base is too small or undisclosed.

Do not use dramatic wording unless both numerator and denominator are externally verifiable.

---

## 3. Slide And Image Substance Verification

When using rendered slides from a PDF deck, do not infer content from slide number. Deck order changes by company and quarter.

Required workflow:

1. Render candidate slides into images.
2. Open each rendered image directly with an image-reading tool.
3. Confirm the actual title, axes, labels, table values, and visible caveats.
4. Write captions from what is visible in the image, not from deck habits or slide-number guesses.
5. If a rendered slide contains information not present in the press release, treat it as an independent primary source and reflect it in the article or ledger.

Forbidden:

- Assuming slide 3 is always KPIs or slide 6 is always segment data.
- Inserting a rendered slide without opening it.
- Reusing prior-quarter deck structure as a guide to current-quarter slide content.

Image QA checklist:

- [ ] 쓰려는 모든 이미지 파일을 Read 툴로 직접 열었는가
- [ ] 각 이미지의 실제 제목 · 차트 축 · 표 내용을 파악했는가
- [ ] 본문 해설과 이미지 캡션이 같은 데이터를 가리키는가
- [ ] 렌더링된 슬라이드 안에 press release 에는 없는 추가 정보가 있다면 본문에도 반영했는가

---

## 4. Earnings Format Floor

In addition to `FORMAT.md`, earnings articles must pass:

- [ ] Required current-quarter and prior-quarter materials compared side by side; missing required release/deck materials remain a fail/research gate.
- [ ] At least two disappeared prior-quarter highlights explored, or fewer than two explicitly stated with wording/order/unit/risk-language checks.
- [ ] Annual -> sequential wording shifts checked.
- [ ] Guidance range and guidance assumptions both checked.
- [ ] Repeated explanations checked for changed effect size.
- [ ] IR frame and counter-reading both present.
- [ ] "진정으로 강해진 것들" or equivalent counterbalance present when critique is material.
- [ ] Self-correction is not only optimistic.
- [ ] `## 한눈에 보기` scorecard appears immediately before the intro.
- [ ] Every slide caption is based on direct rendered-image inspection.
- [ ] "작년 전체 초과" / "single quarter exceeded prior full year" style claims have numerator and denominator verification.

---

## 5. Pre-Draft Gate

Before drafting an earnings article, answer:

- Have required current-quarter and prior-quarter release/deck materials been collected?
- What is this quarter's one-sentence insight, and does it pass `PROMPT.md` §3.9?
- Can the five scorecard axes be scored with evidence?
- What genuinely strengthened, even if the article is critical?
- What would next quarter have to show to confirm or falsify the article's interpretation?

If any answer is missing, return to research instead of writing. If required current-quarter or prior-quarter release/deck materials cannot be obtained, return `checklist_status: "fail"` with `critical_warnings` and write only a non-publishable failure note to the configured article output. Do not invent evidence to fill the gap.

---

Read this file together with `PROMPT.md`, `FORMAT.md`, `SOURCE_POLICY.md`, and `AUTHOR_PROFILE.md`. Earnings-specific metrics are allowed only for earnings-quality analysis; valuation and recommendation language remains prohibited.
