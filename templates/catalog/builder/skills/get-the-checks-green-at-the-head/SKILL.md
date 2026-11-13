---
name: get-the-checks-green-at-the-head
description: When a check fails on a task's pull request, or before you hand a commit on, find what failed at the exact head commit, fix the cause, and never call a check passed that did not run there.
---
# Get the checks green at the head

When to use: whenever `tico task show` says "Checks failed", and before you say done or attach the QA page.

Steps:
1. Confirm the head commit: `gh pr view <n> -R <repo> --json headRefOid`. Only checks on this commit count. Earlier
   heads do not.
2. Read `gh pr checks <n> -R <repo>`. For each failing check, open its log (`gh run view <run id> -R <repo> --log-failed`)
   and find the first real error, not the last line.
3. Decide whose it is. Your change: fix it with a test, as in `playbooks/build-a-dev-task.md`. The base branch is red too
   (the same check fails on the base's newest commit): say so on the task, for the Engineering Manager; do not
   paper over it. A check that fails one run and passes the next on the same commit is flaky: name it on the task with
   both run links.
4. Never skip, disable or loosen a test or a check to make it pass. Never rerun to green and call it fixed.
5. Push the fix and record on the task the new head and the checks that ran on it.

{{app_name}} reads the checks itself and moves the task when they pass. You never merge: Merge is the Release
Manager's stage.
