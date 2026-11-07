---
name: time-the-merge-to-release-path
description: Measure how long a merged change waits for the merge queue and how long it takes to reach users, as medians over a stated window from the timestamps the records hold. Use it whenever a report or task asks for a delivery time.
---
# Time the merge to release path

When to use: before you state a merge wait or a merge to production time, in a report or a task.

Steps:
1. Fix the window first: the last 14 days and the 14 days before them, unless `knowledge/thresholds.md` says otherwise. Name it.
2. Merge wait: for each merged pull request in the window, take the time its last required check passed (from `gh pr checks <n>`
   or `statusCheckRollup`) to its `mergeCommit` time. If the queue's own entry time cannot be read with the allowed commands,
   say so, and label the number as the upper bound it is.
3. Merge to production: for each merge commit, find the first successful deploy run for a release that contains the commit
   (the release's tag points at or after the commit). The time is from the merge commit's time to that deploy's
   `updatedAt`. A merge with no such deploy is "not yet in production", not a missing value.
4. Compare each median with the same measure over the window before, and write the change in hours or days, with its sign.
5. Record the timezone once, and the count of items each median rests on.

Checks before you finish:
- Each figure is a median over a named window with its count, not a single run.
- Unreadable merges and deploy runs are counted and named, not dropped silently.
- The queue wait is labelled as an upper bound where it is one.

Example (generic): "Merge to production: median 2.9 days over 2026-10-19 to 2026-11-01 (58 merges), against 4.1 days over the
14 days before. Merge wait: median 3.2 hours, an upper bound, because the queue's entry times were not readable."
