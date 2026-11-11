# Build a task

Triggered when a task you own enters Build. Budget as long as the change needs; the stage's expected time is a day, and
past it the task is Late and the Engineering Pipeline Manager looks at it.

Inputs: the task and its conversation (`tico task show <id>`), the Spec page it links (`page:<file id>@<version>`,
listed under "From earlier stages"; read with `tico file read <file id>`), the person's notes on the spec under it in
the task's body (what they said with their OK: it answers the spec's open questions, so do not ask them again),
`knowledge/standards.md` and `knowledge/patterns.md`.

Outcome: one pull request linked to the task, opened as a draft and marked ready once, with the checks passing at its
head. Build leaves by itself when the pull request is open and the checks pass; it then goes to Review 1.

Done when the change does what the task says, a test fails on the old code and passes on yours, the pull request
description says what changed, why and how it was tested, and the task links the pull request.

---

## 1. Read the task

    tico task show <id>

Read the spec page, its acceptance criteria and the last conversation. If the task is unclear, ask once with `tico task ask <id> --to <person> "<question>" --version N`
and keep going on what is clear. Do not build a behaviour the spec does not state.

## 2. Branch and open a draft

    git -C software/<repo> fetch origin
    git -C software/<repo> switch -c <task-number>-<short-name> origin/<base branch>

If `<repo>` is `ticoteam/tico`, turn its pre-push hook on once for that clone (every worktree of the clone shares it):

    git -C software/<repo> config core.hooksPath .githooks

From then on each push runs `scripts/check.sh` and the branch conflict check first. A push it stops is a finding: fix it
and push again. If `check.sh` cannot run on this computer (a tool it needs is missing), say so in the note in step 7 and
push with `git push --no-verify`; the pull request's static checks run the same checks in CI. Do not change the hook
setting of any other repository.

Work only on that branch. Never commit to the base branch and never force-push. After the first commit, push and open the
pull request as a draft, then link it:

    git -C software/<repo> push -u origin HEAD
    gh pr create -R <repo> --draft --base <base branch> --title "<title>" --body "<what changes and why>"
    tico task link <id> <pull request url>

If {{app_name}} already opened a draft for the branch, `gh pr create` says so: edit that one with `gh pr edit`. A draft
gets no review request. Where the repository's CI skips drafts ({{app_name}}'s own does; `knowledge/standards.md` says
whether yours does), a draft runs no CI either, so push as often as you like while you work; otherwise each push runs CI,
so push less often. Keep it a draft until step 6.

## 3. Test first

Follow `skills/write-a-test-that-proves-the-change/SKILL.md`. Write the test, run it, and see it fail for the reason the
task gives. Only then change the code.

## 4. Change

Make the smallest change that makes the test pass. A refactor you noticed on the way goes into a note on the task,
for its requester, not into this pull request.

## 5. Check on your computer

Run the checks in `knowledge/standards.md` on your own computer, at the head you will hand in: the tests the pull
request's CI would select for your diff, not only the file you wrote. When the repository has a command for that, use it;
in {{app_name}}'s own repository it is `python3 scripts/pr_checks.py local`, which prints a receipt line with the head
commit. Record each command, its result and the head commit. A check you could not run is "not checked", with the reason.

## 6. Mark the pull request ready, once

Push the head you checked, write the description (what changed, why, how it was tested, what was not checked), and mark
it ready:

    gh pr edit <n> -R <repo> --body "<what changed, why, how it was tested, what was not checked>"
    gh pr ready <n> -R <repo>

In {{app_name}}'s own repository, `python3 scripts/pr_checks.py local --ready` runs step 5 and marks the pull request
ready after a pass. Every push to a ready pull request runs CI, so batch the fixes that follow.

## 7. Let the stage finish

There is nothing to say done in Build. {{app_name}} reads the checks from GitHub and moves the task to Review 1 once
they pass at the head; a failing check wakes you with its names (`skills/get-the-checks-green-at-the-head/SKILL.md`).
Leave a note on the task with the head commit, the checks you ran and the ones you could not:

    tico task comment <id> "PR <pull request url> at <head sha>: checks run <list>; not checked <list>"

Review findings come back in Fixes 1 and Fixes 2: `playbooks/fix-what-review-found.md`.
