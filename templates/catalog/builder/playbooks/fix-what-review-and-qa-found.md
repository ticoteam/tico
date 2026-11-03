# Fix what review and QA found

Triggered by a ticket moving to a fix step (Review 1 Fixes, Review 2 Fixes or QA Fixes) with you as its owner, or by a
verdict on your ticket. Reviewers and QA only comment or give a verdict; the fixes and the decisions are yours.

Outcome: each comment and each failed test is acted on or answered with one line of reason, and the ticket is moved to its
next step with the new head commit.

Done when every comment on the head has a reply, every failed QA test has a fix and a test that now covers it, the checks
ran on the new head, and the ticket note names the head and any comment you did not take, with its reason.

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
`playbooks/build-a-dev-ticket.md` step 3. Push with a normal push. Never force-push over a reviewer's commit.

## 4. Check

Run the checks at the new head and record the head commit. A check that did not run is "not checked".

## 5. Move on

- Review 1 Fixes done: move to Review 2 for a change that needs a second review; otherwise move to In QA.
- Review 2 Fixes done: move to In QA.
- QA Fixes done: move back to In QA, with the fix and the test that now covers the failing case.

    hub task update <id> --step "<step>" --note "<head sha>: fixed <list>; declined <list with one line each>; checks run <list>"

When QA passes, the ticket moves on to Pending Release. Merge then, under `skills/check-the-merge-rules-at-the-head/SKILL.md`,
and never before.
