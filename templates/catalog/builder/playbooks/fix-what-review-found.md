# Fix what review found

Triggered when a task you own enters Fixes 1 or Fixes 2. The reviewer's Review page is listed under "From earlier
stages" in your wake and in `tico task show`, and its comments may also be on the pull request. Reviewers only comment; the fixes and the decisions are yours.

Outcome: each finding is acted on or answered with one line of reason, the checks pass at the new head, and the stage
has what it needs: your done.

---

## 1. Read every finding

    tico task show <id>
    gh pr view <n> -R <repo> --comments

`tico task show` lists each Review page under "From earlier stages" with its file id; read it with
`tico file read <file id>`. The pull request is listed there too. If the Review page this stage needs is not there
(the line says "none was attached") or you cannot read it, do not say done: send the task back for it,
`tico task move <id> "<Review stage>" -m "<what is missing>" --version N`.

## 2. Decide each one

Follow `skills/answer-each-review-comment/SKILL.md`. For each finding, act on it or reply on the pull request with one
line of reason. A blocking finding (a bug, a broken rule, or cost or data to undo) is never left without an answer.

## 3. Fix

Fix on the same branch. Write the test that would have caught the problem first, as in
`playbooks/build-a-dev-task.md` step 3. Never force-push over someone else's commit.

## 4. Check, then push once

The pull request is ready, so every push runs CI. Make all the fixes first, run the checks on your computer at the new
head as in `playbooks/build-a-dev-task.md` step 5, then push once. Record the head commit. A check that did not run is
"not checked".

## 5. Leave the stage

- **Fixes 1:** `tico task done <id> -m "<head sha>: fixed <list>; declined <list with one line each>" --version N`. The
  task moves to Review 2 once the checks pass at that head.
- **Fixes 2:** the same done. When Review 2 found nothing to fix, say done at once:
  `tico task done <id> -m "<head sha>: Review 2 found nothing to fix" --version N`. The task moves to QA once the checks
  pass at that head, and there you build the QA page (`playbooks/qa.md`).
