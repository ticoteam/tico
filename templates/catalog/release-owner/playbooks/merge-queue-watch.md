# Daily merge queue watch

Schedule: every morning at 09:00 team time (routine `daily-merge-queue-watch`). Also run by hand. Budget 10 minutes. This run
usually writes nothing. It writes a task only when a gating check has stalled the merge queue past a threshold. Nothing in the
product repositories changes.

---

## 1. Read what is open

Read `knowledge/merge-gates.md`, including its `## Open stalls`, and the thresholds in `knowledge/thresholds.md`
(the stall rule and the slow-gate rule).

## 2. Read the default branch

    gh run list -R <repo> --branch main --limit 50 --json databaseId,headSha,conclusion,createdAt,updatedAt,workflowName,event

Keep the runs of the gating checks named in `knowledge/merge-gates.md`. Ignore runs of other workflows.

## 3. Find stalls and slow gates

- **Stall:** a gating check whose latest run on the default branch is red, with no green run of the same check since. Its start is the
  first red run after the last green one.
- **Slow gate:** a gating check whose run took longer than the slow-gate threshold from start to finish (`updatedAt` minus `createdAt`).
  One slow run is a fact to note; the same check slow on three days running is a stall in practice.

## 4. Update the open stalls

In `knowledge/merge-gates.md` under `## Open stalls`, one line per open stall: the check, the repository, the first red run's id and time, and the
date it was last seen red. When a stalled check has been green since, move its line to `## Recent stalls` with the end time, so the
weekly report can count it. Change nothing else in that file's stall sections.

## 5. Hand a stall over once

A stall that has lasted more than a working day, or a slow gate three days running, gets one task for the Infrastructure Owner, with the
run ids, the check name and the times. Do not open a second task for the same stall on a later day; the existing task carries it.
The cause is the Infrastructure Owner's to find. Never name a person for it.

## 6. Finish

If nothing is stalled and no gate was slow, write nothing and close the run quietly. Add a line to `memory/learnings.md` only when
the run taught something new about the checks.
