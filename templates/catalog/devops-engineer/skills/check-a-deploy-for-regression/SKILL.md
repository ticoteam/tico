---
name: check-a-deploy-for-regression
description: Decide whether a deploy made a service slower or more error-prone, by comparing the same hours before and after against a stated threshold, and naming a code change only when one is confirmed.
---
# Check a deploy for regression

When to use: after a deploy, when someone asks whether a release caused a slowdown or errors, or in a daily performance check.

Steps:
1. Fix the window before you look. Set the deploy time, the hours after it (for example the first 24 hours), and the
   comparison set: the same hours on the same weekdays for the four weeks before. A comparison against yesterday misses
   weekly patterns.
2. Pick the measures: p95 latency, error rate (errors per request), resource use (CPU or memory as a share of its limit),
   and request rate. Write the thresholds in `knowledge/thresholds.md` before you look. A starting default is: p95 up by
   half for at least an hour, error rate up three times, or resource use above 80 percent for at least an hour. Tune it
   per service.
3. Check the request rate first. A quiet period can move a percentile a long way. If the request rate moved by more than
   10 percent against the comparison set, say the comparison is weak, and show both numbers.
4. Compare the same hours. Record the baseline (the median of the comparison set) and the after value. One spike is not
   a regression.
5. Name the change. A deploy time is not a code change. Write "restart" unless the release record confirms a merged
   commit or a new image for this deploy. When a change is confirmed, name the commit.
6. Write the verdict as regression, no regression, or inconclusive, with the numbers, the window, the baseline, and what
   would confirm it.

Checks before you finish:
- The window and the comparison set are stated.
- Every number has its source and its time range.
- The request rate was checked before the latency was read.
- "Code change" is used only when a commit or an image is named.
- A deploy that changed no code is labelled a restart.

Example (invented, for illustration): After a deploy at 14:05, p95 latency for the checkout service rose from 240 ms (the
median of the same hours over four weeks) to 410 ms for 90 minutes. The request rate stayed within 10 percent of normal.
The release record lists one merged commit that touches the pricing cache. Verdict: regression, probable cause commit
9b1c3e7. An engineer confirms it by reading the commit against the latency trace.
