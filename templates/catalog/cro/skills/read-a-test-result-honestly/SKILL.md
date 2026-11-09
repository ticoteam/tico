---
name: read-a-test-result-honestly
description: Read a finished or running site test against only its own written stop rule, and report a win, a loss, a guardrail failure or "no clear difference" with the sample, the window and the segment behind it.
---
# Read a test result honestly

When to use: when a test's sample is reached or its end date has passed, when a guardrail breaks, or when someone asks
whether a change worked.

Steps:
1. Open the test's entry in `knowledge/tests.md`. Read the hypothesis, the primary metric, the guardrails, the sample and
   the stop rule as they were written. Do not read the result against a rule made later.
2. If the sample is not reached and the end date has not passed, report "running, sample x of y" and stop. Do not read a
   difference from a partial sample.
3. Compare the two variants on the primary metric: visitors, completions, and the difference with its interval, at the
   confidence level in `knowledge/conversion-method.md`.
4. Check the guardrails. A signup lift with fewer paid conversions, more errors or a slower page is a guardrail loss, and
   the test is not a win.
5. Check the weeks and the segments. If the lift sits in one week or one segment, say so, and say it may be novelty or a
   change in the traffic mix.
6. Decide in words: "ship", "revert", "no clear difference: run again with a larger change", or "stopped by a guardrail".
   Record the decision and its date in `knowledge/tests.md`.
7. Record "no clear difference" with the same care as a win. A failed test is a result the team paid to learn.

Checks before you finish:
- The sample and the end date are quoted with the result.
- A test stopped early names the guardrail that stopped it.
- A result from a different page, or from a changed traffic source, is not read as this test.
- The word "winner" appears only when steps 2 to 5 all pass.

Example (invented, for illustration): "Test pricing-trial-button, sample reached 2026-11-06 (4,912 visitors per variant).
Trial starts 25.1 percent on control and 27.9 percent on the variant, a difference of 2.8 points with an interval that
does not cross zero. Guardrail: paid conversions 1.9 percent lower, inside the noise. The lift shows in both weeks.
Decision: ship, with paid conversions watched for two more weeks."
