# Sources

All text in this skill is original. Ideas were drawn from the sources below. Read 2026-11-18 unless dated otherwise.

## Adapted ideas (MIT)

- coreyhaines31, `marketingskills`, `attribution` skill (MIT). https://github.com/coreyhaines31/marketingskills
  Taken: each data source leans a known way; platform claims are never added together; one system of record for the
  customer count; customers' own answers are a second view, not proof; a test settles a disagreement; any override of
  the rule is per channel, written down and dated.

## Ideas only, no wording

- borghei, `Claude-Skills`, `saas-metrics-coach` (Commons Clause), known through the project's research notes, not
  opened for this skill: payback stated in months and graded. Its grade bands appear in `benchmarks.md` only as one
  side of a conflict and are not used.
- Dean Peters' SaaS economics skills (CC BY-NC-SA), known through the project's `inventory.md` only, not opened for
  this skill. Nothing was taken beyond what the inventory lists.
- The project's `inventory.md`, section 3, "Steal" list: raw signup and activity records in, retention matrix out;
  LTV on gross margin; payback in months; one consistent attribution model beats the "right" one; customers' own
  answers support but do not prove; holdouts settle disputes.

## Guides and data (facts and ideas, no wording beyond short quoted phrases)

- David Skok, "SaaS Metrics 2.0", For Entrepreneurs, about 2013, updated May 2016.
  https://www.forentrepreneurs.com/saas-metrics-2/
  Taken: the LTV to CAC and months-to-recover rules of thumb with their age; cohorts by join month, by customers and
  by revenue; the net revenue churn warning level.
- SaaS Capital, "2025 B2B SaaS Retention Benchmarks", published 2025-09-18.
  https://www.saas-capital.com/research/saas-retention-benchmarks-for-private-b2b-companies/
  Taken: the twelve-month cohort definitions of NRR and GRR; the 2025 medians.
- Kyle Poyar and High Alpha, "2025 SaaS Benchmarks Report", Growth Unhinged, 2025-11-12.
  https://www.growthunhinged.com/p/2025-saas-benchmarks-report
  Taken: lead with payback and NRR ahead of LTV to CAC.
- Benchmarkit, 2025 B2B SaaS performance benchmarks. Read through secondary summaries in search results only; the
  report itself was not opened. Taken: the 2025 payback medians, marked secondary and conflicting.
- Trade coverage of Google Ads and Meta lift testing, late 2025 to 2026, read through search results only. Taken: the
  reported minimums and typical test lengths, marked unconfirmed in `attribution-and-holdouts.md`.

## Tico

- `docs/growth.md`, `docs/goals-and-kpis.md`, `docs/finance-tools.md`, `docs/databases.md`, `docs/pages.md`,
  `docs/tasks.md` on `origin/main`, 2026-11-18: how Growth computes CAC, expected months, LTV and payback; the 60-month
  cap; observed LTV as net revenue; direct and loaded CAC; the minimum cohort; which rule named a channel; source
  health; KPI readings and proposals.
- Catalog skills folded in as rules: `segment-marketing-owner/read-cost-per-customer-for-a-segment` (direct for
  payback, loaded to compare), `growth-manager/check-attribution-before-a-claim`,
  `feature-owner/read-an-area-retention-cohort`, `revenue-accountant/bridge-recurring-revenue`.
- Sibling skill in this library: `finance/build-the-pnl`, the source of gross margin and cost of revenue.

## Not checked

- The three holdout triggers, the 25% Unknown threshold, the 10% largest-customer rule and the 2% reconciliation
  tolerance are this skill's defaults. They come from judgment, not from a published standard, and the owner may
  change them in `knowledge/unit-economics.md`.
- No benchmark was found for businesses other than subscription software sold to businesses.
