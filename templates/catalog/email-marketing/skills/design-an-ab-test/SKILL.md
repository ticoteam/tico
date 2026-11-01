---
name: "design-an-ab-test"
description: "Design an email A/B test before it is sent: one variable, a click or reply metric, the smallest lift worth finding, the sample size from the formula and a stated stop rule. Say plainly when the list cannot power the test."
---
# Design an A/B test

When to use: a subject line, preview line, content block, offer or send time is to be tested on an email.

Steps:
1. Write the hypothesis in one sentence: which variable changes, in which direction, and why.
2. Pick the primary metric: clicks or replies, never opens alone. Name the guardrails: unsubscribes and complaints.
3. Set the smallest lift worth finding, as an absolute difference in points, and say why that size matters.
4. Compute the sample per variant with n = 16 x p x (1 - p) / d squared, where p is the baseline rate and d the absolute
   difference. Take the baseline from the last three comparable sends. Write the arithmetic in the test note.
5. Compare with the list. The slice divided by the number of versions must reach n (slice / versions >= n, the same as
   slice >= versions x n). If it cannot, say so in one line and propose
   a larger change, or a bigger list.
6. State the stop rule before the send: the send window, the fixed size, and no stopping early because one version leads.
7. When the window closes, write the result in `knowledge/results.md`: the winner, the margin, and the guardrails.

Checks before you finish:
- One variable only, and the hypothesis names it.
- The primary metric is not opens.
- The sample arithmetic is written out, with the baseline it used.
- The test note says what happens if the list cannot reach the sample.

Example (generic): "Subject test on a list of 40,000, baseline click rate 3 percent, smallest lift worth finding 0.6 points.
n = 16 x 0.03 x 0.97 / 0.006 squared, about 12,900 per version, so about 25,800 in total. A 10,000 slice split two ways gives
5,000 per version, so the test cannot be powered. Proposed instead: one larger change between two offers, to the full
list. Recorded in the test note."
