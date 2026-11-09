---
name: reconcile-headcount-and-gross
description: Reconcile a pay run's expected headcount and gross pay against the last register by pay group. Headcount is last run plus joiners minus leavers; gross moves only by the listed changes. Name any difference that is left.
---
# Reconcile headcount and gross

When to use: once the changes for a run are checked, before the pay summary is written, and after a register comes back.

Steps:
1. Take last run's headcount and gross by pay group from `knowledge/baseline.md`, with its register date.
2. Add the joiners and subtract the leavers for each pay group, using only the changes that passed `skills/check-a-pay-change-against-its-source/SKILL.md`.
   The result is the expected headcount for the run.
3. Take gross pay. Add each listed change to the baseline gross, pro-rated where its period is partial, and show the method used.
4. Compare the expected figures with the roster and the last register. Name each difference, its size and its likely cause.
5. A difference with no listed cause is not hidden in a total. It is a question for the payroll owner, with the group and the amount.
6. Write the expected figures into the summary with the basis: "baseline register 2026-09-30, plus 2 joiners, minus 1 leaver".

Checks before you finish:
- Headcount in each group equals baseline plus joiners minus leavers.
- Gross moves only by listed, sourced changes.
- Every difference is named with its group and amount.
- The baseline is not updated until the post-run check passes.

Example (invented, for illustration): "Hourly group: baseline 14 people, 21,400 gross (register 2026-09-30). Plus 1 joiner (start 10-06), minus 0 leavers. Expected 15 people, 21,400 + 1,920 (pro-rated, 8 of 14 days) = 23,320. Roster shows 15. Reconciled."
