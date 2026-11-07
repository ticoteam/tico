---
name: time-the-merge-to-release-path
description: Measure how long a merged change waits for the merge queue and how long it takes to reach users, as medians over a stated window from the timestamps the records hold. Use it whenever a report or task asks for a delivery time.
---
# Time the merge to release path

When to use: before you state a merge wait or a merge to production time, in a report or a task.

Steps:
1. Fix the window first: the last 14 days and the 14 days before them, unless `knowledge/thresholds.md` says otherwise. Name it.
2. Merge time: each merged pull request's `mergedAt` is the time it entered the default branch. Use `mergedAt`, not
   `mergeCommit`: the merge commit is an object in the JSON, not a timestamp.
3. Merge wait, labelled for what it measures. The queue's own entry time is not readable with the commands this bot may run
   (`gh pr view` does not expose it). So measure from the pull request's last required check passing (from `gh pr checks <n>` or
   `statusCheckRollup`) to its `mergedAt`, and label the figure "last required check to merge". It includes the queue wait and any
   human delay after the checks passed, so it is an upper bound on the queue wait, never the queue wait itself. Call it the queue wait
   only where the queue's entry time is actually read.
4. Merge to production: for each merge in the window, find the first successful deploy run that started after its `mergedAt`
   (`gh run list` on the deploy workflow, `conclusion` success) and the release published first after that merge
   (`gh release list`, `publishedAt`). The time is from `mergedAt` to that deploy's `updatedAt`. This assumes the release contains
   the merge: the commit cannot be checked against the tag without the compare view, so say that in the figure's label. A merge with no
   such deploy is "not yet in production", not a missing value.
5. Compare each median with the same measure over the window before, and write the change in hours or days, with its sign.
6. Record the timezone once, and the count of items each median rests on.

Checks before you finish:
- Each figure is a median over a named window with its count, not a single run.
- Each figure carries its label ("last required check to merge", "merged to deploy that reached users").
- Unreadable merges and deploy runs are counted and named, not dropped silently.

Example (generic): "Merge to production: median 2.9 days over 2026-10-19 to 2026-11-01 (58 merges), against 4.1 days over the
14 days before, labelled 'merged to deploy that reached users'. Last required check to merge: median 3.2 hours, an upper bound on the
queue wait, because the queue's entry times were not readable."
