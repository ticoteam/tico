# Build a Dev ticket

Triggered when a Dev ticket is on your step In Progress and you are its owner. Budget as long as the change needs. A ticket
that has sat at In Progress for more than four hours gets a note to Head of Engineering on the ticket, not a silent wait.

Inputs: the ticket and its conversation (`hub task show <id>`), its acceptance tests if Head of Product wrote them,
`knowledge/standards.md` and `knowledge/patterns.md`.

Outcome: one pull request linked to the ticket, opened as a draft and marked ready once, one head commit whose checks you
ran and recorded, and the ticket moved to Review 1. Every change gets Review 1; a change nobody will see skips In QA after
its review (on a board with In QA), not the review.

Done when the change does what the ticket says, a test fails on the old code and passes on yours, the pull request
description says what changed, why and how it was tested, the ticket links the pull request, and the note on the ticket
names the head commit and the checks.

---

## 1. Read the ticket

    hub task show <id>

Read the spec, the acceptance tests and the last conversation. If the ticket is unclear, ask once with `hub task question "<question>" --on <person> --parent <id>`
and keep going on what is clear. Do not build a behaviour the spec does not state.

## 2. Branch and open a draft

    git -C software/<repo> fetch origin
    git -C software/<repo> switch -c <ticket-number>-<short-name> origin/<base branch>

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
    hub task link <id> <pull request url> --wake-once

If {{app_name}} already opened a draft for the branch, `gh pr create` says so: edit that one with `gh pr edit`. A draft
gets no review request. Where the repository's CI skips drafts ({{app_name}}'s own does; `knowledge/standards.md` says
whether yours does), a draft runs no CI either, so push as often as you like while you work; otherwise each push runs CI,
so push less often. Keep it a draft until step 6.

## 3. Test first

Follow `skills/write-a-test-that-proves-the-change/SKILL.md`. Write the test, run it, and see it fail for the reason the
ticket gives. Only then change the code.

## 4. Change

Make the smallest change that makes the test pass. A refactor you noticed on the way goes into a note for Head of
Engineering on the ticket, not into this pull request.

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

## 7. Move the ticket

    hub task update <id> --step "Review 1" --note "PR <pull request url> at <head sha>: checks run <list>; not checked <list>"

Paste the receipt line from step 5 into the note when you have one. Then wait for the reviewers. Their comments arrive as
a fix step; follow `playbooks/fix-what-review-and-qa-found.md`.
