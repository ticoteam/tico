---
name: write-a-measurable-key-result
description: Write each key result with a metric, a baseline and its source and date, a target, a due date, a proposed owner marked to confirm, and a committed or aspirational label.
---
# Write a measurable key result

When to use: when drafting key results for a new objective, or fixing one that cannot be graded.

Steps:
1. Name the metric in one line and the unit it is counted in. A key result is an outcome you can count, not a task.
2. Find the baseline in a dated source: `hub kpi show <kpi id>` or the goal's reading. Write it with its source and date.
   If there is no reading, write "no baseline" and the gap. Do not estimate one.
3. Set the target and the date it is due. A target with no date cannot be graded.
4. Propose an owner from the request and mark it "to confirm". If no owner is named, write "owner missing".
5. Label it committed (expect 1.0) when the team is sure it can be reached, or aspirational (expect 0.6 to 0.7) when it is a stretch.
6. Check that the result can be read on the due date from a source in Tico. If not, rewrite it, or name the source that will
   hold the reading.

Checks before you finish:
- Every key result has a metric, a baseline with its source and date or a marked gap, a target, a date, an owner marked to
  confirm, and a label.
- A missing baseline is "no baseline", never a guessed number.
- No key result is an activity.

Example (generic, invented): "Paying studios from 26 to 40 by 2026-12-18. Baseline 26 (KPI reading, 2026-08-29). Owner: sales,
to confirm. Aspirational." The same objective with "run two partner events" as its key result is rejected: that is an activity.
