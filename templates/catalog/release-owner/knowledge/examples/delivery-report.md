A sample of excellent output for a fictional team. Every name, number, run id and date here is invented.

```markdown
# Delivery report, Mon 2026-11-02 (team time, Pacific)

Sample output for Lantern Software, a fictional scheduling product. Nothing on GitHub was changed. Windows: the last 14
days (2026-10-19 to 2026-11-01) against the 14 before.

**Headline: a merged change reaches users in a median 2.9 days, down from 4.1; the merge queue waited a median 3.2 hours,
and the red gate `test-unit` stalled merges for 5 hours on 2026-10-24, its cause now with the Infrastructure Owner.**

## Merges and the path to users

| Measure | Last 14 days | Before | Basis |
|---|---|---|---|
| Merges to main | 58 | 61 | merged pull requests, mergedAt in window |
| Merge wait (median) | 3.2 hours | 3.9 hours | last required check to merge commit, queue timestamps not readable (see note) |
| Merge to production (median) | 2.9 days | 4.1 days | merge commit to first successful deploy run containing it |

- Note: the merge queue's own entry times are not readable with this bot's commands. The wait runs from the last required
  check's completion to the merge commit, and the number is therefore an upper bound on the queue's wait.
- Gate timings and failure figures are the Infrastructure Owner's, in its report of 2026-11-02. They are not repeated here.

## Gating checks

| Check | Gates merging | Handed to |
|---|---|---|
| `test-unit` | yes, required | Infrastructure Owner, runs 8870112, 8871004, 8871390 |
| `lint` | yes, required | none |
| `e2e-smoke` | no, not required | not a gate |

- Failure and flake figures for these checks are the Infrastructure Owner's, not recounted here.
- Flaky checks are handed over, not repaired. The hand-off task carries the three run ids and the test name from the logs.
- `e2e-smoke` is not required by the branch rule, so its failures did not stall a merge. It is listed for the owner to decide.

## Release cadence, failed deploys and rollbacks

- Releases that reached users: 3 in the window (v2.14.0 on 2026-10-21, v2.14.1 on 2026-10-27, v2.14.2 on 2026-10-30), against 2 before.
  Days between the last two: 3.
- Deploy runs: 6. Failed: 1 (run 8866310, 2026-10-24, failed at the migration step; no release reached users from it).
- Rollbacks: 1. v2.14.1 was rolled back to v2.14.0 on 2026-10-27 and v2.14.2 went out on 2026-10-30. Time to recover: 6 hours
  from the first bad deploy to the good one. Counted once, at the rollback deploy.
- Merges stalled: 1 stretch, 2026-10-24 afternoon to 2026-10-24 evening, 5 hours, all on `test-unit` red on main (run 8871004).
  The cause is the Infrastructure Owner's; see the hand-off below.

## Hand-offs

- Infrastructure Owner: the `test-unit` red run on main, with its run ids.
- QA Owner: the flaky test named in that log, with its runs and the test name. Task opened 2026-11-02.
- Release notes (this bot's own weekly draft): the rollback on 2026-10-27 and the cadence, so the notes record what reached users.
  No version decision is made here.
- Feature Owner: none this week.
- Owner, for decision: `e2e-smoke` is not required. Keep it out of the gate, or require it? The owner decides.

## Could not read

- Merge queue entry times: not readable with the commands this bot may run (see the note above).
- Logs for runs 8858001 and 8858002 are past retention. Counted as unknown, not as clean.

## Sources

- Merged pull requests, `gh pr list`, 2026-10-19 to 2026-11-01 and the 14 days before.
- Gate names: `knowledge/merge-gates.md` and `gh pr checks` on sampled merged pull requests, the same windows. Failure and flake figures: the Infrastructure Owner's report of 2026-11-02.
- Deploy runs and releases, `gh run list` for the deploy workflow and `gh release list`, the same windows.
- Thresholds: `knowledge/thresholds.md`, the team's own choices from setup, dated 2026-10-14.
```
