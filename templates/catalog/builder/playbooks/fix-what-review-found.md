# Fix what review found

Triggered when a task you own enters Fixes. Read the Review page with both reviews under "From earlier stages"
in your wake or in `tico task show`, and check the comments on the pull request. On an older pipeline, follow the
same steps in Fixes 1 and Fixes 2 using the page from the preceding review stage.

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

Follow `.claude/skills/answer-each-review-comment/SKILL.md`. For each finding, act on it or reply on the pull request with one
line of reason. A blocking finding (a bug, a broken rule, or cost or data to undo) is never left without an answer.

## 3. Fix

Fix on the same branch. Write the test that would have caught the problem first, as in
`playbooks/build-a-dev-task.md` step 3. Never force-push over someone else's commit.

## 4. Check, then push once

The pull request is ready, so every push runs CI. Make all the fixes first, run the checks on your computer at the new
head as in `playbooks/build-a-dev-task.md` step 5, then push once. Record the head commit. A check that did not run is
"not checked". A check that fails because main is red: `.claude/skills/get-the-checks-green-at-the-head/SKILL.md`, "When main
is red" (one `Main red:` task, your task depends on it rather than snoozed, rebase and push once main is fixed,
never a re-run).

## 5. Leave the stage

`tico task done <id> -m "<head sha>: fixed <list>; declined <list with one line each>" --version N`. When there are no
findings, say done at once and record that result. Once the checks pass at that head, the task moves on. In the
default lane it moves to QA, where you build the QA page using `playbooks/qa.md`.
