# Build a Dev ticket

Triggered when a Dev ticket is on your step In Progress and you are its owner. Budget as long as the change needs. A ticket
that has sat at In Progress for more than four hours gets a note to Head of Engineering on the ticket, not a silent wait.

Inputs: the ticket and its conversation (`hub task show <id>`), its acceptance tests if Head of Product wrote them,
`knowledge/standards.md` and `knowledge/patterns.md`.

Outcome: one pull request linked to the ticket, one head commit whose checks you ran and recorded, and the ticket moved to
Review 1. Every change gets Review 1; a change nobody will see skips In QA after its review, not the review.

Done when the change does what the ticket says, a test fails on the old code and passes on yours, the pull request
description says what changed, why and how it was tested, the ticket links the pull request, and the note on the ticket
names the head commit and the checks.

---

## 1. Read the ticket

    hub task show <id>

Read the spec, the acceptance tests and the last conversation. If the ticket is unclear, ask once with `hub task ask <id>`
and keep going on what is clear. Do not build a behaviour the spec does not state.

## 2. Branch

    git -C software/<repo> fetch origin
    git -C software/<repo> switch -c <ticket-number>-<short-name> origin/<base branch>

Work only on that branch. Never commit to the base branch and never force-push.

## 3. Test first

Follow `skills/write-a-test-that-proves-the-change/SKILL.md`. Write the test, run it, and see it fail for the reason the
ticket gives. Only then change the code.

## 4. Change

Make the smallest change that makes the test pass. A refactor you noticed on the way goes into a note for Head of
Engineering on the ticket, not into this pull request.

## 5. Check

Run the test commands in `knowledge/standards.md`. Record each command, its result and the head commit. A check you could
not run is "not checked", with the reason.

## 6. Open the pull request

    gh pr create -R <repo> --base <base branch> --title "<title>" --body "<what changed, why, how it was tested, what was not checked>"
    hub task link <id> <pull request url> --wake-once

## 7. Move the ticket

    hub task update <id> --step "Review 1" --note "PR <pull request url> at <head sha>: checks run <list>; not checked <list>"

Then wait for the reviewers. Their comments arrive as a fix step; follow `playbooks/fix-what-review-and-qa-found.md`.
