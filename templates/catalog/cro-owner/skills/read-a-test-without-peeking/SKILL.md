---
name: read-a-test-without-peeking
description: Read an A/B test once, at its planned sample and end, with a sample ratio check, a confidence interval and a written call of won, lost or inconclusive. Never state a winner from fewer people than planned.
---
# Read a test without peeking

When to use: before you state any test's result, call it winning or losing, or report its primary metric.

Steps:
1. Check that the test has reached its planned n per arm and its planned end. If not, the read is "not yet", with the counts only.
2. Run the sample ratio check: chi-square with 1 degree of freedom on the two arm counts against the planned split (an even
   split unless the brief says otherwise). If p is below 0.001, stop and investigate how users were assigned before reading
   anything else.
3. Check each guardrail against the limit in the brief.
4. Compute each arm's rate with its count. Difference = p_t − p_c.
5. Interval = difference ± 1.96 × sqrt(p_c(1 − p_c) ÷ n_c + p_t(1 − p_t) ÷ n_t).
6. Decide. Won: the whole interval is above zero and the guardrails hold. Lost: the whole interval is below zero. Anything
   else is inconclusive, and inconclusive is a result.
7. Write the read with the decision, the counts, the window and the interval. Name what the test did not decide, and what
   size of test would, or say the page is too small for it.

Checks before you finish:
- Nothing about the primary metric was read before the planned end.
- The sample ratio check is written, with its p value.
- The interval, not the point difference, decides the call.
- No winner is written from fewer people than the brief planned.

Example (invented numbers, for illustration only): control 1,001 of 4,004 (25.0%), variant 1,062 of 4,010 (26.5%). Difference
+1.5 points, 95% interval −0.4 to +3.4 points. Sample ratio check p about 0.95. Inconclusive, because the interval includes zero.
