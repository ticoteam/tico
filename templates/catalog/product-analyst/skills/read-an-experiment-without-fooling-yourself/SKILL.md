---
name: read-an-experiment-without-fooling-yourself
description: Read an A/B test or staged rollout in the order that prevents false wins. Plan first, then the sample ratio check, the planned end, the planned metric with its interval, and guardrails. Give one of four verdicts.
---
# Read an experiment without fooling yourself

When to use: before any readout that says a test worked, failed, or can be decided. Use it on every experiment, including ones that look obvious.

Steps:
1. Find the plan: the metric chosen before launch, the variants, the planned sample size or end date, and the guardrails. If there was no plan, say so in the first line: the readout is exploratory and cannot name a winner.
2. Check the split. Count assignments per variant and compare them with the planned split, using a chi-square test on the counts. Many teams alarm at very small p-values, around 0.001; use the team's own level if it has one. A mismatch that chance is unlikely to produce means assignment is broken. Stop there and report it. Do not read the metric.
3. Check the end. Has the planned sample or duration been reached? If not, the verdict is "not yet readable", with the date it will be. If anyone looked at the result early, say so. Use a sequential method only if the team's tool states one.
4. Compute the planned metric for each variant: its value, the difference between them, and the 95 percent interval on the difference.
5. Show the guardrails beside it (errors, cancellations, support tickets) with their intervals.
6. Segment cuts only when they were planned. An unplanned cut is a lead to test, labelled as one.
7. Give one verdict: better, worse, no detectable difference, or not yet readable. Add "not safe to decide" when step 2 failed or the metric changed after launch. Say what a person needs to decide. Declaring a winner is theirs.

Checks before you finish:
- The plan was found, or the readout says it is exploratory.
- The split check is written down with its counts.
- The difference carries its interval, not only a point estimate.
- No winner comes from an unplanned segment or a guardrail-only look.
- A metric changed after launch is escalated, not quietly reported.

Example (invented, for illustration): "Checkout test, planned 20,000 per arm with a 14-day end. Day 9: 10,082 in A and 9,918 in B. Split check passes (chi-square p about 0.25). Conversion 4.1% in A and 4.5% in B: a difference of 0.4 points, 95% interval about -0.2 to +1.0. Verdict: not yet readable, due on day 14."
