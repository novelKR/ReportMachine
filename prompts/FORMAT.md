# FORMAT.md - Minimum Article Output Floor

This file defines the minimum structural and formatting floor for ReportMachine articles. It is not a content template.

Do not copy the same external skeleton across articles. A valid article can have different section names and order each time, as long as it satisfies this floor and the narrative rules in `PROMPT.md`.

## Korean Output Contract

Final article prose must be natural Korean in honorific style (`~입니다`, `~합니다`). Keep Korean output literals where specified, including the article title/subtitle format, scorecard label `한눈에 보기`, Korean disclaimer intent, and Korean citation examples.

---

## 1. Structural Minimums

### 1.1 Header

The article must begin with:

- `# {{한 줄 제목}}` - one-line Korean title. No series number, installment label, or prior-article reference.
- `*{{부제}}*` - one Korean subtitle stating the article's central question or central observation.

### 1.2 Intro Requirements

The intro must include:

- A recent trigger from the last days or weeks where possible: news, filing, order, regulation, market event, data point, or earnings material. Cite the original URL.
- The question the article will answer, preferably in one sentence near the end of the intro.
- Scope and limits: what the article covers and what it deliberately does not cover.
- A non-recommendation notice and source principle for numbers and images.

Do not use ceremonial openers such as "N 편 시작합니다".

### 1.3 Body Requirements

The body is organized into as many sections as the topic requires. Section count, order, and names are free.

The body must include, whether as separate sections or integrated prose:

1. Concept definition or boundary-setting so the reader does not misunderstand the subject.
2. Evidence from primary sources, including numbers, charts, slides, or official documents when claims require them.
3. One structural tool, classification, or mechanism that leaves the reader with a way to think about the subject.

Avoid repeated `1.1 / 1.2 / 1.3` style subnumbering in the final article. Use meaningful Korean H3 headings only when they help.

Merge or delete sections that would contain only 2-3 sentences.

### 1.4 Narrative Reflection

The body must reflect `PROMPT.md` §3:

- One central flow.
- Branches that serve the central flow.
- Explicit chain logic between branches.
- Recent events placed as intro hooks or confirmation evidence, not as the entire frame.

### 1.5 Images, Tables, And Citations

- Follow `PROMPT.md` §7 and `SOURCE_POLICY.md`.
- Every quantitative claim needs a primary source URL or explicit uncertainty qualifier.
- Image captions must match the actual image content.
- For rendered slides, direct visual inspection is required before captions.

### 1.6 Closing

The closing should restate the core claim in one or two paragraphs. Do not add fixed rituals such as mandatory three-line summaries, investment maps, target prices, or recommendation language.

If the article is explicitly part of a justified series, mention only the next question in one or two sentences.

### 1.7 Disclaimer And Source List

End with:

- A non-investment-recommendation notice.
- A note that numbers and images follow cited source materials.
- A categorized reference list.

---

## 2. Anti-Repetition Rules

### 2.1 No Prior Skeleton Reuse

If a prior article used a sequence such as definition -> problem -> numbers -> technology -> value chain -> risk -> answer, do not reuse that same sequence by default. If the topic truly requires a similar flow, change the section names and justify the logic through the topic itself.

### 2.2 No Narrative Label Reuse

Do not reuse distinctive narrative labels, frame names, slogans, series labels, or section-title formulas from prior articles.

### 2.3 No Ceremonial Phrases

Do not attach author-series habits or ceremonial endings mechanically. They read as self-plagiarism.

### 2.4 No Repeated Numbering Pattern

Avoid repeating `### 1.1 ...`, `### 1.2 ...`, `### 1.3 ...` style article sections. Use meaning-driven Korean headings or no subheadings.

---

## 3. Single Report By Default

### 3.1 Default

One topic should become one complete report.

Default length: 600-900 lines. Upper range: about 1,200 lines.

Do not split a topic into thin installments just to create a series.

### 3.2 When A Series Is Justified

Use a series only when:

