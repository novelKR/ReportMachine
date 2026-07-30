# MODE_CONTRACT.md - Analysis Mode Contracts

`article_type` (`industry` / `earnings` / `preview`) selects the output shape. `analysis_modes`
selects what content is permitted inside that shape. A run may combine modes, for example
`market_reaction + company_fundamental`.

Only the approved modes recorded in the run's intent contract are active. Content that no active
mode permits is out of scope (`INT-05`).

## Mode Matrix

| Mode | Permits | Forbids |
|---|---|---|
| `industry_structure` | Technology, supply chain, competitive structure, standards, policy | Company financial metrics, market-reaction narration |
| `company_fundamental` | Production, customers, products, partners, capacity, certification, business structure | Valuation, share-price interpretation, earnings-quality scoring |
| `market_reaction` | Observed share-price and sector moves as facts, drawdown from a high, relative moves around a disclosure, conditional reading of what the market repriced | Target prices, buy/sell/hold, valuation conclusions, price forecasts |
| `event_analysis` | Cause and propagation of a specific disclosure, regulation, or incident | Speculative causal chains without evidence |
| `earnings_quality` | Revenue, profit, margin, EPS, orders, backlog, cash flow, guidance, segment metrics from company materials or cited transcripts | Valuation multiples used as investment judgment, recommendations |
| `technology_validation` | Distance between a technical announcement and real mass production, yield, or qualification | Treating announcements or samples as proven volume adoption |

## market_reaction Details

This mode exists so that a factual market question is not silently converted into a
fundamentals-only article.

Permitted, when sourced:

- Actual price or index moves over a stated window.
- Drawdown relative to a stated high.
- Relative performance between named peers around a disclosure date.
- Conditional interpretation of which expectation the market appears to have repriced, marked as
  interpretation rather than fact.

Forbidden in every mode, without exception: target price, buy/sell/hold, undervalued/overvalued
conclusions, valuation-multiple investment judgment, price forecasts, portfolio allocation
(`SAFE-01`).

Technical-analysis pattern reading (support lines, chart formations, entry timing) stays out of
scope unless the user explicitly approved it in the intent contract.

## Mode And Article Type Interaction

- `industry` articles may activate `industry_structure`, `company_fundamental`, `market_reaction`,
  `event_analysis`, and `technology_validation`. The general finance-metric ban in `PROMPT.md` §6.1
  still applies except where `market_reaction` explicitly permits observed price facts.
- `earnings` articles always include `earnings_quality` and follow `EARNINGS_PROMPT.md`.
- `preview` articles require `prompts/PREVIEW_PROMPT.md`; if it is absent the run must fail.

## Scope Leakage

If evidence pulls the analysis toward a mode that is not approved, do not quietly expand. Record it
as a scope gap for the user and keep the article inside the approved modes.
