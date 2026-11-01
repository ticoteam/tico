# Review a pull request

Triggered by a task naming one pull request, and used for each pull request in `playbooks/weekday-review-queue.md`. Budget
15 minutes.

Inputs: the pull request's description, its linked issue, its diff, its checks, `knowledge/standards.md`, `knowledge/patterns.md`,
and `knowledge/review-practice.md`.

Outcome: one draft review on the task, at one head commit, that a human can post with one edit. Its first line is a summary and
whether anything blocks the merge. It names what was read and what was not.

Done when every changed file was read or named as skipped, each risky surface has its evidence or a blocking question, every
comment has a label, a location and a quote, the head commit is written on the draft, and the task note carries the blocking count.

---

## 1. Read the change in order

    gh pr view <n> -R <repo> --comments
    gh pr diff <n> -R <repo>
    gh pr checks <n> -R <repo>

1. The description and the linked issue: what the change is for, in the issue's words. If the description says nothing, that is
   the first finding.
2. The risk map: follow `skills/map-the-risk-in-a-diff/SKILL.md`. Mark the risky surfaces before you read any line.
3. The design: should this change exist, and does it fit the code around it? Ask this before any line.
4. The callers of each changed function, the tests, and whether a test would fail on the old code (follow
   `skills/check-that-a-test-proves-the-change/SKILL.md`).
5. The details: naming, comments, error handling, complexity, a migration or a config change, a new dependency.
6. The other open changes on the same files, and the base branch's commits since the branch point.
7. Checks: what passed, what failed, what is pending, and whether a failure is this change's. A check that did not run is "not
   checked", not green.

## 2. Check against the team's standard

Walk `knowledge/standards.md` item by item. Note each item as met, not met, or not checkable from the diff. Look for a pattern from
`knowledge/patterns.md`. A secret, a removed check or a removed test is blocking, and is reported to a human at once by task.

## 3. Size

If the change is over the team's line, or mixes a refactor with a feature, draft one polite request to split it, naming a
suggested seam. Review what you can regardless.

## 4. Draft the review

On the task, in the comment style chosen during setup (default labelled):
- **Summary**, two lines: what the change does, the head commit read, and whether anything blocks it.
- `issue (blocking):` each thing that ships a bug, breaks a stated rule, or costs money or data to undo. File, line, quoted code,
  what happens, and what would resolve it.
- `issue:` a real problem that can wait for a later change.
- `question:` what you could not tell from the diff.
- `suggestion:` an improvement with a short example.
- `nit:` preferences, at most five, never blocking.
- `praise:` one specific line, when earned.
- **Not checked:** tests you did not run, files skipped, pending checks.

Every comment is polite, about the code, and specific. Never "this is wrong": say what happens and why. Follow
`skills/write-a-review-comment-the-author-can-act-on/SKILL.md`.

## 5. Finish

You post nothing. Commit, then `hub task update <id> --status done --note`: the pull request, the head commit, the number of
blocking issues, and what you did not check. Post requested comments with the exact text and pull request using your Tools when a
person has turned mail sending on in Tico; otherwise keep the draft.

## When you cannot tell

Say so as a `question:`. A guess written as a finding wastes an author's day. When the head moves after your review, read the new
commits before you change the verdict, and say which commit the verdict now covers.
