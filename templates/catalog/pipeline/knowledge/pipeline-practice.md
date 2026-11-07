# Pipeline practice

What each measure in the delivery pipeline report means, how it is taken, and over which window. Read it before you
measure anything. The numbers come from the runs and the merges; this file says what they mean.

## The path from merge to users

- **Merge wait.** From a pull request becoming ready to merge (its last required check passing, or its entry into the merge
  queue where the team has one) to its merge commit on the default branch. Where the queue's own timestamps cannot be read
  with the commands this bot may run, measure from the last gating check's completion on the head commit to the merge commit,
  and say so in the report.
- **Merge to production.** From a merge commit on the default branch to the first deploy run that reached users for a
  release containing that commit. A commit is in production when a successful deploy run for a release that includes it
  has concluded.
- **Merges stalled.** Hours the merge queue stood still because a gating check was red on the default branch. Measured from
  the first red run on the default branch to its next green run. The DevOps Engineer owns the cause of a red check;
  this measure only counts the stall it caused.

## Cadence, failures and rollbacks

- **Release cadence.** The number of releases that reached users in the window, counted from the deploy record, not from the
  number of tags. Report the count, and the days between the last two releases that reached users.
- **Failed deploy.** A deploy run that concluded with a failure before it reached users. A deploy a person cancelled is
  reported as cancelled, not as a failure of the pipeline.
- **Rollback.** A deploy of an earlier release after a later release reached users, found in the deploy record. It is counted
  once, at the deploy that made it. Time to recover runs from the first bad deploy to the first good one.

## Gating checks

- A gating check is one the branch rule or the merge queue requires. Name them from `knowledge/merge-gates.md` (written at
  setup) and from the checks on the pull requests (`gh pr checks`). A check that is not required is not a gate, and is never
  called one.
- A check is flaky when it fails and then passes on the same commit with no new commit between the two runs. Flaky checks
  are handed to the DevOps Engineer with their run ids. This bot keeps the gate list, its names and hand-offs, not the failure
  or flake counts.

## Measuring

- Medians over stated windows: the last 14 days and the 14 days before them, unless `knowledge/thresholds.md` says otherwise.
- Count what can be read. A run past log retention is unknown, not clean. A run with no conclusion is unknown.
- Times are stated in team time, with the zone named once in each report.

## Sources

- 2026-11-07: template definitions, written from the card at build time. Not yet checked against a team's own runs; setup
  checks each definition against the team's last two weeks of merges and deploys.
