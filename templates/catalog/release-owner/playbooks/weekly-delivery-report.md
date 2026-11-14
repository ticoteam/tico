# Weekly delivery report

Schedule: Mondays at 09:30 team time (routine `weekly-delivery-report`), after setup. Also run by hand. Budget 45 minutes.
Nothing in the product repositories changes in this run.

---

## Inputs

- `knowledge/thresholds.md`, `knowledge/delivery-practice.md`, `knowledge/merge-gates.md` (with its open and recent stalls), and last week's report.
- Merged pull requests for the last 14 days and the 14 before, with their merge times and check results:
  `gh pr list -R <repo> --state merged --search "merged:>YYYY-MM-DD" --json number,mergedAt,mergeCommit,headRefOid,statusCheckRollup,url`.
- Workflow runs for the deploy workflow, for the same two windows:
  `gh run list -R <repo> --workflow <name> --limit 100 --json databaseId,headSha,conclusion,createdAt,updatedAt,event,headBranch`.
- Releases: `gh release list -R <repo> --limit 30 --json tagName,publishedAt,isDraft,isPrerelease`, and `gh release view <tag> -R <repo>` for each one in the window.
- Logs for failed deploy runs, and for a red gate that stalled a merge: `gh run view <id> -R <repo> --log-failed`. A log past
  retention is unknown, not clean.

## Steps

1. **Read where things stand.** `tico task show <id>`, then the inputs above. For each hand-off in last week's report,
   record whether it was taken up, still open, or dropped.
2. **Measure the path to users.** Follow `skills/time-the-merge-to-release-path/SKILL.md`: merge wait and merge to production,
   each as a median over the window, with the window before.
3. **Name the gating checks.** Follow `skills/name-the-checks-that-gate-merging/SKILL.md`: which checks gate merging, and the
   seat each one's failures are handed to. Cite the Infrastructure Owner's failure figures and the QA Owner's flake figures; do not recount them.
4. **Sort the deploys and releases.** Follow `skills/sort-a-failed-or-rolled-back-deploy/SKILL.md`: each deploy run as succeeded,
   failed, cancelled by a person or rolled back. Count each rollback once, and the release cadence from the deploy record.
5. **Hand each finding to its seat.** One task per hand-off, with the evidence in its body:
   - failing gating checks to the Infrastructure Owner, and flaky tests to the QA Owner, with the run ids and the check name;
   - delivery timings that span repositories to the Feature Owner;
   - a real product bug a failed deploy exposed to issue triage, with the deploy's run id.
   The release record stays in this report: it is the source for the next release notes. A rollback goes to the person who runs
   deploys (step 6). If a seat does not exist on the team, the hand-off goes to the owner as a task.
6. **Draft a rollback request if one is open.** When the last deploy that reached users is bad and no good release has followed,
   follow `playbooks/rollback.md`: a drafted request for the person who runs deploys, on a task. Never run it.
7. **Write and hand over.** Write the report, `tico file publish` it, commit this repository, then close the task with the headline and the path.

## Output

`reports/YYYY-MM-DD-delivery.md`, in this order, in the shape of `knowledge/examples/delivery-report.md`:

- **Headline:** the median merge to production time with its change, and the merge queue wait in one clause.
- **Path to users:** merge wait and merge to production, each with the window before and the label it was measured under.
- **Gating checks:** each gate by name, whether it is required, and the seat its failures are handed to. The Infrastructure Owner's figures are cited, not recounted.
- **Releases and deploys:** releases that reached users, the days between the last two, failed deploys, rollbacks and time to recover.
- **Stalls:** each stretch the merge queue stood still because a gating check was red on the default branch (from `knowledge/merge-gates.md`).
- **Hand-offs:** each task opened, with its seat and what it asks.
- **Could not read:** runs, logs, queue timings or releases unavailable, with the count.
- **Sources:** the windows, the repository, the workflows, and the date of the thresholds file.

## Done when

- Each measure in the output names its window, its median and its label, or is named as unreadable.
- Each failing or flaky gate handed over names its runs (id, commit, date), and its hand-off is a task.
- Each rollback is counted once, with its time to recover.
- The report is published and the task is closed, with the headline and the report path in the note.
