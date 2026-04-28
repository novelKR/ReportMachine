# SOURCE_POLICY.md

This file resolves ReportMachine source, finance-metric, and citation policy for Codex automation.

## General Article Rule

General industry, technology, supply-chain, regulation, and market-structure articles follow `PROMPT.md` section 6.1:

- Do not use revenue, operating income, net income, EPS, ROE, PER, PBR, EV/EBITDA, target price, dividend yield, market cap, share-price movement, order backlog amount, undervalued/overvalued, buy/sell, or recommendation language.
- Keep company discussion focused on technology, operations, positioning, supply chain, production capacity, market share, patents, R&D direction, customers, partnerships, and maturity.

## Earnings Article Exception

For published earnings, IR deck, and conference-call articles, `EARNINGS_PROMPT.md` overrides the general finance-metric ban only where metrics are necessary to evaluate earnings quality.

Allowed for earnings analysis:

- Revenue, profit, margin, EPS, order, backlog, cash flow, guidance, growth rate, consensus beat or miss, and segment metrics when sourced from company materials, filings, or cited transcripts.
- Five-axis scorecard metrics required by `EARNINGS_PROMPT.md`.

Still prohibited in every article type:

- Target price.
- Buy, sell, hold, or recommendation language.
- Undervalued or overvalued conclusions.
- PER, PBR, EV/EBITDA, or similar valuation-centered investment judgment.
- Share-price forecast.
- Portfolio allocation instruction.

## Citation Rule

- Every quantitative claim must include a primary source URL or an explicit uncertainty qualifier.
- Vague sourcing such as "industry reports say" is not acceptable.
- If a source is secondary, mark it as secondary and explain why no primary source is available.
- Do not invent URLs, filings, transcripts, or metrics.

## Image Rule

- Images must come from public official sources, public-license sources, or user-provided files.
- AI-generated images are prohibited in article outputs.
- Author-created charts are prohibited unless the prompt explicitly allows a rare supply-chain or value-chain schematic exception.
- Rendered slides must be inspected directly before captions are written.
