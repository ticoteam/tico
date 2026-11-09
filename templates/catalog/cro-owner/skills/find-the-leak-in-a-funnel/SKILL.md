---
name: find-the-leak-in-a-funnel
description: Name the step that loses the most people in a conversion funnel, measured against each step's own best recent rate, and refuse to name a leak until the tracking and noise checks pass.
---
# Find the leak in a funnel

When to use: before you name a step as the leak, propose a change for it, or put a leak in a headline.

Steps:
1. Name the export: its source, window, segment and the date it was pulled.
2. Count each step for that window and segment. A step with no count is "not measured".
3. Run the tracking checks: zero events on a day with traffic, a later step above an earlier one, or an event that changed
   on a release date. Any hit is a tracking question. Write it in the report before any leak.
4. Compute each rate: the people who did step B divided by the people who did step A, in the same window, segment and export.
5. For each step, take its best weekly rate over the last 13 weeks from the same definition. If the definition changed,
   use only the weeks that match it, and say so.
6. People lost = people entering the step this week × (best − this week's rate). Zero when this week is at or above the best.
7. Noise = 1.96 × sqrt(p × (1 − p) ÷ n), with p this week's rate and n its entering count. A leak is named only when the gap
   is larger than the noise. A smaller gap is listed as "within noise".
8. Rank by people lost. Segment leaks go on their own lines and are never added to the all-visitor total.
9. Write the top line: the step, its count and window, its rate against its best, the people lost, and whether it is named.

Checks before you finish:
- Every rate has its count and window.
- No leak is named while a tracking question is open.
- A step with no count reads "not measured", not zero.
- Segment leaks are not added to the all-visitor total.
- A gap inside the noise is listed as "within noise", not named.

Example (invented numbers, for illustration only): "Pricing → checkout started: 470 of 1,880 people, 2 to 8 Nov, export
pulled 9 Nov. 25.0% against 29.0% in its best of the last 13 weeks. Gap 4.0 points, noise 2.0. 75 people lost. Named."
