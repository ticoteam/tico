---
name: sort-a-failed-or-rolled-back-deploy
description: Sort each deploy run into succeeded, failed, cancelled by a person, or rolled back, from the run conclusion and the release record, and count each rollback once. Use it before counting a deploy as a success, a failure or a rollback.
---
# Sort a failed or rolled-back deploy

When to use: before any count of deploys, failed deploys, rollbacks or release cadence goes into a report.

Steps:
1. List the deploy runs in the window: `gh run list -R <repo> --workflow <deploy workflow> --limit 100 --json databaseId,headSha,conclusion,createdAt,updatedAt,event,headBranch`.
2. Read each run's conclusion:
   - `success` with a release it shipped: **succeeded**. Confirm it with the release record (`gh release view <tag>`), not with the
     run alone. A successful run that shipped no release is "ran, no release".
   - `failure`: **failed**. Read the failed step with `gh run view <id> -R <repo> --log-failed`. Name the step. A log past retention
     is "failed, cause unknown".
   - `cancelled`: check the log or the run's own record for who cancelled it. A cancel by a person is **cancelled by a person**,
     not a delivery failure. A cancel the workflow made itself is **failed**, with the reason.
3. Rollbacks: a rollback is a deploy of an earlier release after a later release reached users. Find it from the release record:
   the version deployed is older than the latest version that had reached users. Count it once, at the deploy that did it.
4. Time to recover: from the first bad deploy (the one that reached users and was later undone) to the first good deploy after it.
5. Release cadence: count the releases that reached users (succeeded deploys with a shipped release), not the tags. Give the
   days between the last two.
6. Never rerun a failed deploy to make the count look better. A rerun is a separate run, and the report counts the first
   conclusion and says so.

Checks before you finish:
- Every deploy run in the window is in exactly one class, or named as unknown.
- A rollback is not also counted as a new release.
- No person is named as the cause. The cause is the step, the run and the commit.

Example (generic): "Deploy runs in the window: 6. Succeeded 4, failed 1 (run 8866310, migration step), cancelled by a person 1.
Rollbacks 1: the release from 2026-10-27 was undone on 2026-10-27; time to recover 6 hours."
