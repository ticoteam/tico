# Cohort retention matrices

Two tables, built from the same rows: one row per customer per month with customer key, first paid month, and net
revenue in that month. Raw payment and activity records go in; the tables come out. Nobody types a retention rate.

## Build

1. **Assign each customer to a cohort**: the month of their first successful payment. Not the signup month, unless the
   table is about trial conversion.
2. **Give each row an age**: months since the cohort month, plus one. The first paid month is age 1.
3. **Decide "still paying" from the saved rule** in `knowledge/unit-economics.md`. Subscription: the subscription was
   active on the last day of the month. No subscription: the customer placed a paid order within the churn window
   (90 days by default, or the segment's `churn_days`).
4. **Customer retention cell** = customers of the cohort still paying at that age / customers the cohort started with.
5. **Revenue retention cell** = net revenue of the cohort at that age / net revenue of the cohort at age 1.
6. **Keep the counts.** Each cell holds its top and bottom numbers, so a reader can open any percentage.

Tico Growth already keeps cohorts by channel, segment and plan: `tico growth cohort list --by channel --grain month`
(add `--customers` for counts, `--json` to work with it). Use it to check the tables, and build from the rows when
revenue or margin is needed, because Growth's cohort read is about customers.

## Layout

Rows are cohorts, oldest at the top. Columns are ages. The table is a triangle: newer cohorts have fewer columns.

Illustration (invented), customer retention:

| Cohort | Start | 1 | 2 | 3 | 4 | 5 | 6 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 2026-04 | 200 | 100% | 70% | 60% | 55% | 52% | 50% |
| 2026-05 | 215 | 100% | 72% | 61% | 57% | 53% | not yet |
| 2026-06 | 190 | 100% | 69% | 60% | 54% | not yet | not yet |
| 2026-07 | 24 | 24 | 17 | 14 | not yet | not yet | not yet |

The July row has under 30 customers, so it shows counts, not rates.

On the page, shade each cell by its value on a single colour, light to dark, with the number always printed in the
cell. Do not rely on colour alone. "Not yet" cells are empty of colour.

## Read

Read it three ways, and write one sentence for each on the page.

- **Across a row: the shape of the curve.** Does it flatten? A curve that settles at a floor means there is a core of
  customers who stay, and LTV is finite and knowable. A curve that keeps falling toward zero means every customer
  eventually leaves and growth only replaces losses. Where the steepest drop sits says where to work: month 1 to 2 is
  about the first experience; a drop at month 12 is about renewal.
- **Down a column: are newer cohorts better or worse at the same age?** This is the fairest test of whether the
  product, the onboarding or the mix of customers changed. Compare the same age only; never a 6-month-old cohort with
  a 2-month-old one.
- **Along a diagonal: one calendar month across every cohort.** A dip in every cohort in the same calendar month is an
  event (a price change, an outage, a season), not a cohort quality problem.

Then read the two tables against each other:

- Customer retention falling while revenue retention holds: the customers who stay are paying more. Healthy if it is
  upgrades, fragile if it is one large account. Check the largest customers.
- Revenue retention falling faster than customer retention: customers are staying and downgrading or ordering less.
  That is a pricing or value problem, not a churn problem.
- Revenue retention above 100% at later ages: expansion outruns loss. Confirm it holds without the top customer.

## Cuts

Cut the same tables by channel, by segment and by plan, one cut at a time. A cut is only shown where each cohort in
it meets the minimum size. The most useful cut is by channel, because it turns "this channel has a low CAC" into
"this channel has a low CAC and its customers leave in two months".

## Mistakes to avoid

- **A partial month as a column.** The current month is not finished. Leave it out.
- **Counting returned customers as never having left.** A customer who churned and came back is a reactivation. Keep
  them in their original cohort and show reactivations as their own line, so the curve does not rise without a reason.
- **Dividing by current customers.** The bottom number is always the cohort's starting count.
- **Averaging rates across cohorts.** To combine cohorts, add the tops and add the bottoms, then divide.
- **Monthly grain for a weekly business.** If most customers buy weekly, use weekly cohorts for the first twelve weeks
  (`--grain week`), then monthly.
- **Mixing billing periods.** Annual customers cannot leave until month 13. Show annual and monthly plans as separate
  tables, or the blended curve looks better than either.
- **Seasonal cohorts read as a trend.** Compare a cohort with the same month a year earlier before calling a change.
- **Free users in a paying cohort.** A cohort starts at first payment. Trial and free activity belongs to the funnel
  (`skills/find-the-leak-in-a-funnel`), not to this table.
