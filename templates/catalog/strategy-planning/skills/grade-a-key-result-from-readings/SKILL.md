---
name: grade-a-key-result-from-readings
description: Grade a key result from 0 to 1 as the fraction of the way from its baseline to its target, using the final dated reading, and mark it ungraded when no reading exists.
---
# Grade a key result from readings

When to use: when grading last quarter, or at a mid-quarter check-in that needs a grade.

Steps:
1. Read the final reading with `hub kpi show <kpi id>`, with its date. Check that the date falls inside the quarter.
2. Compute the fraction: (reading minus baseline) divided by (target minus baseline), to one decimal. This also works when the target is below the baseline. Then clamp the fraction to 0 to 1: above 1 is 1.0, below 0 is 0. A binary result is 0 or 1.
3. With no reading in the quarter, write "ungraded" and name what was missing. Never write 0 and never write 1.
4. Mark the result committed or aspirational. A committed result under 1.0 gets one sentence on why.
5. Average the graded results only, and say how many were left out.
6. Write the grade and its evidence (the reading and its date) into `knowledge/scorecard.md`. Never edit an older grade;
   add a correction line with the date.

Checks before you finish:
- Every grade cites a dated reading, or says "ungraded" with the gap.
- The total averages graded results only, and says how many were left out.
- No older grade was edited.

Example (generic, invented): Baseline 18 studios, target 30, final reading 26 on 2026-08-29. The grade is (26 - 18) divided by
(30 - 18), which is 0.7, marked aspirational. A waitlist result with no reading is "ungraded", and the total says one result
was left out.
