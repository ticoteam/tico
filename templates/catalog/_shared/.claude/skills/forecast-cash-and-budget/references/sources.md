# Sources

All text in this skill is original. Ideas were drawn from the sources below. Read 2026-11-18 unless dated otherwise.

## Adapted ideas (Apache-2.0 and MIT)

- Anthropic, `knowledge-work-plugins`, small-business plugin, `cash-flow-snapshot` (Apache-2.0).
  https://github.com/anthropics/knowledge-work-plugins
  Taken: confirm the opening balance first, because "will we make payroll" is a balance question; low and high
  cases; the largest risks named in money. Changed: the source bands the net change by a percentage, which puts
  "low" above "high" when the net is negative (a net of -100 with a 20% band gives low -80 and high -120). This skill
  flexes receipts and payments separately and carries each case's closing cash forward.
- Anthropic, same repository, finance plugin, `variance-analysis` (Apache-2.0).
  Taken: commentary in four parts (headline, driver, outlook, action); drivers must add up to the difference; a
  different percentage test for budget, forecast and prior period.
- `mohitagw15856/pm-claude-skills`, `cash-flow-forecast` (MIT).
  https://github.com/mohitagw15856/pm-claude-skills
  Taken: a 13-week forecast rolled weekly with a lowest-cash read-out and a "levers if cash is tight" block listing
  trade-offs; never invent a starting balance or an amount.

## Essays and guides (ideas only)

- Paul Graham, "Default Alive or Default Dead?", October 2015. https://paulgraham.com/aord.html
  Taken: the test itself: with costs flat and recent growth continuing, does the company reach profit on the cash it
  has. The month-by-month table and the limits listed in `driver-budget.md` are this skill's own.
- Elena Kiristova, "Best Practices in Rolling Forecasts", FP&A Trends Group, April 2018.
  https://fpa-trends.com/article/best-practices-rolling-forecasts
  Taken: re-forecast monthly or quarterly over a horizon that runs past the year end; driver-based, not a line-by-line
  rebuild; large projects kept separate; forecasts kept independent of performance targets.

## Tico

- `docs/finance-tools.md`, `docs/bookkeeper.md`, `backend/finance/core/kinds.py` and the Finance Owner's
  `playbooks/weekly-cash-summary.md`, `playbooks/cash-outlook-13-weeks.md` and `playbooks/usage-against-budget.md`
  on `origin/main`, 2026-11-18: `finance/pay-from`, the `cash`, `burn` and `runway_months` metrics, the reports,
  budgets on P&L lines with warn, ask, decline and hold-payment limits, `budget_create`, `budget_update`,
  `allocation_set`, `projection_set`, the daily Watch (runway under 6 months), `knowledge/cash-thresholds.md` and
  `knowledge/cash-forecast.md`.
- Catalog skills folded in: `finance-owner/build-the-13-week-cash-forecast` (direct method, 8 to 12 lines, weeks 1
  to 4 from documents, the 10% miss rule, the miss log), `finance-owner/write-the-close-variance-note` (two-part
  limit, about 120 words, "unexplained" not "timing").
- Catalog skills used as sub-steps: `fpa-analyst/check-a-spend-spike-before-alarm`,
  `finance-owner/reconcile-a-cash-figure`.
- Sibling skills in this library: `build-the-pnl` (unit costs, driver split), `close-the-month`,
  `collect-receivables`, `pay-bills-and-buy`, `stay-on-top-of-taxes`, `measure-unit-economics`,
  `set-and-test-prices`.

## This skill's own defaults (not from a source)

- The working floor of four weeks of cash out when no minimum is set; the default flex sizes for the low and high
  cases; the 5% reserve; the re-plan triggers; "four periods running" as the sign of a leaning forecast.
- The percentage tests in `variance-commentary.md` follow the shape of Anthropic's `variance-analysis`; the money
  floor of 1% of revenue is from `build-the-pnl`.

## Not found, not verified

- No skill or guide in the research linked drivers to both the P&L and cash, or covered building a budget from
  actuals with an approval step. Sections 2 to 4 of `driver-budget.md` are a working method.
- No source gave tested band widths for a cash forecast; the defaults are replaced by the company's own misses
  after eight weeks.
- Loan covenants, credit-line terms and tax payment dates are company-specific and are read from the documents.
