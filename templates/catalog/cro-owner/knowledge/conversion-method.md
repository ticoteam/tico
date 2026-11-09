# Conversion method

The standing rules behind `playbooks/weekly-conversion-check.md`, `playbooks/run-an-experiment.md` and the three skills
in `skills/`. `funnel.md` holds the counts, `pages.md` the pages and the never-test list. This file holds how a rate, a
leak and a test result are computed, so each run does not re-derive them.

## Rates

- A rate is the people who did step B in a window divided by the people who did step A in the same window, for the same
  segment, from the same export. Both counts come from one export. Never mix a count from one source with one from another.
- A step with no count is "not measured". It is never zero, and it is never dropped from the ranking without saying so.
- Write every rate with its count, window and source: `pricing → checkout: 470 of 1,880, 2 to 8 Nov, export pulled 9 Nov`.
- A later step above an earlier one, or a step with zero events on a day that had traffic, is a tracking question first.

## Leaks

- People lost = people entering the step this week × (the step's best weekly rate in the last 13 weeks − this week's
  rate). Zero when this week is at or above that best.
- Each step is measured against its own best. A large top-of-funnel step does not outrank a smaller one by volume alone
  when its rate is close to its own best.
- A leak is named only when the gap is larger than the noise: 1.96 × sqrt(p × (1 − p) ÷ n), with p this week's rate and n
  its count entering the step. A gap inside the noise is listed as "within noise".
- Segment leaks are ranked on their own lines and are never added to the all-visitor total.

## Sample size and run time

- Per arm, for a two-sided 5% test with 80% power:
  n = 7.849 × [p1(1 − p1) + p2(1 − p2)] ÷ (p2 − p1)². The constant is (1.96 + 0.8416)².
- p1 is the control rate from a clean baseline window, with its count and dates in the brief. p2 is p1 plus the smallest
  change worth shipping, written in points.
- Run time in weeks = ceil(2 × n ÷ eligible entrants per week), at least 2 and at most 6. Eligible entrants are the
  people who would see the test after the brief's exclusions, averaged over the last four full weeks.
- If the run needs more than six weeks, the page is too small for that change. Say so, and propose a bigger change or a
  different page. Do not run a test longer than six weeks.

## Reading a result

- A test is read once, at its planned end: each arm has at least its planned n and the planned run time has passed.
  Nothing about the primary metric is read before then. Guardrails are watched during the run; a guardrail breach goes to
  the page owner as a question at once.
- Sample ratio mismatch: chi-square with 1 degree of freedom on the arm counts against the planned split. If p is below
  0.001, stop and investigate assignment before any result is read.
- Difference = p_t − p_c. Interval = difference ± 1.96 × sqrt(p_c(1 − p_c) ÷ n_c + p_t(1 − p_t) ÷ n_t).
- Won: the whole interval is above zero and the guardrails hold. Lost: the whole interval is below zero. Anything else is
  inconclusive. Inconclusive is a result, and it is recorded as one.
- A winner from fewer people than planned is not a winner, whatever the point estimate says.

## Sources

- Ron Kohavi, Diane Tang and Ya Xu, "Trustworthy Online Controlled Experiments" (2020). Cited for fixed sample sizes and
  the cost of early stopping. Not re-read for this file; check the book before quoting a section.
- Evan Miller, "How Not To Run an A/B Test" (2010). Cited for the peeking problem. Not re-read for this file; check the
  post before quoting it.
- 2026-11-06: the formulas above are standard two-proportion results, written out with their constants at setup. The
  sources above were not re-read for this file.