- One article would exceed about 1,500 lines.
- The topic naturally divides into two or three large independent questions.
- Time-staged updates are part of the subject.

### 3.3 Series Limit

Maximum series length: 3 parts. Four or more parts create reader fatigue unless the user explicitly approves a broader project.

### 3.4 Introductory Series Parts

An introductory series part must not use the closing conventions of a complete single report. That would mislead the reader into thinking the analysis is already complete.

---

## 4. Mechanical Formatting Rules

### 4.1 Korean Style

- 100% honorific Korean: `~입니다`, `~합니다`.
- Use thousands separators for numbers.
- Use either `YYYY.MM` or `YYYY 년 MM 월` consistently.
- Keep currency and units consistent.

### 4.2 Citation Example

```markdown
... ([*{{보고서명, YYYY.MM}}*]({{URL}}))
```

All quantitative claims need a primary source URL or explicit uncertainty qualifier. Vague sourcing is not acceptable.

### 4.3 Tables

General industry articles must contain zero financial or valuation metrics. Follow `SOURCE_POLICY.md` and `PROMPT.md` §6.

Allowed company quantitative information in general articles includes market share (%), production capacity, patents, certifications, technical specifications, and similar operational evidence.

### 4.4 Reference List

Group references by category, such as government/regulatory, company technical material, company IR, public education, standards bodies, research institutes, or media context.

Every listed reference needs a URL.

---

## 5. Final Checklist

### 5.1 Structure

- [ ] One-line Korean title and one subtitle; no series number unless explicitly justified.
- [ ] Intro includes recent trigger, article question, scope/limits, and disclaimer.
- [ ] Body includes boundary-setting, evidence, and a structural mechanism.
- [ ] Body flows through central flow -> branches -> chain -> confirmation evidence.
- [ ] Recent events are evidence or hooks, not the whole frame.
- [ ] Zero prior-article references.

### 5.2 Repetition

- [ ] No copied section sequence from prior articles.
- [ ] No reused narrative labels or distinctive prior phrasing.
- [ ] No repeated subnumbering formula.
- [ ] No ceremonial series phrases.

### 5.3 Content Quality

- [ ] General articles contain zero prohibited financial or valuation metrics.
- [ ] Company discussion stays on technology, operations, supply chain, positioning, customers, partnerships, market share, capacity, patents, certification, or maturity.
- [ ] Images normally total 6-12 and follow `PROMPT.md` §7.
- [ ] Zero AI-generated images.
- [ ] Every quantitative claim has a primary source URL or explicit uncertainty qualifier.
- [ ] 600-900 lines is the normal target; under 400 lines usually feels incomplete.
- [ ] No self-invented classification system unless grounded in a cited external standard.

### 5.4 Short Section Cleanup

- [ ] Any 2-3 sentence section is merged or deleted unless it is a necessary transition.

### 5.5 Earnings / IR Article Checklist

For articles based on company earnings releases, conference calls, investor presentations, or IR decks:

- [ ] Required current-quarter and prior-quarter release/deck materials were collected and compared; missing required release/deck materials are a fail/research gate.
- [ ] At least two disappeared prior-quarter highlights were explored, or fewer than two were explicitly stated with wording/order/unit/risk-language checks.
- [ ] Any YoY-to-QoQ or annual-to-sequential wording shift was checked.
- [ ] Any unchanged guidance range with worse assumptions was checked.
- [ ] Repeated cause explanations with changed effect size were checked.
- [ ] The IR narrative frame was named and paired with a counter-reading.
- [ ] A counterbalance section such as "진정으로 강해진 것들" corrects over-criticism.
- [ ] Self-correction did not move only in the direction of optimism.
- [ ] The `## 한눈에 보기` scorecard appears immediately before the intro, with total score, five axes, and 2-4 sentence evaluation.
- [ ] Every rendered slide image was directly inspected before captioning.
- [ ] "single quarter exceeded prior full year" style claims include numerator/denominator verification.

---

This file defines only the minimum floor. Above this floor, article structure should vary by topic.
