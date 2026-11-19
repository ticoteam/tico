# Sources

All text in this skill is original. Ideas were drawn from the sources below. Read 2026-11-18 unless dated otherwise.

## Adapted ideas (Apache-2.0 or MIT)

- Anthropic, `knowledge-work-plugins`, finance plugin, `financial-statements` and `variance-analysis` skills
  (Apache-2.0). https://github.com/anthropics/knowledge-work-plugins
  Taken: a fixed list of variance drivers; drivers must sum to the total; non-GAAP figures are labelled and
  reconciled to GAAP.
- mohitagw15856, `pm-claude-skills`, `bookkeeping-categorization` (MIT).
  https://github.com/mohitagw15856/pm-claude-skills
  Taken: settle categorization edge cases before a P&L is trusted.

## Guides (ideas and facts, no wording)

- SaaS Capital, "COGS for SaaS", dated 2024-10-03.
  https://www.saas-capital.com/blog/cogs-for-saas-business/
  Taken: what belongs in cost of revenue and what does not; customer success is split by whether the work is retention
  or selling; services reported apart from subscription; the 80 to 85 percent licence gross margin figure (their own
  portfolio and survey).
- ICONIQ, "2026 State of AI" survey, read through secondary coverage on 2026-11-18 (the report itself was not opened).
  https://www.iconiq.com/growth/insights/state-of-ai-2026
  Taken: the roughly 52 percent average AI product gross margin, quoted as survey answers and marked unverified.

## Tico

- `docs/finance-tools.md`, `docs/bookkeeper.md`, `docs/usage.md`, `docs/pages.md`, `docs/tasks.md` on `origin/main`,
  2026-11-18: report kinds, metrics, the default chart (AI services under Cost of revenue), views and `excl` totals.
- Catalog skills folded in: `controller/explain-a-variance` (drivers matched to documents; timing, one-off, run-rate;
  a named residual).
- Catalog skills used as sub-steps: `finance-owner/categorize-a-feed-row`, `revenue-accountant/bridge-recurring-revenue`,
  `revenue-accountant/reconcile-billing-to-the-books`, `controller/review-the-books-for-unusual-entries`.

## Not checked

- No primary accounting standard was read for the GAAP caption table. It follows common US practice for private
  software companies. A CPA confirms it before statements go to an outside reader.
