---
name: find-the-biggest-drop-off
description: Read a funnel export by step and landing page, name the step that loses the most people in number, check it for segments and sample size, and say what the export cannot show.
---
# Find the biggest drop-off

When to use: each weekly conversion review, and whenever a page or step is said to be losing people.

Steps:
1. Take the funnel export for the last 28 days and the 28 days before, by landing page and step. Note the date range, the
   traffic type (all, paid, organic, direct), and whether the export counts visitors or sessions.
2. For each step in `knowledge/funnel.md`, read the people who reached it and the people who completed it. Lost is
   reached minus completed. Rate is completed over reached.
3. Name the biggest loss in people, not the lowest rate. A step where 40 people arrive and 4 finish (36 lost) can matter
   less than a step where 900 arrive and 450 finish (450 lost). Report both numbers.
4. Segment the biggest loss by device and by source before you call it a page problem. If one device or one source holds
   most of the loss, say that is where it sits.
5. Check the base. If fewer than 100 people reached the step in the window, say the rate can move by several points from
   chance alone, and do not compare it with last week as if it were firm.
6. Compare the same step with the 28 days before. A change is "moved" only when it is larger than the noise for that base.
   Otherwise it is "about the same".
7. Record the step, the people lost, the rate, the segment that holds most of the loss, the window, and the export's name.

Checks before you finish:
- The step names match `knowledge/funnel.md`. An export step with no name there is listed as unmapped, not guessed at.
- Visitors and sessions are never added together.
- A step missing from the export is "not measured", never zero.
- The rate is not called a conversion of the whole site unless the export covers the whole site.

Example (invented, for illustration): "Funnel export, 2026-10-14 to 2026-11-10, all traffic. Pricing to trial start: 1,240
reached, 310 completed, 930 lost (75 percent). Most of the loss is mobile (710 of 930). The base is large enough to read.
The 28 days before lost 76 percent, so this is about the same."
