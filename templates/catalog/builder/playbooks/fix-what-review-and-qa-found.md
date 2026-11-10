# Fix what review and QA found

Triggered by review or QA comments on a pull request of a task you own. Reviewers and QA only comment or give a
verdict; the fixes and the decisions are yours.

Outcome: each comment and each failed test is acted on or answered with one line of reason, and the task is done with
the new head commit.

Done when every comment on the head has a reply, every failed QA test has a fix and a test that now covers it, the checks
ran on the new head, and the task note names the head and any comment you did not take, with its reason.

---

## 1. Read every comment

    gh pr view <n> -R <repo> --comments
    hub task show <id>

Read each reviewer comment and, for QA, the failing test and the head it judged.

## 2. Decide each one

Follow `skills/answer-each-review-comment/SKILL.md`. For each comment, act on it or reply on the pull request with one line
of reason. A blocking comment (a bug, a broken rule, or cost or data to undo) is never left without an answer.

## 3. Fix

Fix on the same branch. Write the test that would have caught the problem first, as in
`playbooks/build-a-dev-task.md` step 3. Never force-push over a reviewer's commit.

## 4. Check, then push once

The pull request is ready, so every push runs CI. Make all the fixes first, run the checks on your computer at the new
head as in `playbooks/build-a-dev-task.md` step 5, then push once with a normal push. Record the head commit. A check
that did not run is "not checked".

## 5. Finish

    hub task done <id> -m "<head sha>: fixed <list>; declined <list with one line each>; checks run <list>" --version N

Merge only under `skills/check-the-merge-rules-at-the-head/SKILL.md`, with the checks green at the head, and never before.
