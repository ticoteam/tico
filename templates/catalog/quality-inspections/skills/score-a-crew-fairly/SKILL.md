---
name: score-a-crew-fairly
description: Compute a crew's pass rate and re-do rate from a random sample of jobs, with the job count and the range shown, and decide whether the score is reliable enough to report or rank.
---
# Score a crew fairly

When to use: before you publish any crew score, rate, rank or trend, in the weekly page or a note to the Operations Manager.

Steps:
1. Pick the window: the last 8 weeks, rolling. Use the same window for every crew.
2. Count the inspections in the window, split into random checks and complaint checks. Score on random checks. Report complaint checks as a separate line for the same crew.
3. Count the passes and the fails on random checks. Pass rate is passes divided by inspections.
4. Count the re-dos in the window and the jobs they came from. Re-do rate is re-dos divided by jobs completed, using the same denominator for every crew.
5. Compute the 95 percent range for the pass rate with a Wilson score interval. Show the range beside the rate, every time.
6. Check reliability: fewer than 10 jobs means the score is shown as not yet reliable and the crew is not ranked. Say how many more jobs are needed before the score is useful.
7. Compare with the other crews only on reliable scores, and only with their ranges in view. Two crews whose ranges overlap are not different on this evidence.
8. Note any item that drives a crew's fails. If the item fails in other crews too, it is a system cause (see `skills/sort-failures-by-cause/SKILL.md`), not a crew finding.

Checks before you finish:
- Every score shows its job count and range.
- No crew is ranked with fewer than 10 jobs.
- Complaint checks are not in the random-sample rate.
- The window and the denominators are written on the page.
- Nothing says a crew is "failing" or "good" without the numbers behind it.

Example (generic): "Crew C, 8 random checks in the window: 6 passed, 75 percent, range about 41 to 93. Under 10 jobs: not yet reliable and not ranked. Two re-dos, from 8 jobs: 25 percent. Next four weeks decide whether this is a trend."
