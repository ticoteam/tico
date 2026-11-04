---
name: "define-a-metric"
description: "Write or check one metric line for the investor update: its name, formula, source, period, inclusion rules, owner, order and the baseline month, so the number means the same thing every month."
---
# Define a metric

When to use: when a metric is added to `knowledge/metrics.md`, when a reader asks what a number means, or when a source changes. Investors compare the
same line month to month; a silent change of definition looks like growth or decline and erodes trust.

Steps:
1. Name the metric the way the team says it, with its unit: "Monthly recurring revenue (USD)", "Paying studios (count)".
2. Write the formula in one line: what is counted, what is excluded, and over which period. Example: "MRR: sum of monthly subscription fees
   for accounts active on the last day of the month. Excludes one-off fees, trials and services."
3. Name the source: the system, report or KPI id (`hub kpi show <kpi id>`), and the date of the reading. A metric with no source is not added.
4. Name the owner who supplies it, and the day of the month it is due.
5. Set the order: where it sits in the table. Keep it fixed. A new metric goes at the end unless the owner asks otherwise, and is shown as new for one month.
6. Record the baseline: the value for the prior month, from the same source, so the first comparison is like for like. If it cannot be rebuilt, say so and
   start the comparison the next month.
7. Note any restatement: a figure corrected after publication is shown with its old and new value and the reason, in the next update.

Checks before you finish:
- The formula can be recomputed by a person from the source, with no judgement call.
- The source and its reading date are on the line.
- No definition is changed in an update. A proposed change goes to the task with its reason.

Example (invented, for illustration): "Net revenue retention (trailing 12 months, %): revenue this month from the customers who paid 12 months ago, divided by
what those same customers paid 12 months ago. Expansion counted, new customers excluded. Source: billing export, read on the 2nd. Owner: the owner. Order: 4.
Baseline: 103% for July, rebuilt from the same export."
