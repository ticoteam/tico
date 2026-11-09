# Delivery practice

What each measure in the delivery report means, how it is taken, and over which window. Read it before you
measure anything. The numbers come from the runs and the merges; this file says what they mean.

## The path from merge to users

- **Merge wait.** From a pull request's last required check passing (or its entry into the merge queue, where that time can
  be read) to its `mergedAt` on the default branch. The queue's entry time is not exposed to this bot, so the figure is labelled
  "last required check to merge" and is an upper bound on the queue wait. Where the queue's own timestamps cannot be read
  with the commands this bot may run, measure from the last gating check's completion on the head commit to its `mergedAt`,
  and say so in the report.
- **Merge to production.** From a merge's `mergedAt` on the default branch to the first deploy run that reached users for a
  release containing that commit. A commit is in production when a successful deploy run for a release that includes it
  has concluded.
- **Merges stalled.** Hours the merge queue stood still because a gating check was red on the default branch. Measured from
  the first red run on the default branch to its next green run. The Infrastructure Owner owns the cause of a red check;
  this measure only counts the stall it caused.

## Cadence, failures and rollbacks

- **Release cadence.** The number of releases that reached users in the window, counted from the deploy record, not from the
  number of tags. Report the count, and the days between the last two releases that reached users.
- **Failed deploy.** A deploy run that concluded with a failure before it reached users. A deploy a person cancelled is
  reported as cancelled, not as a failure of the delivery path.
- **Rollback.** A deploy of an earlier release after a later release reached users, found in the deploy record. It is counted
  once, at the deploy that made it. Time to recover runs from the first bad deploy to the first good one.

## Gating checks

- A gating check is one the branch rule or the merge queue requires. Name them from `knowledge/merge-gates.md` (written at
  setup) and from the checks on the pull requests (`gh pr checks`). A check that is not required is not a gate, and is never
  called one.
- A check is flaky when it fails and then passes on the same commit with no new commit between the two runs. Flaky checks
  are handed with their run ids to the QA Owner when the flake is in a test, and to the Infrastructure Owner when it is in a
  runner, cache or workflow. This bot keeps the gate list, its names and hand-offs, not the failure or flake counts.

## Measuring

- Medians over stated windows: the last 14 days and the 14 days before them, unless `knowledge/thresholds.md` says otherwise.
- Count what can be read. A run past log retention is unknown, not clean. A run with no conclusion is unknown.
- Times are stated in team time, with the zone named once in each report.

## Sources

- 2026-11-07: template definitions, written from the card at build time. Not yet checked against a team's own runs; setup
  checks each definition against the team's last two weeks of merges and deploys.
