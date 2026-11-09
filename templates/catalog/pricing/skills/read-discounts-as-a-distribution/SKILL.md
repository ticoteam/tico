---
name: read-discounts-as-a-distribution
description: Report discounts as a distribution (share discounted, median among discounted deals, spread, share over the written cap) with a noise check against the last period. Name outlier deals, never salespeople.
---
# Read discounts as a distribution

When to use: before you report any discount figure, before you say discounts are "too high" or "getting worse", and before you call a change between two periods a trend.

Steps:
1. Pull closed-won deals for the period: deal ID, close date, the list price on the price list on the close date, the charged price per period, term, segment and deal size band. Annualise multi-year deals and say so.
2. Compute each deal's discount as one minus charged over list. A deal with no discount counts as zero, not as missing.
3. Report the share of deals discounted (discount above zero) and the median discount among those deals. Do not lead with the mean: one large deal moves it.
4. Give the spread: the 25th and 75th percentiles, or the range, with the count.
5. Count the deals over the written cap and list them by deal ID.
6. Split by segment and deal size only where each group has about ten or more deals. Otherwise write "too few deals" for that group.
7. Compare with the last period on the same definition. Each share has a standard error of about the square root of p times (1 minus p) over n. The standard error of the difference is the square root of the two standard errors squared and added. A change under about two of those is not yet a trend; say so.
8. Name outliers by deal ID and the question each raises. Do not name the salesperson. The owner decides whether a deal broke a rule.

Checks before you finish:
- The definition is written down: list price source, charged price basis, annualisation.
- The headline uses the median, and the share discounted is shown with its count.
- Every group shows its deal count.
- A change against the last period is shown with its noise, or marked as not yet a trend.
- No salesperson is named.

Example (invented, for illustration): "September: 14 of 24 closed-won deals discounted (58%), median 12% among those, range 5% to 35%. Six are over the 15% cap, all multi-location, listed by deal ID. August: 9 of 22 (41%). Each share has a standard error of about 10 points, so the 17-point change is about 1.2 standard errors of the difference (about 14.5 points). Not yet a trend."
