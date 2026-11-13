# Build a task

Triggered when a task you own enters Build. Budget as long as the change needs; the stage's expected time is a day, and
past it the task is Late and the Engineering Manager looks at it.

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

## 2. Branch, set up and open a draft

    git -C software/<repo> fetch origin
    git -C software/<repo> switch -c tico/<first 8 characters of the task id>-<short-name> origin/<base branch>

The branch name has the shape {{app_name}}'s task worktrees use. If the repository has a setup script
(`scripts/dev_setup.sh` by convention; its README or CONTRIBUTING file says), run it once in this clone before the first
push, and again after a pull that changes a lock file: it installs what the repository's checks and pre-push hook need.
If the repository asks for a pre-push hook and its setup script does not turn it on, turn it on as the repository says.

A push the hook stops is a finding: fix it and push again. Never skip the hook. If it cannot run on this computer (a
tool it needs is missing and the setup script cannot install it), say on the task which tool, and do not push around it.

One task has one pull request at a time. When the task already has an open one (`tico task show` lists it: the task
was someone else's before you), work on its branch instead: `gh pr checkout <its link>`, commit and push there, and
never open a second pull request beside it.

Work only on that branch. Never commit to the base branch. Never force-push, with one exception: after a rebase on the
base branch (a merge conflict wakes you for one), push your own branch with `git push --force-with-lease`. After the
first commit, push. {{app_name}} opens the draft pull request for a `tico/<task>-` branch at its first push and links
it to the task within a few minutes; give it your title and body with `gh pr edit`:

    git -C software/<repo> push -u origin HEAD
    gh pr list -R <repo> --head <branch>
    gh pr edit <n> -R <repo> --title "<title>" --body "<what changes and why>"

Never run `gh pr create` on a task branch: racing {{app_name}}'s draft makes two pull requests on one branch. Only when
no draft shows up ten minutes after the push (the repository is not connected to {{app_name}}), open one with
`gh pr create -R <repo> --draft` and link it with `tico task link <id> <pull request url>`. A draft
gets no review request. Where the repository's CI skips drafts ({{app_name}}'s own does; `knowledge/standards.md` says
whether yours does), a draft runs no CI either, so push as often as you like while you work; otherwise each push runs CI,
so push less often. Keep it a draft until step 6.

## 3. Test first

Follow `skills/write-a-test-that-proves-the-change/SKILL.md`. Write the test, run it, and see it fail for the reason the
task gives. Only then change the code.

## 4. Change

Make the smallest change that makes the test pass. A refactor you noticed on the way goes into a note on the task,
for its requester, not into this pull request. If the change fixes a failure the repository's tests list as known (an
exception or baseline entry), delete that entry in this pull request: a stale entry fails the merge queue.

## 5. Check on your computer

Run only the tests your change can break, at the head you will hand in: the test files for the code you changed and
their nearest neighbours, one file at a time, with the commands in `knowledge/standards.md`. Never the whole suite or a
full local CI run on this computer: the pull request's checks run the rest once it is ready. Record each command, its
result and the head commit. A check you could not run is "not checked", with the reason.

## 6. Mark the pull request ready, once

Push the head you checked, write the description (what changed, why, how it was tested, what was not checked), and mark
it ready:

    gh pr edit <n> -R <repo> --body "<what changed, why, how it was tested, what was not checked>"
    gh pr ready <n> -R <repo>

Every push to a ready pull request runs CI, so batch the fixes that follow.

## 7. Let the stage finish

There is nothing to say done in Build. {{app_name}} reads the checks from GitHub and moves the task to Review 1 once
they pass at the head; a failing check wakes you with its names (`skills/get-the-checks-green-at-the-head/SKILL.md`).
When the same check fails on main too, follow that skill's "When main is red": relate your task to the one open
`Main red:` task (file it only when none is open), stay blocked on it rather than snoozed, and once main is fixed
rebase on it and push; never re-run the failed jobs.
Leave a note on the task with the head commit, the checks you ran and the ones you could not:

    tico task comment <id> "PR <pull request url> at <head sha>: checks run <list>; not checked <list>"

Review findings come back in Fixes 1 and Fixes 2: `playbooks/fix-what-review-found.md`. Then QA: `playbooks/qa.md`.
